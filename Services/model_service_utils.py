"""
模型服务工具模块 - 提供特征提取、数据预处理、模型定义等功能
被 tlc_server.py、cc_server.py、hplc_server.py 共用

主要功能：
1. 从 SMILES 字符串提取分子描述符
2. 计算溶剂特征
3. 为 TLC/CC/HPLC 服务生成模型输入特征向量

包含的模型定义：
1. TLCANN       - TLC Rf 值预测的前馈神经网络
2. CCNeuralNet   - 柱色谱分离预测的前馈神经网络
3. GINGraphPooling - HPLC 保留时间预测的图神经网络 (GeoGNN)
"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F

import math
import re
import os
from pathlib import Path
from typing import Iterable, List, Sequence

import numpy as np
from rdkit import Chem
from rdkit.Chem import AllChem, Descriptors, MACCSkeys, rdMolDescriptors


# ==================== 文件路径配置 ====================
# 当前目录（Services/）
SERVICES_DIR = Path(__file__).resolve().parent

# TLC 模型文件路径
TLC_MODEL_PATH = SERVICES_DIR / "best_tlc_model.joblib"    # scikit-learn 格式
TLC_TORCH_MODEL_PATH = SERVICES_DIR / "best_tlc_model.pth"  # PyTorch 格式

# CC 模型文件路径
CC_MODEL_PATH = SERVICES_DIR / "best_cc_joblib_model.joblib"      # 旧 scikit-learn 格式，仅兼容保留
CC_TORCH_MODEL_PATH = SERVICES_DIR / "best_cc_model.pth"          # CC GeoGNN 服务默认权重
CC_GEOGNN_MODEL_PATH = CC_TORCH_MODEL_PATH

# HPLC 模型和数据文件路径
HPLC_MODEL_PATH = SERVICES_DIR / "best_hplc_model.pth"          # PyTorch 格式
HPLC_COLUMN_DESCRIPTOR_PATH = SERVICES_DIR / "hplc_column_descriptor.npy"  # 色谱柱描述符

# 五种常见溶剂的 SMILES（用于计算溶剂描述符）
SOLVENT_SMILES = ["CCCCCC", "CC(OCC)=O", "C(Cl)Cl", "CO", "CCOCC"]
# 对应顺序：己烷(Hexane)、乙酸乙酯(EtOAc)、二氯甲烷(DCM)、甲醇(MeOH)、乙醚(Et2O)


# ==================== 文件检查工具 ====================
def require_file(path: Path, label: str) -> None:
    """
    检查所需文件是否存在，不存在则抛出异常

    Args:
        path: 文件路径
        label: 文件描述（用于错误信息）
    """
    if not path.exists():
        raise FileNotFoundError(
            f"{label} 文件缺失: {path}。请先将训练好的模型放入 Services 目录。"
        )


# ==================== 通用辅助函数 ====================
def safe_index(alist, elem):
    """
    安全索引查找：元素不存在时返回列表最后一个索引

    用于处理 OOV（Out-Of-Vocabulary）情况
    """
    try:
        return alist.index(elem)
    except ValueError:
        return len(alist) - 1


def rdchem_enum_to_list(values):
    """将 RDKit 枚举类型转换为值列表"""
    return [values[i] for i in range(len(values))]


def check_smiles_validity(smiles: str) -> bool:
    """检查 SMILES 字符串是否能被 RDKit 解析"""
    try:
        m = Chem.MolFromSmiles(smiles)
        return m is not None
    except Exception:
        return False


# =============================================================================
# ==================== TLC / CC 模型定义 ======================================
# =============================================================================

class TLCANN(nn.Module):
    """
    TLC Rf 值预测的 ANN 模型

    网络结构：
    - 输入层 → 隐藏层(128) → 隐藏层(128) → 输出层(1)
    - 激活函数: LeakyReLU（隐藏层）、Sigmoid（输出层）
    - Sigmoid 确保输出在 [0, 1] 范围内，符合 Rf 值的物理意义

    Args:
        in_features: 输入特征维度
        hidden_features: 隐藏层神经元数（默认128）
        out_features: 输出维度（默认1，即 Rf 值）
    """

    def __init__(self, in_features: int, hidden_features: int = 128, out_features: int = 1):
        super().__init__()
        self.input_layer = nn.Linear(in_features, hidden_features)     # 输入层
        self.hidden_layer = nn.Linear(hidden_features, hidden_features) # 隐藏层
        self.output_layer = nn.Linear(hidden_features, out_features)   # 输出层

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        前向传播

        使用 4 层 LeakyReLU 激活的隐藏层 + 1 层 Sigmoid 输出层
        这样的深层结构能更好地捕捉分子结构与 Rf 值的非线性关系

        Args:
            x: 输入特征张量

        Returns:
            预测的 Rf 值（[0, 1] 范围）
        """
        x = F.leaky_relu(self.input_layer(x))         # 输入 → 隐藏层1
        x = F.leaky_relu(self.hidden_layer(x))        # 隐藏层1 → 隐藏层2
        x = F.leaky_relu(self.hidden_layer(x))        # 隐藏层2 → 隐藏层3
        x = F.leaky_relu(self.hidden_layer(x))        # 隐藏层3 → 隐藏层4
        return torch.sigmoid(self.output_layer(x))    # Sigmoid → 输出 [0,1]


