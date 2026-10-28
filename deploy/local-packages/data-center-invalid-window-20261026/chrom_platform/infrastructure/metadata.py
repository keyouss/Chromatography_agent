"""Import model registries and compose build-specific metadata."""

from sqlalchemy import ForeignKeyConstraint, MetaData

from .db import Base
from .models import AuditEvent, Confirmation, ExecutionLedger, IdempotencyRecord, Job, JobAttempt, JobEvent, OutboxEvent, Project
from chrom_platform.artifact_core.models import Artifact, Structure

__all__ = [
    "Base",
    "Project",
    "Artifact",
    "Structure",
    "AuditEvent",
    "Confirmation",
    "Job",
    "JobAttempt",
    "JobEvent",
    "OutboxEvent",
    "IdempotencyRecord",
    "ExecutionLedger",
    "compose_metadata",
    "m2_metadata",
]


def compose_metadata(*registries: MetaData) -> MetaData:
    """Copy model tables into one registry and fail on duplicate ownership."""

    result = MetaData(naming_convention=Base.metadata.naming_convention)
    seen: set[str] = set()
    for registry in registries:
        for name, table in registry.tables.items():
            if name in seen:
                raise ValueError(f"duplicate table ownership: {name}")
            table.to_metadata(result)
            seen.add(name)
    return result


def m2_metadata() -> MetaData:
    """Return the M1 + M2 registry used by a future M2 assembly/migration check."""

    from chrom_platform.modules.data_center.persistence import DataCenterBase
    from chrom_platform.modules.prediction.persistence import PredictionBase
    # Importing the batch models registers their tables on the shared M1 Base.
    from chrom_platform.modules.prediction import batch_service as _batch_service  # noqa: F401

    result = compose_metadata(Base.metadata, DataCenterBase.metadata, PredictionBase.metadata)
    # This is the one cross-module relationship.  The prediction module only
    # sees the public staging port; assembly adds the FK once both registries
    # are present, keeping each module's ORM metadata independently mappable.
    result.tables["prediction"].append_constraint(ForeignKeyConstraint(
        ("prediction_record_id",),
        (result.tables["prediction_record"].c.id,),
        name="fk_prediction_record_id_prediction_record",
        ondelete="RESTRICT",
    ))
    # The record projection also keeps the optional back-reference declared
    # in the M2 migration.  Keeping both directions in the composed registry
    # prevents Alembic and ORM metadata from silently diverging.
    result.tables["prediction_record"].append_constraint(ForeignKeyConstraint(
        ("prediction_id",),
        (result.tables["prediction"].c.id,),
        name="fk_prediction_record_prediction_id_prediction",
        ondelete="RESTRICT",
    ))
    return result
