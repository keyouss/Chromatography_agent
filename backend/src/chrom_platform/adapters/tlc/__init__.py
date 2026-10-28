"""受控 TLC Rf 科学适配器。

本包只提供显式构造的 TLC runtime/adapter；不会从客户端输入或源码目录
发现模型路径。模型文件仍由构建装配提供，不复制到仓库。
"""

from .adapter import TlcPredictionAdapter
from .manifest import (
    TLC_ARTIFACT_ID,
    TLC_ARTIFACT_MANIFEST_SHA256,
    TLC_MODEL_VERSION,
    TLC_SCOPE_ID,
    TLC_SOURCE_SHA256,
    TlcArtifactManifest,
)
from .runtime import (
    TlcArtifactUnavailable,
    TlcModelRoot,
    TlcRuntime,
    TlcRuntimeStatus,
)
from .service import (
    TlcBatchRow,
    TlcBatchRowResult,
    TlcConditionResult,
    TlcPredictionService,
    TlcReverseCandidate,
    TlcReverseResult,
)
from .schema import (
    TlcConditionComparisonCreate,
    TlcConditionComparisonResult,
    TlcReverseCreate,
    TlcReverseResult as TlcReverseHttpResult,
)

__all__ = [
    "TLC_ARTIFACT_ID",
    "TLC_ARTIFACT_MANIFEST_SHA256",
    "TLC_MODEL_VERSION",
    "TLC_SCOPE_ID",
    "TLC_SOURCE_SHA256",
    "TlcArtifactManifest",
    "TlcArtifactUnavailable",
    "TlcModelRoot",
    "TlcPredictionAdapter",
    "TlcRuntime",
    "TlcRuntimeStatus",
    "TlcBatchRow",
    "TlcBatchRowResult",
    "TlcConditionResult",
    "TlcPredictionService",
    "TlcReverseCandidate",
    "TlcReverseResult",
    "TlcConditionComparisonCreate",
    "TlcConditionComparisonResult",
    "TlcReverseCreate",
    "TlcReverseHttpResult",
]