class CCNeuralNet(nn.Module):
    """
    柱色谱(CC)分离预测的神经网络模型

    预测两个化合物在柱色谱中的保留时间（Rt1 和 Rt2）

    网络结构：
    - 输入层 → 隐藏层(256) → 隐藏层(256) → 输出层(2)
    - 激活函数: LeakyReLU（隐藏层）、Softplus（输出层）
    - Softplus 确保输出为正数，符合保留时间的物理意义

    Args:
        in_features: 输入特征维度
        hidden_features: 隐藏层神经元数（默认256）
        out_features: 输出维度（默认2，即两个化合物的保留时间）
    """

    def __init__(self, in_features: int, hidden_features: int = 256, out_features: int = 2):
        super().__init__()
        self.input_layer = nn.Linear(in_features, hidden_features)      # 输入层
        self.hidden_layer = nn.Linear(hidden_features, hidden_features)  # 隐藏层
        self.output_layer = nn.Linear(hidden_features, out_features)    # 输出层

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        前向传播

        使用 4 层 LeakyReLU 隐藏层 + Softplus 输出层
        Softplus 函数 (ln(1+e^x)) 确保保留时间始终为正数

        Args:
            x: 输入特征张量（两个化合物的联合特征）

        Returns:
            两个化合物的预测保留时间 [Rt1, Rt2]
        """
        x = F.leaky_relu(self.input_layer(x))         # 输入 → 隐藏层1
        x = F.leaky_relu(self.hidden_layer(x))        # 隐藏层1 → 隐藏层2
        x = F.leaky_relu(self.hidden_layer(x))        # 隐藏层2 → 隐藏层3
        x = F.leaky_relu(self.hidden_layer(x))        # 隐藏层3 → 隐藏层4
        return F.softplus(self.output_layer(x))       # Softplus → 正数输出


# ==================== 比例解析工具 ====================
def parse_ratio_pair(value: str) -> tuple[float, float]:
    """
    解析洗脱剂比例字符串，如 "40/10" → (40.0, 10.0)

    Args:
        value: 比例字符串，如 "90/10"、"40/10"

    Returns:
        两个浮点数 (溶剂A比例, 溶剂B比例)
    """
    numbers = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", value or "")]
    if len(numbers) >= 2:
        return numbers[-2], numbers[-1]
    if len(numbers) == 1:
        return numbers[0], 1.0
    return 1.0, 1.0


def normalize_eluent_ratios(ratios: Sequence[float], length: int = 5) -> np.ndarray:
    """
    归一化洗脱剂比例数组到指定长度

    Args:
        ratios: 原始比例列表
        length: 输出数组长度（默认5种溶剂）

    Returns:
        归一化后的 numpy 数组（各元素之和为1）
    """
    arr = np.zeros(length, dtype=np.float32)
    for idx, value in enumerate(ratios[:length]):
        arr[idx] = max(float(value), 0.0)
    total = float(arr.sum())
    if total > 0:
        arr /= total  # 归一化，使总和为1
    return arr


# ==================== 分子特征提取 ====================
def mol_from_smiles(smiles: str):
    """
    从 SMILES 字符串创建 RDKit 分子对象

    Args:
        smiles: SMILES 字符串

    Returns:
        RDKit Mol 对象

    Raises:
        ValueError: SMILES 无效时
    """
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError(f"无效的 SMILES: {smiles}")
    return mol


def basic_descriptors(smiles: str) -> np.ndarray:
    """
    计算分子的基本理化性质描述符

    包括：分子量、拓扑极性表面积(TPSA)、可旋转键数、
    氢键供体数、氢键受体数、脂水分配系数(LogP)

    Args:
        smiles: SMILES 字符串

    Returns:
        6个基本描述符的数组
    """
    mol = mol_from_smiles(smiles)
    return np.array(
        [
            Descriptors.ExactMolWt(mol),                    # 精确分子量
            rdMolDescriptors.CalcTPSA(mol),                  # 拓扑极性表面积
            Descriptors.NumRotatableBonds(mol),              # 可旋转键数
            Descriptors.NumHDonors(mol),                     # 氢键供体数
            Descriptors.NumHAcceptors(mol),                  # 氢键受体数
            Descriptors.MolLogP(mol),                        # 脂水分配系数
        ],
        dtype=np.float32,
    )


def maccs(smiles: str) -> np.ndarray:
    """
    计算 MACCS 结构键指纹（166位）

    MACCS 是 MDL 公司开发的分子结构键描述符，
    用于表示分子中是否存在特定的结构片段

    Args:
        smiles: SMILES 字符串

    Returns:
        167维的 MACCS 指纹数组
    """
    mol = mol_from_smiles(smiles)
    fp = MACCSkeys.GenMACCSKeys(mol)
    return np.array([int(x) for x in fp], dtype=np.float32)


def morgan(smiles: str, n_bits: int = 2048) -> np.ndarray:
    """
    计算 Morgan（ECFP）圆形指纹

    一种基于原子环境的圆形指纹，常用于分子相似性比较

    Args:
        smiles: SMILES 字符串
        n_bits: 指纹位长（默认2048位）

    Returns:
        n_bits 长度的二进制指纹数组
    """
    mol = mol_from_smiles(smiles)
    fp = AllChem.GetMorganFingerprintAsBitVect(mol, 2, nBits=n_bits)
    return np.array(fp, dtype=np.float32)


# ==================== 溶剂特征计算 ====================
def solvent_descriptor_vector(eluent_ratios: Sequence[float]) -> np.ndarray:
    """
    根据洗脱剂比例计算溶剂描述符向量

    对每种溶剂按其比例加权，将基本描述符加权求和

    Args:
        eluent_ratios: 5种溶剂的比例列表

    Returns:
        6维的溶剂描述符向量
    """
    ratios = normalize_eluent_ratios(eluent_ratios)
    total = np.zeros(6, dtype=np.float32)
    for ratio, smiles in zip(ratios, SOLVENT_SMILES):
        if ratio:
            total += basic_descriptors(smiles) * float(ratio)
    return total


# ==================== TLC 特征提取 ====================
def tlc_features(smiles: str, eluent_ratios: Sequence[float]) -> np.ndarray:
    """
    生成 TLC Rf 值预测的完整特征向量

    组合特征：MACCS指纹(167) + 溶剂描述符(6) + 分子基本描述符(6) + 溶剂比例(5)
    = 184维特征向量

    Args:
        smiles: 化合物的 SMILES 字符串
        eluent_ratios: 5种溶剂的比例

    Returns:
        形状为 (1, 184) 的特征矩阵
    """
    ratios = normalize_eluent_ratios(eluent_ratios)
    return np.concatenate(
        [
            maccs(smiles),              # 167维 MACCS 指纹
            solvent_descriptor_vector(ratios),  # 6维溶剂描述符
            basic_descriptors(smiles),   # 6维分子描述符
            ratios,                      # 5维溶剂比例
        ]
    ).reshape(1, -1)


# ==================== CC 特征提取 ====================
def cc_eluent_features(eluent_system: str) -> np.ndarray:
    """
    生成柱色谱的洗脱剂特征

    从字符串中解析 PE/EA 比例，生成溶剂特征

    Args:
        eluent_system: 洗脱剂系统描述，如 "PE/EA 90/10"

    Returns:
        11维的洗脱剂特征（5维比例 + 6维描述符）
    """
    a, b = parse_ratio_pair(eluent_system)
    total = a + b
    if total <= 0:
        pe, ea = 0.5, 0.5
    else:
        pe, ea = a / total, b / total
    ratios = np.array([pe, ea, 0.0, 0.0, 0.0], dtype=np.float32)
    return np.concatenate([ratios, solvent_descriptor_vector(ratios)])


def column_mass_feature(m_param: float) -> float:
    """
    处理固定相质量参数（确保为有效值）

    Args:
        m_param: 固定相质量（克）

    Returns:
        有效的质量值（默认为4.0）
    """
    if not math.isfinite(float(m_param)):
        return 4.0
    return float(m_param)


def cc_single_features(smiles: str, eluent_system: str, m_param: float, speed: float) -> np.ndarray:
    """
    生成单个化合物的 CC 特征向量

    组合特征：MACCS指纹(167) + 基本描述符(6) + 洗脱剂特征(11) + 柱参数(2)
    = 186维特征向量

    Args:
        smiles: 化合物 SMILES
        eluent_system: 洗脱剂系统
        m_param: 固定相质量
        speed: 流速

    Returns:
        186维特征向量
    """
    return np.concatenate(
        [
            maccs(smiles),                # 167维 MACCS 指纹
            basic_descriptors(smiles),     # 6维分子描述符
            cc_eluent_features(eluent_system),  # 11维洗脱剂特征
            np.array([column_mass_feature(m_param), float(speed)], dtype=np.float32),  # 柱参数
        ]
    )


def cc_pair_features(
    smiles_list: Sequence[str],
    eluent_system: str,
    m_param: float,
    speed: float,
) -> np.ndarray:
    """
    生成两个化合物对的 CC 特征向量（用于分离预测）

    特征构成：化合物1特征 + 化合物2特征 + |化合物1特征 - 化合物2特征|
    = 186 * 3 = 558维特征向量

    Args:
        smiles_list: 两个化合物的 SMILES 列表
        eluent_system: 洗脱剂系统
        m_param: 固定相质量
        speed: 流速

    Returns:
        形状为 (1, 558) 的特征矩阵
    """
    if len(smiles_list) < 2:
        raise ValueError("CC 预测需要两个 SMILES 字符串。")

    f1 = cc_single_features(smiles_list[0], eluent_system, m_param, speed)  # 化合物1特征
    f2 = cc_single_features(smiles_list[1], eluent_system, m_param, speed)  # 化合物2特征

    # 拼接：化合物1特征 + 化合物2特征 + 差异特征
    return np.concatenate([f1, f2, np.abs(f1 - f2)]).reshape(1, -1)


# ==================== HPLC 替代预测（无需深度模型） ====================
def hplc_surrogate_prediction(
    smiles_pair: str,
    eluent: float,
    speed: float,
    columns: Iterable[str],
) -> tuple[list[float], list[list[float]]]:
    """
    HPLC 保留时间的替代预测方法（基于分子描述符的简化算法）

    在 torch_geometric 不可用或 GeoGNN 模型加载失败时的备选方案。

    基于分子量、极性等基本描述符估算保留时间，
    精度低于训练好的 GeoGNN 模型。

    Args:
        smiles_pair: 两个分子的 SMILES，逗号分隔
        eluent: 流动相比例
        speed: 流速
        columns: 候选色谱柱列表

    Returns:
        sp_list: 各色谱柱的分离概率列表
        rt_list: 各色谱柱的保留时间对列表 [[rt1, rt2], ...]
    """
    # 解析两个分子的 SMILES
    smiles = [s.strip() for s in smiles_pair.split(",") if s.strip()]
    if len(smiles) < 2:
        raise ValueError("HPLC 预测需要两个用逗号分隔的 SMILES 字符串。")

    # 计算两个分子的基本描述符
    desc = [basic_descriptors(s) for s in smiles[:2]]

    # 计算描述符差异的均值（用于估算分离程度）
    delta = float(np.mean(np.abs(desc[0] - desc[1])))

    # 根据分子量和极性估算基础保留时间
    base_rt = [max(0.2, (d[0] / 120.0 + d[1] / 80.0) / max(speed, 0.1)) for d in desc]

    sp_list: list[float] = []
    rt_list: list[list[float]] = []

    # 对各色谱柱分别预测
    for index, column in enumerate(columns):
        # 色谱柱因子：基于柱名计算（简化模拟）
        col_factor = 1.0 + (sum(ord(ch) for ch in column.strip()) % 17) / 50.0

        # 极性因子
        polarity = max(0.01, min(float(eluent), 1.0))

        # 计算两个分子的保留时间
        rt1 = base_rt[0] * col_factor * (1.0 + polarity)
        rt2 = base_rt[1] * col_factor * (1.0 + polarity * 0.8)

        # 计算分离概率：基于保留时间差
        separation = abs(rt2 - rt1) / max(rt1, rt2, 1e-6)
        sp = max(0.0, min(0.999, separation + delta / 200.0 + index * 0.005))

        sp_list.append(float(sp))
        rt_list.append([float(rt1), float(rt2)])

    return sp_list, rt_list


# =============================================================================
# ==================== HPLC GeoGNN 模型（来自 Retention-Time-Prediction 项目）==
# =============================================================================

# ---- 尝试导入 torch_geometric（可选依赖） ----
try:
    from torch_geometric.nn import (
        MessagePassing,
        global_add_pool,
        global_mean_pool,
        global_max_pool,
        GlobalAttention,
        Set2Set,
    )
    from torch_geometric.data import Data
    _HAS_TORCH_GEOMETRIC = True
except ImportError:
    _HAS_TORCH_GEOMETRIC = False

# ---- 日光官能团 SMARTS 列表 ----
DAY_LIGHT_FG_SMARTS_LIST = [
    "[CX4]", "[$([CX2](=C)=C)]", "[$([CX3]=[CX3])]", "[$([CX2]#C)]",
    "[CX3]=[OX1]", "[$([CX3]=[OX1]),$([CX3+]-[OX1-])]", "[CX3](=[OX1])C",
    "[OX1]=CN", "[CX3](=[OX1])O", "[CX3](=[OX1])[F,Cl,Br,I]",
    "[CX3H1](=O)[#6]", "[CX3](=[OX1])[OX2][CX3](=[OX1])",
    "[NX3][CX3](=[OX1])[#6]", "[NX3][CX3]=[NX3+]",
    "[NX3,NX4+][CX3](=[OX1])[OX2,OX1-]", "[NX3][CX3](=[OX1])[OX2H0]",
    "[NX3,NX4+][CX3](=[OX1])[OX2H,OX1-]", "[CX3](=O)[O-]",
    "[CX3](=[OX1])(O)O", "[CX3](=[OX1])([OX2])[OX2H,OX1H0-1]",
    "C[OX2][CX3](=[OX1])[OX2]C", "[CX3](=O)[OX2H1]",
    "[CX3](=O)[OX1H0-,OX2H1]", "[NX3][CX2]#[NX1]",
    "[#6][CX3](=O)[OX2H0][#6]", "[#6][CX3](=O)[#6]",
    "[OD2]([#6])[#6]", "[H]", "[!#1]", "[H+]", "[+H]", "[!H]",
    "[NX3;H2,H1;!$(NC=O)]", "[NX3][CX3]=[CX3]",
    "[NX3;H2;!$(NC=[!#6]);!$(NC#[!#6])][#6]",
    "[NX3;H2,H1;!$(NC=O)].[NX3;H2,H1;!$(NC=O)]",
    "[NX3][$(C=C),$(cc)]",
    "[NX3,NX4+][CX4H]([*])[CX3](=[OX1])[O,N]",
    "[NX3H2,NH3X4+][CX4H]([*])[CX3](=[OX1])[NX3,NX4+][CX4H]([*])[CX3](=[OX1])[OX2H,OX1-]",
    "[$([NX3H2,NX4H3+]),$([NX3H](C)(C))][CX4H]([*])[CX3](=[OX1])[OX2H,OX1-,N]",
    "[CH3X4]",
    "[CH2X4][CH2X4][CH2X4][NHX3][CH0X3](=[NH2X3+,NHX2+0])[NH2X3]",
    "[CH2X4][CX3](=[OX1])[NX3H2]",
    "[CH2X4][CX3](=[OX1])[OH0-,OH]",
    "[CH2X4][SX2H,SX1H0-]",
    "[CH2X4][CH2X4][CX3](=[OX1])[OH0-,OH]",
    "[$([$([NX3H2,NX4H3+]),$([NX3H](C)(C))][CX4H2][CX3](=[OX1])[OX2H,OX1-,N])]",
    "[CH2X4][#6X3]1:[$([#7X3H+,#7X2H0+0]:[#6X3H]:[#7X3H]),$([#7X3H])]:[#6X3H]:[$([#7X3H+,#7X2H0+0]:[#6X3H]:[#7X3H]),$([#7X3H])]:[#6X3H]1",
    "[CHX4]([CH3X4])[CH2X4][CH3X4]",
    "[CH2X4][CHX4]([CH3X4])[CH3X4]",
    "[CH2X4][CH2X4][CH2X4][CH2X4][NX4+,NX3+0]",
    "[CH2X4][CH2X4][SX2][CH3X4]",
    "[CH2X4][cX3]1[cX3H][cX3H][cX3H][cX3H][cX3H]1",
    "[$([NX3H,NX4H2+]),$([NX3](C)(C)(C))]1[CX4H]([CH2][CH2][CH2]1)[CX3](=[OX1])[OX2H,OX1-,N]",
    "[CH2X4][OX2H]",
    "[NX3][CX3]=[SX1]",
    "[CHX4]([CH3X4])[OX2H]",
    "[CH2X4][cX3]1[cX3H][nX3H][cX3]2[cX3H][cX3H][cX3H][cX3H][cX3]12",
    "[CH2X4][cX3]1[cX3H][cX3H][cX3]([OHX2,OH0X1-])[cX3H][cX3H]1",
    "[CHX4]([CH3X4])[CH3X4]",
    "N[CX4H2][CX3](=[OX1])[O,N]",
    "N1[CX4H]([CH2][CH2][CH2]1)[CX3](=[OX1])[O,N]",
    "[$(*-[NX2-]-[NX2+]#[NX1]),$(*-[NX2]=[NX2+]=[NX1-])]",
    "[$([NX1-]=[NX2+]=[NX1-]),$([NX1]#[NX2+]-[NX1-2])]",
    "[#7]", "[NX2]=N", "[NX2]=[NX2]",
    "[$([NX2]=[NX3+]([O-])[#6]),$([NX2]=[NX3+0](=[O])[#6])]",
    "[$([#6]=[N+]=[N-]),$([#6-]-[N+]#[N])]",
    "[$([nr5]:[nr5,or5,sr5]),$([nr5]:[cr5]:[nr5,or5,sr5])]",
    "[NX3][NX3]", "[NX3][NX2]=[*]",
    "[CX3;$([C]([#6])[#6]),$([CH][#6])]=[NX2][#6]",
    "[$([CX3]([#6])[#6]),$([CX3H][#6])]=[$([NX2][#6]),$([NX2H])]",
    "[NX3+]=[CX3]",
    "[CX3](=[OX1])[NX3H][CX3](=[OX1])",
    "[CX3](=[OX1])[NX3H0]([#6])[CX3](=[OX1])",
    "[CX3](=[OX1])[NX3H0]([NX3H0]([CX3](=[OX1]))[CX3](=[OX1]))[CX3](=[OX1])",
    "[$([NX3](=[OX1])(=[OX1])O),$([NX3+]([OX1-])(=[OX1])O)]",
    "[$([OX1]=[NX3](=[OX1])[OX1-]),$([OX1]=[NX3+]([OX1-])[OX1-])]",
    "[NX1]#[CX2]", "[CX1-]#[NX2+]",
    "[$([NX3](=O)=O),$([NX3+](=O)[O-])][!#8]",
    "[$([NX3](=O)=O),$([NX3+](=O)[O-])][!#8].[$([NX3](=O)=O),$([NX3+](=O)[O-])][!#8]",
    "[NX2]=[OX1]",
    "[$([#7+][OX1-]),$([#7v5]=[OX1]);!$([#7](~[O])~[O]);!$([#7]=[#7])]",
    "[OX2H]", "[#6][OX2H]", "[OX2H][CX3]=[OX1]",
    "[OX2H]P", "[OX2H][#6X3]=[#6]", "[OX2H][cX3]:[c]",
    "[OX2H][$(C=C),$(cc)]", "[$([OH]-*=[!#6])]", "[OX2,OX1-][OX2,OX1-]",
    # P
    "[$(P(=[OX1])([$([OX2H]),$([OX1-]),$([OX2]P)])([$([OX2H]),$([OX1-]),$([OX2]P)])[$([OX2H]),$([OX1-]),$([OX2]P)]),$([P+]([OX1-])([$([OX2H]),$([OX1-]),$([OX2]P)])([$([OX2H]),$([OX1-]),$([OX2]P)])[$([OX2H]),$([OX1-]),$([OX2]P)])]",
    "[$(P(=[OX1])([OX2][#6])([$([OX2H]),$([OX1-]),$([OX2][#6])])[$([OX2H]),$([OX1-]),$([OX2][#6]),$([OX2]P)]),$([P+]([OX1-])([OX2][#6])([$([OX2H]),$([OX1-]),$([OX2][#6])])[$([OX2H]),$([OX1-]),$([OX2][#6]),$([OX2]P)])]",
    # S
    "[S-][CX3](=S)[#6]", "[#6X3](=[SX1])([!N])[!N]",
    "[SX2]", "[#16X2H]", "[#16!H0]", "[#16X2H0]",
    "[#16X2H0][!#16]", "[#16X2H0][#16X2H0]",
    "[#16X2H0][!#16].[#16X2H0][!#16]",
    "[$([#16X3](=[OX1])[OX2H0]),$([#16X3+]([OX1-])[OX2H0])]",
    "[$([#16X3](=[OX1])[OX2H,OX1H0-]),$([#16X3+]([OX1-])[OX2H,OX1H0-])]",
    "[$([#16X4](=[OX1])=[OX1]),$([#16X4+2]([OX1-])[OX1-])]",
    "[$([#16X4](=[OX1])(=[OX1])([#6])[#6]),$([#16X4+2]([OX1-])([OX1-])([#6])[#6])]",
    "[$([#16X4](=[OX1])(=[OX1])([#6])[OX2H,OX1H0-]),$([#16X4+2]([OX1-])([OX1-])([#6])[OX2H,OX1H0-])]",
    "[$([#16X4](=[OX1])(=[OX1])([#6])[OX2H0]),$([#16X4+2]([OX1-])([OX1-])([#6])[OX2H0])]",
    "[$([#16X4]([NX3])(=[OX1])(=[OX1])[#6]),$([#16X4+2]([NX3])([OX1-])([OX1-])[#6])]",
    "[SX4](C)(C)(=O)=N",
    "[$([SX4](=[OX1])(=[OX1])([!O])[NX3]),$([SX4+2]([OX1-])([OX1-])([!O])[NX3])]",
    "[$([#16X3]=[OX1]),$([#16X3+][OX1-])]",
    "[$([#16X3](=[OX1])([#6])[#6]),$([#16X3+]([OX1-])([#6])[#6])]",
    "[$([#16X4](=[OX1])(=[OX1])([OX2H,OX1H0-])[OX2][#6]),$([#16X4+2]([OX1-])([OX1-])([OX2H,OX1H0-])[OX2][#6])]",
    "[$([SX4](=O)(=O)(O)O),$([SX4+2]([O-])([O-])(O)O)]",
    "[$([#16X4](=[OX1])(=[OX1])([OX2][#6])[OX2][#6]),$([#16X4](=[OX1])(=[OX1])([OX2][#6])[OX2][#6])]",
    "[$([#16X4]([NX3])(=[OX1])(=[OX1])[OX2][#6]),$([#16X4+2]([NX3])([OX1-])([OX1-])[OX2][#6])]",
    "[$([#16X4]([NX3])(=[OX1])(=[OX1])[OX2H,OX1H0-]),$([#16X4+2]([NX3])([OX1-])([OX1-])[OX2H,OX1H0-])]",
    "[#16X2][OX2H,OX1H0-]", "[#16X2][OX2H0]",
    "[#6][F,Cl,Br,I]", "[F,Cl,Br,I]", "[F,Cl,Br,I].[F,Cl,Br,I].[F,Cl,Br,I]",
]

# ---- CompoundKit: 化合物特征提取工具包 ----
class CompoundKit:
    """化合物特征提取工具包 - 原子和键的特征编码器"""

    atom_vocab_dict = {
        "atomic_num": list(range(1, 119)) + ['misc'],
        "chiral_tag": [],  # 在 class 定义后填充
        "degree": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 'misc'],
        "explicit_valence": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 'misc'],
        "formal_charge": [-5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 'misc'],
        "hybridization": [],  # 在 class 定义后填充
        "implicit_valence": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 'misc'],
        "is_aromatic": [0, 1],
        "total_numHs": [0, 1, 2, 3, 4, 5, 6, 7, 8, 'misc'],
        'num_radical_e': [0, 1, 2, 3, 4, 'misc'],
        'atom_is_in_ring': [0, 1],
        'valence_out_shell': [0, 1, 2, 3, 4, 5, 6, 7, 8, 'misc'],
        'in_num_ring_with_size3': [0, 1, 2, 3, 4, 5, 6, 7, 8, 'misc'],
        'in_num_ring_with_size4': [0, 1, 2, 3, 4, 5, 6, 7, 8, 'misc'],
        'in_num_ring_with_size5': [0, 1, 2, 3, 4, 5, 6, 7, 8, 'misc'],
        'in_num_ring_with_size6': [0, 1, 2, 3, 4, 5, 6, 7, 8, 'misc'],
        'in_num_ring_with_size7': [0, 1, 2, 3, 4, 5, 6, 7, 8, 'misc'],
        'in_num_ring_with_size8': [0, 1, 2, 3, 4, 5, 6, 7, 8, 'misc'],
    }
    bond_vocab_dict = {
        "bond_dir": [],      # 在 class 定义后填充
        "bond_type": [],     # 在 class 定义后填充
        "is_in_ring": [0, 1],
        'bond_stereo': [],   # 在 class 定义后填充
        'is_conjugated': [0, 1],
    }
    atom_float_names = ["van_der_waals_radis", "partial_charge", 'mass']

    day_light_fg_smarts_list = DAY_LIGHT_FG_SMARTS_LIST
    day_light_fg_mo_list = []  # 在 class 定义后填充

    morgan_fp_N = 200
    maccs_fp_N = 167
    period_table = Chem.GetPeriodicTable()

    @staticmethod
    def get_atom_value(atom, name):
        """获取原子属性值"""
        if name == 'atomic_num':
            return atom.GetAtomicNum()
        elif name == 'chiral_tag':
            return atom.GetChiralTag()
        elif name == 'degree':
            return atom.GetDegree()
        elif name == 'explicit_valence':
            return atom.GetExplicitValence()
        elif name == 'formal_charge':
            return atom.GetFormalCharge()
        elif name == 'hybridization':
            return atom.GetHybridization()
        elif name == 'implicit_valence':
            return atom.GetImplicitValence()
        elif name == 'is_aromatic':
            return int(atom.GetIsAromatic())
        elif name == 'mass':
            return int(atom.GetMass())
        elif name == 'total_numHs':
            return atom.GetTotalNumHs()
        elif name == 'num_radical_e':
            return atom.GetNumRadicalElectrons()
        elif name == 'atom_is_in_ring':
            return int(atom.IsInRing())
        elif name == 'valence_out_shell':
            return CompoundKit.period_table.GetNOuterElecs(atom.GetAtomicNum())
        else:
            raise ValueError(name)

    @staticmethod
    def get_atom_feature_id(atom, name):
        """获取原子特征 ID（用于 Embedding 编码）"""
        return safe_index(CompoundKit.atom_vocab_dict[name],
                          CompoundKit.get_atom_value(atom, name))

    @staticmethod
    def get_atom_feature_size(name):
        """获取原子特征词汇表大小"""
        return len(CompoundKit.atom_vocab_dict[name])

    @staticmethod
    def get_bond_value(bond, name):
        """获取键属性值"""
        if name == 'bond_dir':
            return bond.GetBondDir()
        elif name == 'bond_type':
            return bond.GetBondType()
        elif name == 'is_in_ring':
            return int(bond.IsInRing())
        elif name == 'is_conjugated':
            return int(bond.GetIsConjugated())
        elif name == 'bond_stereo':
            return bond.GetStereo()
        else:
            raise ValueError(name)

    @staticmethod
    def get_bond_feature_id(bond, name):
        """获取键特征 ID"""
        return safe_index(CompoundKit.bond_vocab_dict[name],
                          CompoundKit.get_bond_value(bond, name))

    @staticmethod
    def get_bond_feature_size(name):
        """获取键特征词汇表大小"""
        return len(CompoundKit.bond_vocab_dict[name])

    @staticmethod
    def get_morgan_fingerprint(mol, radius=2):
        """计算 Morgan 指纹（200位）"""
        nBits = CompoundKit.morgan_fp_N
        mfp = AllChem.GetMorganFingerprintAsBitVect(mol, radius, nBits=nBits)
        return [int(b) for b in mfp.ToBitString()]

    @staticmethod
    def get_maccs_fingerprint(mol):
        """计算 MACCS 指纹（167位）"""
        fp = AllChem.GetMACCSKeysFingerprint(mol)
        return [int(b) for b in fp.ToBitString()]

    @staticmethod
    def get_daylight_functional_group_counts(mol):
        """计算日光官能团计数"""
        fg_counts = []
        for fg_mol in CompoundKit.day_light_fg_mo_list:
            sub_structs = Chem.Mol.GetSubstructMatches(mol, fg_mol, uniquify=True)
            fg_counts.append(len(sub_structs))
        return fg_counts

    @staticmethod
    def get_ring_size(mol):
        """获取各原子的环大小信息 (N, 6)"""
        rings = mol.GetRingInfo()
        rings_info = [r for r in rings.AtomRings()]
        ring_list = []
        for atom in mol.GetAtoms():
            atom_result = []
            for ringsize in range(3, 9):
                num = sum(1 for r in rings_info
                          if len(r) == ringsize and atom.GetIdx() in r)
                atom_result.append(min(num, 9))
            ring_list.append(atom_result)
        return ring_list

    @staticmethod
    def check_partial_charge(atom):
        """获取原子的 Gasteiger 偏电荷"""
        pc = atom.GetDoubleProp('_GasteigerCharge')
        if pc != pc:  # NaN 检查
            pc = 0
        if pc == float('inf'):
            pc = 10
        return pc

    @staticmethod
    def atom_to_feat_vector(atom):
        """将原子转换为特征向量字典"""
        return {
            "atomic_num": safe_index(CompoundKit.atom_vocab_dict["atomic_num"],
                                     atom.GetAtomicNum()),
            "chiral_tag": safe_index(CompoundKit.atom_vocab_dict["chiral_tag"],
                                     atom.GetChiralTag()),
            "degree": safe_index(CompoundKit.atom_vocab_dict["degree"],
                                 atom.GetTotalDegree()),
            "explicit_valence": safe_index(CompoundKit.atom_vocab_dict["explicit_valence"],
                                           atom.GetExplicitValence()),
            "formal_charge": safe_index(CompoundKit.atom_vocab_dict["formal_charge"],
                                        atom.GetFormalCharge()),
            "hybridization": safe_index(CompoundKit.atom_vocab_dict["hybridization"],
                                        atom.GetHybridization()),
            "implicit_valence": safe_index(CompoundKit.atom_vocab_dict["implicit_valence"],
                                           atom.GetImplicitValence()),
            "is_aromatic": safe_index(CompoundKit.atom_vocab_dict["is_aromatic"],
                                      int(atom.GetIsAromatic())),
            "total_numHs": safe_index(CompoundKit.atom_vocab_dict["total_numHs"],
                                      atom.GetTotalNumHs()),
            'num_radical_e': safe_index(CompoundKit.atom_vocab_dict['num_radical_e'],
                                        atom.GetNumRadicalElectrons()),
            'atom_is_in_ring': safe_index(CompoundKit.atom_vocab_dict['atom_is_in_ring'],
                                          int(atom.IsInRing())),
            'valence_out_shell': safe_index(
                CompoundKit.atom_vocab_dict['valence_out_shell'],
                CompoundKit.period_table.GetNOuterElecs(atom.GetAtomicNum())),
            'van_der_waals_radis': CompoundKit.period_table.GetRvdw(atom.GetAtomicNum()),
            'partial_charge': CompoundKit.check_partial_charge(atom),
            'mass': atom.GetMass(),
        }

    @staticmethod
    def get_atom_names(mol):
        """获取分子中所有原子的特征字典列表"""
        atom_features_dicts = []
        Chem.rdPartialCharges.ComputeGasteigerCharges(mol)
        for atom in mol.GetAtoms():
            atom_features_dicts.append(CompoundKit.atom_to_feat_vector(atom))
        ring_list = CompoundKit.get_ring_size(mol)
        for i, atom in enumerate(mol.GetAtoms()):
            for j, size in enumerate(range(3, 9)):
                atom_features_dicts[i][f'in_num_ring_with_size{size}'] = safe_index(
                    CompoundKit.atom_vocab_dict[f'in_num_ring_with_size{size}'],
                    ring_list[i][j])
        return atom_features_dicts


# ---- 初始化 CompoundKit 需要 RDKit 枚举的字段 ----
try:
    CompoundKit.atom_vocab_dict["chiral_tag"] = rdchem_enum_to_list(
        Chem.rdchem.ChiralType.values)
    CompoundKit.atom_vocab_dict["hybridization"] = rdchem_enum_to_list(
        Chem.rdchem.HybridizationType.values)
    CompoundKit.bond_vocab_dict["bond_dir"] = rdchem_enum_to_list(
        Chem.rdchem.BondDir.values)
    CompoundKit.bond_vocab_dict["bond_type"] = rdchem_enum_to_list(
        Chem.rdchem.BondType.values)
    CompoundKit.bond_vocab_dict["bond_stereo"] = rdchem_enum_to_list(
        Chem.rdchem.BondStereo.values)
except Exception:
    pass

CompoundKit.day_light_fg_mo_list = [
    Chem.MolFromSmarts(s) for s in DAY_LIGHT_FG_SMARTS_LIST
]


# ---- Compound3DKit: 3D 构象处理 ----
class Compound3DKit:
    """化合物 3D 构象处理工具"""

    @staticmethod
    def get_atom_poses(mol, conf):
        """从构象中获取原子 3D 坐标"""
        atom_poses = []
        for i, atom in enumerate(mol.GetAtoms()):
            if atom.GetAtomicNum() == 0:
                return [[0.0, 0.0, 0.0]] * len(mol.GetAtoms())
            pos = conf.GetAtomPosition(i)
            atom_poses.append([pos.x, pos.y, pos.z])
        return atom_poses

    @staticmethod
    def get_MMFF_atom_poses(mol):
        """获取分子 MMFF 优化后的 3D 坐标"""
        conf = mol.GetConformer()
        return mol, Compound3DKit.get_atom_poses(mol, conf)

    @staticmethod
    def get_2d_atom_poses(mol):
        """获取分子 2D 坐标"""
        AllChem.Compute2DCoords(mol)
        conf = mol.GetConformer()
        return Compound3DKit.get_atom_poses(mol, conf)

    @staticmethod
    def get_bond_lengths(edges, atom_poses):
        """计算键长"""
        bond_lengths = []
        for src_node_i, tar_node_j in edges:
            bond_lengths.append(
                np.linalg.norm(atom_poses[tar_node_j] - atom_poses[src_node_i]))
        return np.array(bond_lengths, 'float32')

    @staticmethod
    def get_superedge_angles(edges, atom_poses, dir_type='HT'):
        """计算键角（super-edge angles）"""

        def _get_vec(atom_poses, edge):
            return atom_poses[edge[1]] - atom_poses[edge[0]]

        def _get_angle(vec1, vec2):
            norm1 = np.linalg.norm(vec1)
            norm2 = np.linalg.norm(vec2)
            if norm1 == 0 or norm2 == 0:
                return 0
            vec1 = vec1 / (norm1 + 1e-5)
            vec2 = vec2 / (norm2 + 1e-5)
            return np.arccos(np.dot(vec1, vec2))

        E = len(edges)
        edge_indices = np.arange(E)
        super_edges = []
        bond_angles = []
        for tar_edge_i in range(E):
            tar_edge = edges[tar_edge_i]
            if dir_type == 'HT':
                src_edge_indices = edge_indices[edges[:, 1] == tar_edge[0]]
            elif dir_type == 'HH':
                src_edge_indices = edge_indices[edges[:, 1] == tar_edge[1]]
            else:
                raise ValueError(dir_type)
            for src_edge_i in src_edge_indices:
                if src_edge_i == tar_edge_i:
                    continue
                src_edge = edges[src_edge_i]
                src_vec = _get_vec(atom_poses, src_edge)
                tar_vec = _get_vec(atom_poses, tar_edge)
                super_edges.append([src_edge_i, tar_edge_i])
                bond_angles.append(_get_angle(src_vec, tar_vec))

        if len(super_edges) == 0:
            super_edges = np.zeros([0, 2], 'int64')
            bond_angles = np.zeros([0, ], 'float32')
        else:
            super_edges = np.array(super_edges, 'int64')
            bond_angles = np.array(bond_angles, 'float32')
        return super_edges, bond_angles


# ---- 特征维度计算 ----
def get_atom_feature_dims(list_acquired_feature_names):
    """计算指定原子特征的维度列表"""
    return list(map(len, [CompoundKit.atom_vocab_dict[name]
                          for name in list_acquired_feature_names]))


def get_bond_feature_dims(list_acquired_feature_names):
    """计算指定键特征的维度列表（+1 为自环边留空间）"""
    list_bond_feat_dim = list(map(len, [CompoundKit.bond_vocab_dict[name]
                                        for name in list_acquired_feature_names]))
    return [_l + 1 for _l in list_bond_feat_dim]


# ---- 分子 → 图数据转换 ----
def mol_to_graph_data(mol):
    """
    将 RDKit 分子转换为 GNN 图数据结构

    提取原子特征（9类 + mass）、键特征（3类）、边索引等

    Args:
        mol: RDKit Mol 对象

    Returns:
        包含原子特征、键特征、边索引、指纹的字典
    """
    if len(mol.GetAtoms()) == 0:
        return None

    atom_id_names = [
        "atomic_num", "chiral_tag", "degree", "explicit_valence",
        "formal_charge", "hybridization", "implicit_valence",
        "is_aromatic", "total_numHs",
    ]
    bond_id_names = ["bond_dir", "bond_type", "is_in_ring"]

    data = {}
    for name in atom_id_names:
        data[name] = []
    data['mass'] = []
    for name in bond_id_names:
        data[name] = []
    data['edges'] = []

    # 原子特征
    for atom in mol.GetAtoms():
        if atom.GetAtomicNum() == 0:
            return None
        for name in atom_id_names:
            data[name].append(
                CompoundKit.get_atom_feature_id(atom, name) + 1)  # +1: 0 留给 OOV
        data['mass'].append(CompoundKit.get_atom_value(atom, 'mass') * 0.01)

    # 键特征
    for bond in mol.GetBonds():
        i = bond.GetBeginAtomIdx()
        j = bond.GetEndAtomIdx()
        data['edges'] += [(i, j), (j, i)]
        for name in bond_id_names:
            bond_feature_id = CompoundKit.get_bond_feature_id(bond, name) + 1
            data[name] += [bond_feature_id] * 2

    # 自环
    N = len(data[atom_id_names[0]])
    for i in range(N):
        data['edges'] += [(i, i)]
    for name in bond_id_names:
        bond_feature_id = CompoundKit.get_bond_feature_size(name) + 2
        data[name] += [bond_feature_id] * N

    # 无边情况
    if len(data['edges']) == 0:
        for name in bond_id_names:
            data[name] = np.zeros((0,), dtype="int64")
        data['edges'] = np.zeros((0, 2), dtype="int64")

    # 转 numpy
    for name in atom_id_names:
        data[name] = np.array(data[name], 'int64')
    data['mass'] = np.array(data['mass'], 'float32')
    for name in bond_id_names:
        data[name] = np.array(data[name], 'int64')
    data['edges'] = np.array(data['edges'], 'int64')

    # 指纹
    data['morgan_fp'] = np.array(CompoundKit.get_morgan_fingerprint(mol), 'int64')
    data['maccs_fp'] = np.array(CompoundKit.get_maccs_fingerprint(mol), 'int64')
    data['daylight_fg_counts'] = np.array(
        CompoundKit.get_daylight_functional_group_counts(mol), 'int64')
    return data


def mol_to_geognn_graph_data_MMFF3d(mol):
    """
    将 RDKit 分子转换为 GeoGNN 几何增强图数据（含 MMFF 3D 坐标/键长/键角）

    Args:
        mol: RDKit Mol 对象

    Returns:
        含 atom_pos、bond_length、BondAngleGraph 的扩展图数据字典
    """
    data = mol_to_graph_data(mol)
    if len(mol.GetAtoms()) <= 400:
        mol, atom_poses = Compound3DKit.get_MMFF_atom_poses(mol)
    else:
        atom_poses = Compound3DKit.get_2d_atom_poses(mol)
    data['atom_pos'] = np.array(atom_poses, 'float32')
    data['bond_length'] = Compound3DKit.get_bond_lengths(
        data['edges'], data['atom_pos'])
    BondAngleGraph_edges, bond_angles = \
        Compound3DKit.get_superedge_angles(data['edges'], data['atom_pos'])
    data['BondAngleGraph_edges'] = BondAngleGraph_edges
    data['bond_angle'] = np.array(bond_angles, 'float32')
    return data


def obtain_3D_mol(smiles: str, name: str = "conform"):
    """
    从 SMILES 生成分子的 3D 构象并保存为 .mol 文件

    Args:
        smiles: SMILES 字符串
        name: 输出文件名（不含扩展名）

    Returns:
        优化后的 RDKit Mol 对象
    """
    mol = AllChem.MolFromSmiles(smiles)
    new_mol = Chem.AddHs(mol)
    AllChem.EmbedMultipleConfs(new_mol)
    AllChem.MMFFOptimizeMoleculeConfs(new_mol)
    new_mol = Chem.RemoveHs(new_mol)
    Chem.MolToMolFile(new_mol, name + '.mol')
    return new_mol


# ---- HPLC 色谱柱配置 ----
# 色谱柱参数: [涂层类型, 直径, 固定化类型, 描述符索引]
COLUMN_SPECIFY = {
    'ADH': [1, 5, 0, 0],  'ODH': [1, 5, 0, 1],   'IC': [0, 5, 1, 2],
    'IA':  [0, 5, 1, 3],  'OJH': [1, 5, 0, 4],   'ASH': [1, 5, 0, 5],
    'IC3': [0, 3, 1, 6],  'IE': [0, 5, 1, 7],    'ID':  [0, 5, 1, 8],
    'OD3': [1, 3, 0, 9],  'IB': [0, 5, 1, 10],   'AD':  [1, 10, 0, 11],
    'AD3': [1, 3, 0, 12], 'IF': [0, 5, 1, 13],   'OD':  [1, 10, 0, 14],
    'AS':  [1, 10, 0, 15],'OJ3': [1, 3, 0, 16],  'IG':  [0, 5, 1, 17],
    'AZ':  [1, 10, 0, 18],'IAH': [0, 5, 1, 19],  'OJ':  [1, 10, 0, 20],
    'ICH': [0, 5, 1, 21], 'OZ3': [1, 3, 0, 22],  'IF3': [0, 3, 1, 23],
    'IAU': [0, 1.6, 1, 24],
}
COLUMN_NAMES = list(COLUMN_SPECIFY.keys())

# ---- GeoGNN 图神经网络模型组件 ----

# 原子/键特征名称（用于 Embedding 编码）
ATOM_ID_NAMES = [
    "atomic_num", "chiral_tag", "degree", "explicit_valence",
    "formal_charge", "hybridization", "implicit_valence",
    "is_aromatic", "total_numHs",
]
BOND_ID_NAMES = ["bond_dir", "bond_type", "is_in_ring"]
# 含色谱柱信息时的扩展键特征名（coated/immobilized 是自定义二值特征，不在 vocab 中）
FULL_BOND_ID_NAMES = BOND_ID_NAMES + ['coated', 'immobilized']

# 键的浮点特征名称
BOND_FLOAT_NAMES = ["bond_length", "prop", "diameter"]
# 键角的浮点特征名称（含色谱柱描述符）
BOND_ANGLE_FLOAT_NAMES = [
    'bond_angle', 'TPSA', 'RASA', 'RPSA', 'MDEC', 'MATS',
    'column_TPSA', 'column_RASA', 'column_RPSA', 'column_MDEC', 'column_MATS',
]

CC_BOND_FLOAT_NAMES = ["bond_length", 'prop', 'e', 'm', 'V_e']
CC_BOND_ANGLE_FLOAT_NAMES = [
    'bond_angle', '153', '278', '884', '885', '1273', '1594',
    '431', '1768', '1769', '1288', '1521',
    'MolWt', 'nRotB', 'HBD', 'HBA', 'LogP',
]
CC_ELUENT_SMILES = ['CCCCCC', 'CC(OCC)=O', 'C(Cl)Cl', 'CO', 'CCOCC']

FULL_ATOM_FEATURE_DIMS = get_atom_feature_dims(ATOM_ID_NAMES)
# 键特征维度：vocab 中的3个 + coated(2二值) + immobilized(2二值)
FULL_BOND_FEATURE_DIMS = get_bond_feature_dims(BOND_ID_NAMES) + [2, 2]


class AtomEncoder(nn.Module):
    """
    原子特征编码器 - 将各类原子属性通过 Embedding 层编码为统一维度的向量
    """

    def __init__(self, emb_dim: int):
        super().__init__()
        self.atom_embedding_list = nn.ModuleList()
        for dim in FULL_ATOM_FEATURE_DIMS:
            emb = nn.Embedding(dim + 5, emb_dim)
            nn.init.xavier_uniform_(emb.weight.data)
            self.atom_embedding_list.append(emb)

    def forward(self, x):
        x_embedding = 0
        for i in range(x.shape[1]):
            x_embedding += self.atom_embedding_list[i](x[:, i])
        return x_embedding


class BondEncoder(nn.Module):
    """
    键特征编码器 - 将各类键属性通过 Embedding 层编码为统一维度的向量
    """

    def __init__(self, emb_dim: int):
        super().__init__()
        self.bond_embedding_list = nn.ModuleList()
        for dim in FULL_BOND_FEATURE_DIMS:
            emb = nn.Embedding(dim + 5, emb_dim)
            nn.init.xavier_uniform_(emb.weight.data)
            self.bond_embedding_list.append(emb)

    def forward(self, edge_attr):
        bond_embedding = 0
        for i in range(edge_attr.shape[1]):
            bond_embedding += self.bond_embedding_list[i](edge_attr[:, i])
        return bond_embedding


class RBF(nn.Module):
    """
    径向基函数（Radial Basis Function）层

    将连续值（如键长）转换为离散的 RBF 特征向量
    """

    def __init__(self, centers, gamma):
        super().__init__()
        self.register_buffer("centers", centers.reshape([1, -1]).to(torch.float32))
        self.register_buffer("gamma", gamma.to(torch.float32))

    def forward(self, x):
        x = x.reshape([-1, 1]).to(self.centers.device, dtype=self.centers.dtype)
        return torch.exp(-self.gamma * torch.square(x - self.centers))


class BondFloatRBF(nn.Module):
    """
    键浮点特征 RBF 编码器

    键长 → RBF → Linear → 嵌入向量
    比例  → RBF → Linear → 嵌入向量
    直径  → RBF → Linear → 嵌入向量
    """

    def __init__(self, bond_float_names, embed_dim, rbf_params=None):
        super().__init__()
        self.bond_float_names = bond_float_names

        if rbf_params is None:
            self.rbf_params = {
                'bond_length': (
                    nn.Parameter(torch.arange(0, 2, 0.1)),
                    nn.Parameter(torch.Tensor([10.0]))),
                'prop': (
                    nn.Parameter(torch.arange(0, 1, 0.05)),
                    nn.Parameter(torch.Tensor([1.0]))),
                'diameter': (
                    nn.Parameter(torch.arange(3, 12, 0.3)),
                    nn.Parameter(torch.Tensor([1.0]))),
                'column_TPSA': (
                    nn.Parameter(torch.arange(0, 1, 0.05)),
                    nn.Parameter(torch.Tensor([1.0]))),
                'column_RASA': (
                    nn.Parameter(torch.arange(0, 1, 0.05)),
                    nn.Parameter(torch.Tensor([1.0]))),
                'column_RPSA': (
                    nn.Parameter(torch.arange(0, 1, 0.05)),
                    nn.Parameter(torch.Tensor([1.0]))),
                'column_MDEC': (
                    nn.Parameter(torch.arange(0, 10, 0.5)),
                    nn.Parameter(torch.Tensor([2.0]))),
                'column_MATS': (
                    nn.Parameter(torch.arange(0, 1, 0.05)),
                    nn.Parameter(torch.Tensor([1.0]))),
                'e': (
                    nn.Parameter(torch.arange(0, 1, 0.05)),
                    nn.Parameter(torch.Tensor([1.0]))),
                'V_e': (
                    nn.Parameter(torch.arange(0, 1, 0.05)),
                    nn.Parameter(torch.Tensor([1.0]))),
                'm': (
                    nn.Parameter(torch.arange(0, 1, 0.05)),
                    nn.Parameter(torch.Tensor([1.0]))),
            }
        else:
            self.rbf_params = rbf_params

        self.linear_list = nn.ModuleList()
        self.rbf_list = nn.ModuleList()
        for name in self.bond_float_names:
            centers, gamma = self.rbf_params[name]
            # gamma 必须是普通 tensor（不是 nn.Parameter），
            # 否则 RBF 会把它注册为模型参数，导致与旧 checkpoint 不兼容。
            if isinstance(gamma, nn.Parameter):
                gamma = gamma.data.clone()
            self.rbf_list.append(RBF(centers, gamma))
            self.linear_list.append(nn.Linear(len(centers), embed_dim))

    def forward(self, bond_float_features):
        out_embed = 0
        for i, name in enumerate(self.bond_float_names):
            x = bond_float_features[:, i].reshape(-1, 1)
            rbf_x = self.rbf_list[i](x)
            out_embed += self.linear_list[i](rbf_x)
        return out_embed


class BondAngleFloatRBF(nn.Module):
    """
    键角浮点特征 RBF 编码器

    键角 → RBF → Linear → 嵌入向量
    其他分子描述符 → Linear → 嵌入向量
    """

    def __init__(self, bond_angle_float_names, embed_dim, rbf_params=None):
        super().__init__()
        self.bond_angle_float_names = bond_angle_float_names
        if rbf_params is None:
            self.rbf_params = {
                'bond_angle': (
                    nn.Parameter(torch.arange(0, torch.pi, 0.1)),
                    nn.Parameter(torch.Tensor([10.0]))),
            }
        else:
            self.rbf_params = rbf_params

        self.linear_list = nn.ModuleList()
        self.rbf_list = nn.ModuleList()
        for name in self.bond_angle_float_names:
            if name == 'bond_angle':
                centers, gamma = self.rbf_params[name]
                # gamma 必须是普通 tensor（不是 nn.Parameter），
                # 否则 RBF 会把它注册为模型参数，导致与旧 checkpoint 不兼容。
                if isinstance(gamma, nn.Parameter):
                    gamma = gamma.data.clone()
                self.rbf_list.append(RBF(centers, gamma))
                self.linear_list.append(nn.Linear(len(centers), embed_dim))
            else:
                # 其他描述符合并后经一个 Linear 层编码
                self.linear_list.append(
                    nn.Linear(len(self.bond_angle_float_names) - 1, embed_dim))
                break

    def forward(self, bond_angle_float_features):
        out_embed = 0
        for i, name in enumerate(self.bond_angle_float_names):
            if name == 'bond_angle':
                x = bond_angle_float_features[:, i].reshape(-1, 1)
                rbf_x = self.rbf_list[i](x)
                out_embed += self.linear_list[i](rbf_x)
            else:
                x = bond_angle_float_features[:, 1:]
                out_embed += self.linear_list[i](x)
                break
        return out_embed


# 仅在 torch_geometric 可用时才定义 MessagePassing 子类
if _HAS_TORCH_GEOMETRIC:

    class GINConv(MessagePassing):
        """
        GIN (Graph Isomorphism Network) 图卷积层

        公式: h' = MLP((1 + eps) * h + SUM(ReLU(h_j + edge_attr)))

        Args:
            emb_dim: 节点嵌入维度
        """

        def __init__(self, emb_dim):
            super().__init__(aggr="add")
            self.mlp = nn.Sequential(
                nn.Linear(emb_dim, emb_dim),
                nn.BatchNorm1d(emb_dim),
                nn.ReLU(),
                nn.Linear(emb_dim, emb_dim))
            self.eps = nn.Parameter(torch.Tensor([0]))

        def forward(self, x, edge_index, edge_attr):
            edge_embedding = edge_attr
            out = self.mlp(
                (1 + self.eps) * x
                + self.propagate(edge_index, x=x, edge_attr=edge_embedding))
            return out

        def message(self, x_j, edge_attr):
            return F.relu(x_j + edge_attr)

        def update(self, aggr_out):
            return aggr_out

    class GINNodeEmbedding(nn.Module):
        """
        GIN 节点嵌入模块

        多层 GINConv + 键编码器，输出节点和边的嵌入表示

        Args:
            num_layers: GIN 层数
            emb_dim: 嵌入维度
            drop_ratio: Dropout 比率
            JK: Jumping Knowledge 模式 ("last" / "sum")
            residual: 是否使用残差连接
        """

        def __init__(self, num_layers, emb_dim, drop_ratio=0.5, JK="last",
                     residual=False,
                     bond_float_names=None,
                     bond_angle_float_names=None):
            super().__init__()
            self.num_layers = num_layers
            self.drop_ratio = drop_ratio
            self.JK = JK
            self.residual = residual

            if bond_float_names is None:
                bond_float_names = BOND_FLOAT_NAMES
            if bond_angle_float_names is None:
                bond_angle_float_names = BOND_ANGLE_FLOAT_NAMES
            self.bond_float_names = bond_float_names
            self.bond_angle_float_names = bond_angle_float_names

            if self.num_layers < 2:
                raise ValueError("Number of GNN layers must be greater than 1.")

            self.atom_encoder = AtomEncoder(emb_dim)
            self.bond_encoder = BondEncoder(emb_dim)
            self.bond_float_encoder = BondFloatRBF(bond_float_names, emb_dim)
            self.bond_angle_encoder = BondAngleFloatRBF(bond_angle_float_names, emb_dim)

            self.convs = nn.ModuleList()
            self.convs_bond_angle = nn.ModuleList()
            self.convs_bond_embeding = nn.ModuleList()
            self.convs_bond_float = nn.ModuleList()
            self.convs_angle_float = nn.ModuleList()
            self.batch_norms = nn.ModuleList()
            self.batch_norms_ba = nn.ModuleList()
            for layer in range(num_layers):
                self.convs.append(GINConv(emb_dim))
                self.convs_bond_angle.append(GINConv(emb_dim))
                self.convs_bond_embeding.append(BondEncoder(emb_dim))
                self.convs_bond_float.append(BondFloatRBF(bond_float_names, emb_dim))
                self.convs_angle_float.append(BondAngleFloatRBF(bond_angle_float_names, emb_dim))
                self.batch_norms.append(nn.BatchNorm1d(emb_dim))
                self.batch_norms_ba.append(nn.BatchNorm1d(emb_dim))

        def forward(self, batched_atom_bond, batched_bond_angle):
            x = batched_atom_bond.x
            edge_index = batched_atom_bond.edge_index
            edge_attr = batched_atom_bond.edge_attr
            edge_index_ba = batched_bond_angle.edge_index
            edge_attr_ba = batched_bond_angle.edge_attr

            n_bond_id = len(FULL_BOND_ID_NAMES)

            # 初始节点嵌入
            h_list = [self.atom_encoder(x)]

            # 几何增强路径（初始边嵌入）
            h_list_ba = [self.bond_float_encoder(
                edge_attr[:, n_bond_id:].to(torch.float32))
                + self.bond_encoder(edge_attr[:, 0:n_bond_id].to(torch.int64))]

            for layer in range(self.num_layers):
                h = self.convs[layer](h_list[layer], edge_index, h_list_ba[layer])
                cur_h_ba = (
                    self.convs_bond_embeding[layer](
                        edge_attr[:, 0:n_bond_id].to(torch.int64))
                    + self.convs_bond_float[layer](
                        edge_attr[:, n_bond_id:].to(torch.float32)))
                cur_angle_hidden = self.convs_angle_float[layer](edge_attr_ba)
                h_ba = self.convs_bond_angle[layer](
                    cur_h_ba, edge_index_ba, cur_angle_hidden)

                if layer == self.num_layers - 1:
                    h = F.dropout(h, self.drop_ratio, training=self.training)
                    h_ba = F.dropout(h_ba, self.drop_ratio, training=self.training)
                else:
                    h = F.dropout(F.relu(h), self.drop_ratio, training=self.training)
                    h_ba = F.dropout(F.relu(h_ba), self.drop_ratio, training=self.training)
                if self.residual:
                    h += h_list[layer]
                    h_ba += h_list_ba[layer]

                h_list.append(h)
                h_list_ba.append(h_ba)

            if self.JK == "last":
                node_representation = h_list[-1]
            elif self.JK == "sum":
                node_representation = 0
                for layer in range(self.num_layers + 1):
                    node_representation += h_list[layer]

            return node_representation

    class GINGraphPooling(nn.Module):
        """
        GIN 图池化模型 — HPLC 保留时间预测的完整 GeoGNN 模型

        使用 GINNodeEmbedding 嵌入节点，再通过图池化得到图的表示，
        最后经线性层输出预测值（三个任务：10% 分位 / 中位数 / 90% 分位保留时间）。

        Args:
            num_tasks: 预测任务数（默认 3：10% / 中位数 / 90%）
            num_layers: GINConv 层数
            emb_dim: 嵌入维度
            residual: 残差连接
            drop_ratio: Dropout 比率
            JK: Jumping Knowledge 模式 ("last" / "sum")
            graph_pooling: 图池化方式 ("sum", "mean", "max", "attention", "set2set")
            descriptor_dim: Mordred 描述符维度
        """

        def __init__(self, num_tasks=3, num_layers=5, emb_dim=128,
                     residual=False, drop_ratio=0, JK="last",
                     graph_pooling="sum", descriptor_dim=1827,
                     bond_float_names=None,
                     bond_angle_float_names=None,
                     use_relu_output=False):
            super().__init__()

            self.num_layers = num_layers
            self.drop_ratio = drop_ratio
            self.JK = JK
            self.emb_dim = emb_dim
            self.num_tasks = num_tasks
            self.graph_pooling = graph_pooling
            self.descriptor_dim = descriptor_dim

            if self.num_layers < 2:
                raise ValueError("Number of GNN layers must be greater than 1.")

            self.gnn_node = GINNodeEmbedding(
                num_layers, emb_dim, JK=JK, drop_ratio=drop_ratio,
                residual=residual,
                bond_float_names=bond_float_names,
                bond_angle_float_names=bond_angle_float_names)

            if graph_pooling == "sum":
                self.pool = global_add_pool
            elif graph_pooling == "mean":
                self.pool = global_mean_pool
            elif graph_pooling == "max":
                self.pool = global_max_pool
            elif graph_pooling == "attention":
                self.pool = GlobalAttention(gate_nn=nn.Sequential(
                    nn.Linear(emb_dim, emb_dim),
                    nn.BatchNorm1d(emb_dim),
                    nn.ReLU(),
                    nn.Linear(emb_dim, 1)))
            elif graph_pooling == "set2set":
                self.pool = Set2Set(emb_dim, processing_steps=2)
            else:
                raise ValueError("Invalid graph pooling type.")

            if use_relu_output:
                self.graph_pred_linear = nn.Sequential(
                    nn.Linear(self.emb_dim, self.num_tasks),
                    nn.ReLU())
            else:
                self.graph_pred_linear = nn.Linear(self.emb_dim, self.num_tasks)

            self.NN_descriptor = nn.Sequential(
                nn.Linear(self.descriptor_dim, self.emb_dim),
                nn.Sigmoid(),
                nn.Linear(self.emb_dim, self.emb_dim))

            self.sigmoid = nn.Sigmoid()

        def forward(self, batched_atom_bond, batched_bond_angle):
            h_node = self.gnn_node(batched_atom_bond, batched_bond_angle)
            h_graph = self.pool(h_node, batched_atom_bond.batch)
            output = self.graph_pred_linear(h_graph)
            if self.training:
                return output, h_graph
            else:
                return torch.clamp(output, min=0, max=1e8), h_graph


# ==================== Mordred 描述符（可选依赖） ====================
try:
    from mordred import Calculator, descriptors  # noqa: E402
    _MORDRED_CALC = Calculator(descriptors, ignore_3D=False)
    _HAS_MORDRED = True
except ImportError:
    _MORDRED_CALC = None
    _HAS_MORDRED = False


def _compute_mordred_descriptor(mol, nBits=1826, errors_as_zeros=True):
    """计算分子的 Mordred 描述符向量，不可用时返回零向量"""
    if not _HAS_MORDRED:
        return np.zeros((nBits,), dtype=np.float32)
    try:
        from mordred import is_missing
        result = _MORDRED_CALC(mol)
        desc_list = [r if not is_missing(r) else 0 for r in result]
        return np.array(desc_list)
    except Exception:
        return (np.NaN if not errors_as_zeros
                else np.zeros((nBits,), dtype=np.float32))


# ==================== GeoGNN 预测函数（运行时检查 torch_geometric） ====================
def cal_prob(prediction):
    """
    计算分离概率 Sp

    基于两个化合物的 10%/90% 分位预测区间，计算分离概率：
    - 如果两个区间无重叠 → Sp = 1（完全分离）
    - 如果有重叠 → Sp = 1 - (重叠长度 / 总长度)

    Args:
        prediction: [[pred_1], [pred_2]], 每个 pred 为 shape (1, 3)
                    三列对应 10%/中位数/90% 分位保留时间

    Returns:
        Sp: 分离概率 (0~1)
    """
    a = prediction[0][0]
    b = prediction[1][0]
    if a[2] < b[0]:
        return 1.0
    elif a[0] > b[2]:
        return 1.0
    else:
        length = min(a[2], b[2]) - max(a[0], b[0])
        all_len = max(a[2], b[2]) - min(a[0], b[0])
        return 1.0 - length / all_len


def hplc_predict_with_geognn(local_model, column_descriptor,
                              smiles, eluent, speed, column_list,
                              device="cpu"):
    """
    使用 GeoGNN 模型对多色谱柱进行 HPLC 保留时间预测

    Args:
        local_model: GINGraphPooling 模型实例（需已加载权重并置于 eval 模式）
        column_descriptor: 色谱柱描述符数组 (shape: [25, 1826])
        smiles: 两个化合物的 SMILES，逗号分隔 (如 "N[C@@H](C)C(O)=O,N[C@H](C)C(O)=O")
        eluent: 流动相比例 (如 0.1 = 10%)
        speed: 流速 (mL/min)
        column_list: 候选色谱柱名称列表（如 ["ADH", "IA", "OJH"]）
        device: 推理设备。默认 cpu；可显式传入 "cuda:0" 或非负整数启用 GPU。

    Returns:
        sp_list: 各色谱柱的分离概率
        rt_list: 各色谱柱的保留时间对 [[rt1, rt2], ...]

    Raises:
        ImportError: 缺少 torch_geometric 或 mordred
        ValueError: 参数错误（SMILES 无效、未知色谱柱等）
    """
    if not _HAS_TORCH_GEOMETRIC:
        raise ImportError(
            "需要 torch_geometric 库才能使用 GeoGNN 模型预测。"
            "请安装: pip install torch_geometric")

    if not _HAS_MORDRED:
        raise ImportError(
            "需要 mordred 库才能使用 GeoGNN 模型预测。"
            "请安装: pip install mordred")

    import torch
    from torch_geometric.data import Data  # noqa: F811

    smiles_list = [s.strip() for s in smiles.split(",")]
    if len(smiles_list) != 2:
        raise ValueError("需要提供两个化合物的 SMILES，用逗号分隔")

    for c in column_list:
        if c not in COLUMN_SPECIFY:
            raise ValueError(f"未知色谱柱: {c}。已知选项: {COLUMN_NAMES}")

    if isinstance(device, str):
        torch_device = torch.device(device if device.startswith("cuda") and torch.cuda.is_available() else "cpu")
    else:
        torch_device = torch.device(f"cuda:{device}" if torch.cuda.is_available() and device >= 0 else "cpu")
    local_model.to(torch_device)

    sp_list = []
    rt_list = []

    for predict_column in column_list:
        col_specify = COLUMN_SPECIFY[predict_column]
        col_des = np.array(column_descriptor[col_specify[3]])

        all_descriptor = []
        dataset = []
        for smile in smiles_list:
            mol = obtain_3D_mol(smile, "conform")
            mol = Chem.MolFromMolFile("conform.mol")
            all_descriptor.append(_compute_mordred_descriptor(mol))
            dataset.append(mol_to_geognn_graph_data_MMFF3d(mol))

        y_pred = []
        for i in range(len(dataset)):
            data = dataset[i]
            atom_feature_list = []
            bond_feature_list = []
            for name in ATOM_ID_NAMES:
                atom_feature_list.append(data[name])
            for name in BOND_ID_NAMES:
                bond_feature_list.append(data[name])
            atom_feature = torch.from_numpy(
                np.array(atom_feature_list).T).to(torch.int64)
            bond_feature = torch.from_numpy(
                np.array(bond_feature_list).T).to(torch.int64)
            bond_float_feature = torch.from_numpy(
                data["bond_length"].astype(np.float32))
            bond_angle_feature = torch.from_numpy(
                data["bond_angle"].astype(np.float32))
            y = torch.tensor([float(speed)], dtype=torch.float32)
            edge_index = torch.from_numpy(data["edges"].T).to(torch.int64)
            bond_index = torch.from_numpy(
                data["BondAngleGraph_edges"].T).to(torch.int64)

            prop = torch.full([bond_feature.shape[0]], float(eluent), dtype=torch.float32)
            coated = torch.full([bond_feature.shape[0]], float(col_specify[0]), dtype=torch.float32)
            diameter = torch.full([bond_feature.shape[0]], float(col_specify[1]), dtype=torch.float32)
            immobilized = torch.full([bond_feature.shape[0]], float(col_specify[2]), dtype=torch.float32)

            TPSA = torch.full([bond_angle_feature.shape[0]], float(all_descriptor[i][820]) / 100, dtype=torch.float32)
            RASA = torch.full([bond_angle_feature.shape[0]], float(all_descriptor[i][821]), dtype=torch.float32)
            RPSA = torch.full([bond_angle_feature.shape[0]], float(all_descriptor[i][822]), dtype=torch.float32)
            MDEC = torch.full([bond_angle_feature.shape[0]], float(all_descriptor[i][1568]), dtype=torch.float32)
            MATS = torch.full([bond_angle_feature.shape[0]], float(all_descriptor[i][457]), dtype=torch.float32)

            col_TPSA = torch.full([bond_angle_feature.shape[0]], float(col_des[820]) / 100, dtype=torch.float32)
            col_RASA = torch.full([bond_angle_feature.shape[0]], float(col_des[821]), dtype=torch.float32)
            col_RPSA = torch.full([bond_angle_feature.shape[0]], float(col_des[822]), dtype=torch.float32)
            col_MDEC = torch.full([bond_angle_feature.shape[0]], float(col_des[1568]), dtype=torch.float32)
            col_MATS = torch.full([bond_angle_feature.shape[0]], float(col_des[457]), dtype=torch.float32)

            bond_feature = torch.cat(
                [bond_feature, coated.reshape(-1, 1)], dim=1)
            bond_feature = torch.cat(
                [bond_feature, immobilized.reshape(-1, 1)], dim=1)
            bond_feature = torch.cat(
                [bond_feature, bond_float_feature.reshape(-1, 1)], dim=1)
            bond_feature = torch.cat(
                [bond_feature, prop.reshape(-1, 1)], dim=1)
            bond_feature = torch.cat(
                [bond_feature, diameter.reshape(-1, 1)], dim=1)

            bond_angle_feature = torch.cat(
                [bond_angle_feature.reshape(-1, 1), TPSA.reshape(-1, 1)], dim=1)
            bond_angle_feature = torch.cat(
                [bond_angle_feature, RASA.reshape(-1, 1)], dim=1)
            bond_angle_feature = torch.cat(
                [bond_angle_feature, RPSA.reshape(-1, 1)], dim=1)
            bond_angle_feature = torch.cat(
                [bond_angle_feature, MDEC.reshape(-1, 1)], dim=1)
            bond_angle_feature = torch.cat(
                [bond_angle_feature, MATS.reshape(-1, 1)], dim=1)
            bond_angle_feature = torch.cat(
                [bond_angle_feature, col_TPSA.reshape(-1, 1)], dim=1)
            bond_angle_feature = torch.cat(
                [bond_angle_feature, col_RASA.reshape(-1, 1)], dim=1)
            bond_angle_feature = torch.cat(
                [bond_angle_feature, col_RPSA.reshape(-1, 1)], dim=1)
            bond_angle_feature = torch.cat(
                [bond_angle_feature, col_MDEC.reshape(-1, 1)], dim=1)
            bond_angle_feature = torch.cat(
                [bond_angle_feature, col_MATS.reshape(-1, 1)], dim=1)

            data_atom_bond = Data(atom_feature, edge_index, bond_feature, y)
            data_bond_angle = Data(
                edge_index=bond_index, edge_attr=bond_angle_feature)

            local_model.eval()
            with torch.no_grad():
                pred, _h_graph = local_model(
                    data_atom_bond.to(torch_device),
                    data_bond_angle.to(torch_device))

            y_pred.append(pred.detach().cpu().data.numpy() / speed)

        sp_list.append(cal_prob(y_pred))
        rt_list.append([float(y_pred[0][0][1]), float(y_pred[1][0][1])])

    return sp_list, rt_list


# ==================== CC GeoGNN 预测 ====================

CC_SELECTED_MORDRED_INDICES = [153, 278, 884, 885, 1273, 1594, 431, 1768, 1769, 1288, 1521]
CC_DESCRIPTOR_TOTAL_DIM = 16


def _cc_get_descriptor(smiles, ratio):
    compound_mol = Chem.MolFromSmiles(smiles)
    descriptor = []
    descriptor.append(Descriptors.ExactMolWt(compound_mol))
    descriptor.append(Chem.rdMolDescriptors.CalcTPSA(compound_mol))
    descriptor.append(Descriptors.NumRotatableBonds(compound_mol))
    descriptor.append(Descriptors.NumHDonors(compound_mol))
    descriptor.append(Descriptors.NumHAcceptors(compound_mol))
    descriptor.append(Descriptors.MolLogP(compound_mol))
    descriptor = np.array(descriptor, dtype=np.float32) * ratio
    return descriptor


def _cc_get_eluent_descriptor(eluent_array):
    des = np.zeros([6], dtype=np.float32)
    for i in range(eluent_array.shape[0]):
        if eluent_array[i] != 0:
            e_descriptors = _cc_get_descriptor(CC_ELUENT_SMILES[i], eluent_array[i])
            des += e_descriptors
    return des


def _cc_convert_e(e):
    if e == 'PE':
        return 0
    elif e == 'EA':
        return 1
    elif e == 'DCM':
        return 2
    else:
        return 0


def _cc_convert_eluent(eluent_str):
    parts = eluent_str.strip().split('/')
    PE = int(parts[0])
    EA = int(parts[1])
    ratio = np.array([PE, EA, 0, 0, 0], dtype=np.float32)
    total = PE + EA
    if total > 0:
        ratio = ratio / total
    return _cc_get_eluent_descriptor(ratio).reshape(1, -1)


def _cc_load_norm_params():
    try:
        base = SERVICES_DIR / "column_chromatography_prediction-main" / "dataset_save"
        X_max_eluent = np.load(str(base / "X_max_eluent.npy"))
        X_min_eluent = np.load(str(base / "X_min_eluent.npy"))
        X_max_descriptor = np.load(str(base / "X_max_descriptor.npy"))
        X_min_descriptor = np.load(str(base / "X_min_descriptor.npy"))
        return X_max_eluent, X_min_eluent, X_max_descriptor, X_min_descriptor
    except (FileNotFoundError, OSError):
        return None, None, None, None


def _cc_prepare_descriptor_block(smiles_list, descriptor_override=None):
    all_descriptors = []
    mol_list = []
    for index, smi in enumerate(smiles_list):
        mol = obtain_3D_mol(smi)
        if mol is None:
            raise ValueError(f"无法生成 3D 构象: {smi}")
        mol_list.append(mol)
        if descriptor_override is not None:
            all_descriptors.append(np.asarray(descriptor_override[index], dtype=np.float32))
            continue
        basic_desc = [
            Descriptors.MolWt(mol),
            Descriptors.NumRotatableBonds(mol),
            Descriptors.NumHDonors(mol),
            Descriptors.NumHAcceptors(mol),
            Descriptors.MolLogP(mol),
        ]
        mordred_desc = _compute_mordred_descriptor(mol, nBits=1827)
        selected_mordred = [mordred_desc[i] for i in CC_SELECTED_MORDRED_INDICES]
        all_descriptors.append(np.array(basic_desc + selected_mordred, dtype=np.float32))
    return mol_list, np.stack(all_descriptors, axis=0)


def _cc_build_graph_pair(smiles, eluent, e, m, V_e, descriptor_override=None):
    import torch
    from torch_geometric.data import Data

    e_x = np.array([_cc_convert_e(e)], dtype=np.float32).reshape(-1, 1)
    m_x = np.array([m], dtype=np.float32).reshape(-1, 1)
    V_e_x = np.array([V_e], dtype=np.float32).reshape(-1, 1)

    eluent_descriptor = _cc_convert_eluent(eluent)
    X_max_eluent, X_min_eluent, X_max_descriptor, X_min_descriptor = _cc_load_norm_params()
    has_norm = X_max_eluent is not None
    if has_norm:
        eluent_descriptor_norm = (eluent_descriptor - X_min_eluent) / (X_max_eluent - X_min_eluent + 1e-8)
    else:
        eluent_descriptor_norm = eluent_descriptor

    smiles_list = [s.strip() for s in smiles.split(",")]
    if len(smiles_list) != 2:
        raise ValueError("CC prediction requires exactly 2 SMILES, comma-separated")

    mol_list, all_descriptors = _cc_prepare_descriptor_block(smiles_list, descriptor_override=descriptor_override)
    if has_norm:
        all_descriptors = (all_descriptors - X_min_descriptor) / (X_max_descriptor - X_min_descriptor + 1e-8)

    graph_data_list = []
    for idx, smi in enumerate(smiles_list):
        atom_bond_graph = mol_to_geognn_graph_data_MMFF3d(mol_list[idx])
        if atom_bond_graph is None:
            raise ValueError(f"无法生成图数据: {smi}")

        atom_feature_list = []
        bond_feature_list = []
        for name in ATOM_ID_NAMES:
            atom_feature_list.append(atom_bond_graph[name])
        for name in BOND_ID_NAMES:
            bond_feature_list.append(atom_bond_graph[name])

        atom_feature = torch.from_numpy(np.array(atom_feature_list).T).to(torch.int64)
        bond_feature = torch.from_numpy(np.array(bond_feature_list).T).to(torch.int64)
        bond_index = torch.from_numpy(atom_bond_graph['edges'].T).to(torch.int64)
        bond_float_feature = torch.from_numpy(atom_bond_graph['bond_length'].astype(np.float32))
        bond_angle_feature = torch.from_numpy(atom_bond_graph['bond_angle'].astype(np.float32)).reshape(-1, 1)
        bond_angle_index = torch.from_numpy(atom_bond_graph['BondAngleGraph_edges'].T).to(torch.int64)

        num_bonds = bond_feature.shape[0]
        prop = torch.ones([num_bonds, 6], dtype=torch.float32) * torch.from_numpy(eluent_descriptor_norm.astype(np.float32))
        bond_feature = torch.cat([bond_feature, bond_float_feature.reshape(-1, 1)], dim=1)
        bond_feature = torch.cat([bond_feature, prop], dim=1)
        bond_feature = torch.cat([bond_feature, torch.from_numpy(e_x).expand(num_bonds, 1)], dim=1)
        bond_feature = torch.cat([bond_feature, torch.from_numpy(m_x).expand(num_bonds, 1)], dim=1)
        bond_feature = torch.cat([bond_feature, torch.from_numpy(V_e_x).expand(num_bonds, 1)], dim=1)

        desc_tensor = torch.from_numpy(all_descriptors[idx:idx + 1].astype(np.float32))
        bond_angle_feature = torch.cat([bond_angle_feature, desc_tensor.expand(bond_angle_feature.shape[0], -1)], dim=1)

        data_atom_bond = Data(x=atom_feature, edge_index=bond_index, edge_attr=bond_feature)
        data_bond_angle = Data(
            edge_index=bond_angle_index,
            edge_attr=bond_angle_feature,
            num_nodes=bond_feature.shape[0],
        )
        graph_data_list.append((data_atom_bond, data_bond_angle))

    return graph_data_list


def _cc_load_geognn_model(device="cpu"):
    import torch
    model_path = str(CC_TORCH_MODEL_PATH)
    if not os.path.exists(model_path):
        raise FileNotFoundError(
            f"CC GeoGNN 模型文件不存在: {model_path}。"
            "请先训练模型或复制预训练权重。")

    nn_params = {
        'num_tasks': 6,
        'num_layers': 5,
        'emb_dim': 128,
        'drop_ratio': 0.0,
        'JK': "last",
        'graph_pooling': "sum",
        'descriptor_dim': 1827,
        'bond_float_names': CC_BOND_FLOAT_NAMES,
        'bond_angle_float_names': CC_BOND_ANGLE_FLOAT_NAMES,
        'use_relu_output': True,
    }

    model = GINGraphPooling(**nn_params)
    checkpoint = torch.load(model_path, map_location=torch.device('cpu'))
    state_dict = checkpoint.get('model_state_dict', checkpoint)
    model.load_state_dict(state_dict, strict=False)
    model.to(device)
    model.eval()
    return model


def cc_predict_with_geognn(smiles, eluent, e=None, m=None, V_e=None, device="cpu", model=None):
    if not _HAS_TORCH_GEOMETRIC:
        raise ImportError(
            "需要 torch_geometric 库才能使用 CC GeoGNN 模型预测。"
            "请安装: pip install torch_geometric")

    if not _HAS_MORDRED:
        raise ImportError(
            "需要 mordred 库才能使用 CC GeoGNN 模型预测。"
            "请安装: pip install mordred")

    import torch
    from torch_geometric.data import Data

    if model is None:
        model = _cc_load_geognn_model(device)

    if e is None:
        e = 'PE'
    if m is None:
        m = 4
    if V_e is None:
        V_e = 40

    torch_device = torch.device(device)
    graph_data_list = _cc_build_graph_pair(smiles, eluent, e, m, V_e)
    predictions = []
    with torch.no_grad():
        for data_atom_bond, data_bond_angle in graph_data_list:
            pred, _h_graph = model(
                data_atom_bond.to(torch_device),
                data_bond_angle.to(torch_device),
            )
            predictions.append(pred.detach().cpu().numpy())

    rt_a = max(0.0, float(predictions[0][0][1]))
    rt_b = max(0.0, float(predictions[1][0][1]))
    interval_a = [[
        max(0.0, float(predictions[0][0][0])),
        max(0.0, float(predictions[0][0][1])),
        max(0.0, float(predictions[0][0][2])),
    ]]
    interval_b = [[
        max(0.0, float(predictions[1][0][0])),
        max(0.0, float(predictions[1][0][1])),
        max(0.0, float(predictions[1][0][2])),
    ]]

    sp = cal_prob([interval_a, interval_b])
    rt_list = [round(rt_a, 4), round(rt_b, 4)]

    return sp, rt_list
