"""Strict internal contracts for the prediction module.

The module intentionally has no router or default adapter registration.  A
build must explicitly register the adapters and model contracts it actually
ships before a capability becomes visible.
"""

from .adapters import (
    AdapterCapability,
    AdapterManifest,
    AdapterProtocol,
    CapabilityRegistry,
    ModelUnavailableError,
    ModelVerificationEvidence,
    PredictionAdapter,
    PredictionAdapterProtocol,
    PredictionContractError,
    VerifiedModelHandle,
    issue_verified_model_handle,
)
from .batch import *  # noqa: F401,F403
from .batch import __all__ as _BATCH_EXPORTS
from .ports import *  # noqa: F401,F403
from .ports import __all__ as _PORT_EXPORTS
from .schemas import *  # noqa: F401,F403
from .schemas import __all__ as _SCHEMA_EXPORTS
from .service import PredictionCreateService, PredictionService, PredictionServiceError
from .persistence import (
    PredictionBase,
    PredictionExecutionSnapshot,
    PredictionPersistenceUnavailable,
    PredictionRecordRow,
    PredictionRow,
    PredictionAtomicAdapter,
    SQLAlchemyPredictionRepository,
    SQLAlchemyPredictionPersistence,
)
from .router import create_router
from .worker import PredictionWorker, PredictionWorkerHandler, WorkerResult, handle_prediction_job

__all__ = [
    "AdapterManifest",
    "AdapterCapability",
    "AdapterProtocol",
    "CapabilityRegistry",
    "ModelUnavailableError",
    "ModelVerificationEvidence",
    "PredictionAdapter",
    "PredictionAdapterProtocol",
    "PredictionContractError",
    "VerifiedModelHandle",
    "issue_verified_model_handle",
    "PredictionCreateService",
    "PredictionService",
    "PredictionServiceError",
    "PredictionBase",
    "PredictionExecutionSnapshot",
    "PredictionPersistenceUnavailable",
    "PredictionRecordRow",
    "PredictionRow",
    "PredictionAtomicAdapter",
    "SQLAlchemyPredictionRepository",
    "SQLAlchemyPredictionPersistence",
    "create_router",
    "PredictionWorker",
    "PredictionWorkerHandler",
    "WorkerResult",
    "handle_prediction_job",
    *_BATCH_EXPORTS,
    *_PORT_EXPORTS,
    *_SCHEMA_EXPORTS,
]
