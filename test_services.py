"""
测试脚本 - 验证三个预测服务(TLC/CC/HPLC)是否正常运行
发送测试请求并打印返回结果
"""

import requests
import json


def test_services():
    """依次测试 TLC、CC、HPLC 三个预测服务"""
    print("=" * 50)
    print("测试预测服务连接")
    print("=" * 50)
    
    # ========== 测试 TLC 服务 ==========
    print("\n[1] 测试 TLC 服务 (端口 8000)...")
    try:
        # 发送POST请求：预测酮类化合物的Rf值
        response = requests.post(
            "http://127.0.0.1:8000/predict",
            json={
                "smiles": "O=C(C1=CC=CC=C1)CCCl",    # 化合物SMILES
                "eluent_ratios": [0.98, 0.02, 0, 0, 0]  # 洗脱剂比例（己烷:乙酸乙酯=49:1）
            },
            timeout=5
        )
        print(f"   [OK] TLC服务正常: {response.json()}")
    except Exception as e:
        print(f"   [ERROR] TLC服务异常: {e}")
    
    # ========== 测试 CC 服务 ==========
    print("\n[2] 测试 CC 服务 (端口 8001)...")
    try:
        # 发送POST请求：预测两个化合物的柱色谱分离效果
        response = requests.post(
            "http://127.0.0.1:8001/predict",
            json={
                "smiles_list": ["CC(C)CCOC(=O)C1=CC=CC=C1", "COC(=O)C1=CC=CC=C1"],  # 两个化合物SMILES
                "eluent_system": "PE/EA 40/10",   # 石油醚/乙酸乙酯 40:10
                "m_param": 4.0,          # 4g硅胶柱
                "speed": 8.0             # 流速 8.0 mL/min
            },
            timeout=5
        )
        print(f"   [OK] CC服务正常: {response.json()}")
    except Exception as e:
        print(f"   [ERROR] CC服务异常: {e}")
    
    # ========== 测试 HPLC 服务 ==========
    print("\n[3] 测试 HPLC 服务 (端口 8002)...")
    try:
        # 发送POST请求：预测两个化合物的HPLC保留时间
        response = requests.post(
            "http://127.0.0.1:8002/predict",
            json={
                "smiles": "CCO,CCCO",        # 乙醇和丙醇的SMILES
                "eluent": 0.1,               # 流动相比例0.1
                "speed": 1.0,                # 流速 1.0 mL/min
                "columns": "ADH,IA"          # 候选色谱柱
            },
            timeout=5
        )
        print(f"   [OK] HPLC服务正常: {response.json()}")
    except Exception as e:
        print(f"   [ERROR] HPLC服务异常: {e}")
    
    print("\n" + "=" * 50)
    print("测试完成")
    print("=" * 50)


if __name__ == "__main__":
    test_services()