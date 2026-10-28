"""Application service for the fixed-template experiment lifecycle."""

from __future__ import annotations

from datetime import datetime, timezone
from contextlib import ExitStack, nullcontext
import hashlib
import json
from typing import Any, Mapping

from .errors import (
    ConfirmationRequiredError,
    TrainingError,
    TrainingConflictError,
    TrainingNotFoundError,
    TrainingPreconditionError,
    TrainingUnavailableError,
    TrainingValidationError,
)
from .persistence import StaticTrainingTemplateRegistry, new_id, now_utc
from .ports import CandidateRegistrationPort, ConfirmationPort, DataVersionPort, EvaluationProtocolPort, FeatureVersionPort, TrainingRepository, TrainingTemplateRegistry
from .schemas import (
    EmptyRequest,
    Experiment,
    ExperimentAccepted,
    ExperimentComparison,
    ExperimentComparisonMetric,
    ExperimentComparisonMetricValue,
    ExperimentComparisonRequest,
    ExperimentCreate,
    ExperimentMetrics,
    ExperimentPatch,
    ExperimentValidation,
    TrainingEventPage,
    LogEntry,
    TrainingArtifact,
    TrainingEvent,
    ResourceBudget,
    SubmitRequest,
    TrainingExperimentSpec,
    TrainingJob,
)


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def config_hash(spec: TrainingExperimentSpec) -> str:
    return hashlib.sha256(_canonical(spec.model_dump(mode="json")).encode("utf-8")).hexdigest()


def _value(value: object, key: str, default: object = None) -> object:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _ready_immutable(value: object | None) -> bool:
    if value is None or _value(value, "status") != "READY" or not _value(value, "id"):
        return False
    immutable = _value(value, "immutable", None)
    if immutable is not None and immutable is not True:
        return False
    return _value(value, "is_mutable", False) is not True


def _ready_feature(value: object | None) -> bool:
    """Feature versions have no status in the shared DTO, but reject explicit mutable states."""

    if value is None or not _value(value, "id"):
        return False
    status = _value(value, "status", None)
    if status is not None and status != "READY":
        return False
    immutable = _value(value, "immutable", None)
    return immutable is not False and _value(value, "is_mutable", False) is not True


def _protocol_approved(value: object | None) -> bool:
    return value is not None and _value(value, "status") == "APPROVED"


def _parameters_allowed(parameters: Mapping[str, Any], schema: object) -> bool:
    """Template schemas are the only source of accepted hyperparameter names."""

    if not parameters:
        return True
    if not isinstance(schema, Mapping):
        return False
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return False
    return set(parameters).issubset({str(key) for key in properties})


def _port_get(port: object, primary: str, fallback: str, *args: object) -> object | None:
    method = getattr(port, primary, None) or getattr(port, fallback, None)
    if not callable(method):
        raise TrainingUnavailableError(f"training dependency does not expose {primary}")
    return method(*args)


def _within_budget(value: ResourceBudget, limit: ResourceBudget) -> bool:
    return value.cpu_cores <= limit.cpu_cores and value.memory_mb <= limit.memory_mb and value.gpu_count <= limit.gpu_count and value.max_runtime_seconds <= limit.max_runtime_seconds


class TrainingService:
    def __init__(
        self,
        repository: TrainingRepository | None = None,
        *,
        templates: TrainingTemplateRegistry | None = None,
        data_versions: DataVersionPort | None = None,
        feature_versions: FeatureVersionPort | None = None,
        evaluation_protocols: EvaluationProtocolPort | None = None,
        confirmation: ConfirmationPort | None = None,
        candidate_registration: CandidateRegistrationPort | None = None,
    ):
        if repository is None:
            raise TrainingUnavailableError("training repository is not configured")
        self.repository = repository
        self.templates = templates
        self.data_versions = data_versions
        self.feature_versions = feature_versions
        self.evaluation_protocols = evaluation_protocols
        self.confirmation = confirmation
        self.candidate_registration = candidate_registration

    def list_templates(self, *, task_type: str | None = None):
        if self.templates is None:
            raise TrainingUnavailableError("training template registry is not configured")
        values = list(self.templates.list(task_type=task_type))
        return values

    def get_template(self, template_id: str, version: str | None = None):
        if self.templates is None:
            raise TrainingUnavailableError("training template registry is not configured")
        value = None
        if version is not None:
            value = self.templates.get(template_id, version)
        else:
            candidates = list(self.templates.list(task_type=None))
            candidates = [item for item in candidates if item.id == template_id]
            if candidates:
                # Registry order is not a version contract; choose the
                # lexically greatest declared version when no version is
                # requested and return that real definition.
                value = sorted(candidates, key=lambda item: item.version)[-1]
        if value is None:
            raise TrainingNotFoundError("training template does not exist")
        return value

    def create(self, request: ExperimentCreate) -> Experiment:
        now = now_utc()
        spec = TrainingExperimentSpec.model_validate(request.model_dump(mode="json"))
        value = Experiment(id=new_id("exp"), revision=1, config_hash=config_hash(spec), etag='"1"', spec=spec, status="DRAFT", validation_report=None, job_id=None, current_attempt=0, candidate_status="NOT_REQUESTED", candidate_model_id=None, created_at=now)
        return self.repository.save_experiment(value)

    def get(self, project_id: str, experiment_id: str) -> Experiment:
        value = self.repository.get_experiment(project_id, experiment_id)
        if value is None:
            raise TrainingNotFoundError("experiment does not exist")
        return value

    def list(self, project_id: str, *, task_type: str | None = None, status: str | None = None):
        return list(self.repository.list_experiments(project_id, task_type=task_type, status=status))

    def events(self, project_id: str, experiment_id: str, *, after_seq: int = 0, limit: int = 100) -> TrainingEventPage:
        current = self.get(project_id, experiment_id)
        reader = getattr(self.repository, "list_events", None)
        rows, has_more = reader(project_id, current.id, after_seq=after_seq, limit=limit) if callable(reader) else ([], False)
        values = [row if isinstance(row, TrainingEvent) else TrainingEvent.model_validate(row) for row in rows]
        next_seq = str(values[-1].seq if values else after_seq)
        return TrainingEventPage(events=values, next_seq=next_seq, has_more=bool(has_more), stream_status="TERMINAL" if current.status in {"SUCCEEDED", "FAILED", "CANCELED", "TIMED_OUT", "INTERRUPTED"} else "OPEN")

    def logs(self, project_id: str, experiment_id: str, *, after_seq: int = 0, limit: int = 100) -> tuple[list[LogEntry], bool]:
        current = self.get(project_id, experiment_id)
        reader = getattr(self.repository, "list_logs", None)
        rows, has_more = reader(project_id, current.id, after_seq=after_seq, limit=limit) if callable(reader) else ([], False)
        return [row if isinstance(row, LogEntry) else LogEntry.model_validate(row) for row in rows], bool(has_more)

    def metrics(self, project_id: str, experiment_id: str, *, attempt: int | None = None, after_step: int | None = None) -> ExperimentMetrics:
        current = self.get(project_id, experiment_id)
        selected = attempt or max(1, current.current_attempt or 1)
        reader = getattr(self.repository, "get_metrics", None)
        if not callable(reader):
            return ExperimentMetrics(experiment_id=current.id, attempt=selected, protocol_snapshot={"id": current.spec.evaluation_protocol.id, "version": current.spec.evaluation_protocol.version}, series=[], final_metrics=[], next_step=None)
        value = reader(project_id, current.id, attempt=selected, after_step=after_step)
        return value if isinstance(value, ExperimentMetrics) else ExperimentMetrics.model_validate(value)

    def artifacts(self, project_id: str, experiment_id: str, *, after_id: str | None = None, limit: int = 50) -> tuple[list[TrainingArtifact], bool]:
        current = self.get(project_id, experiment_id)
        reader = getattr(self.repository, "list_artifacts", None)
        rows, has_more = reader(project_id, current.id, after_id=after_id, limit=limit) if callable(reader) else ([], False)
        return [row if isinstance(row, TrainingArtifact) else TrainingArtifact.model_validate(row) for row in rows], bool(has_more)

    def compare(self, request: ExperimentComparisonRequest, *, project_ids: list[str]) -> ExperimentComparison:
        if len(set(request.experiment_ids)) != len(request.experiment_ids):
            raise TrainingValidationError("experiment_ids must be unique")
        values: list[Experiment] = []
        project_id: str | None = None
        for candidate_project in project_ids:
            found = [self.repository.get_experiment(candidate_project, item) for item in request.experiment_ids]
            if all(item is not None for item in found):
                values = [item for item in found if item is not None]
                project_id = candidate_project
                break
        if project_id is None:
            raise TrainingNotFoundError("experiment does not exist")
        if any(item.status != "SUCCEEDED" for item in values):
            raise TrainingConflictError("only succeeded experiments can be compared")
        if any(item.spec.evaluation_protocol.id != request.protocol_id or item.spec.evaluation_protocol.version != request.protocol_version for item in values):
            raise TrainingValidationError("experiments do not share the requested evaluation protocol")
        data_versions = {item.spec.data_version_id for item in values}
        split_snapshots = {_canonical(item.spec.split.model_dump(mode="json")) for item in values}
        if len(data_versions) != 1 or len(split_snapshots) != 1:
            raise TrainingValidationError("experiments do not share compatible data and split snapshots")
        reader = getattr(self.repository, "comparison_metrics", None)
        if not callable(reader):
            raise TrainingValidationError("comparable evaluation metrics are unavailable")
        rows = [reader(project_id, item.id, protocol_id=request.protocol_id, protocol_version=request.protocol_version) for item in values]
        if any(row is None for row in rows):
            raise TrainingValidationError("comparable evaluation metrics are unavailable")
        metric_keys = sorted(set().union(*(row.keys() for row in rows if row is not None)))
        if not metric_keys:
            raise TrainingValidationError("comparable evaluation metrics are unavailable")
        metrics: list[ExperimentComparisonMetric] = []
        for key in metric_keys:
            metric_values = [ExperimentComparisonMetricValue(resource_id=item.id, value=float(row[key])) for item, row in zip(values, rows) if row is not None and key in row]
            if len(metric_values) != len(values):
                raise TrainingValidationError("experiments have incompatible metric sets")
            metrics.append(ExperimentComparisonMetric(key=key, unit=None, values=metric_values, direction="none"))
        return ExperimentComparison(experiment_ids=list(request.experiment_ids), protocol_snapshot={"id": request.protocol_id, "version": request.protocol_version}, data_snapshot={"data_version_ids": sorted({item.spec.data_version_id for item in values})}, metrics=metrics, warnings=[])

    def patch(self, project_id: str, experiment_id: str, request: ExperimentPatch, *, expected_revision: int | None = None, if_match: str | None = None) -> Experiment:
        current = self.get(project_id, experiment_id)
        if current.spec_locked or current.status not in {"DRAFT", "INVALID", "READY"}:
            raise TrainingConflictError("submitted experiment specification is immutable")
        if expected_revision is not None and current.revision != expected_revision:
            raise TrainingPreconditionError("experiment revision is stale")
        if if_match is not None and current.etag != if_match:
            raise TrainingPreconditionError("experiment etag is stale")
        updates = request.model_dump(exclude_unset=True, mode="python")
        merged = current.spec.model_dump(mode="python")
        merged.update(updates)
        spec = TrainingExperimentSpec.model_validate(merged)
        value = current.model_copy(update={"revision": current.revision + 1, "config_hash": config_hash(spec), "etag": f'"{current.revision + 1}"', "spec": spec, "status": "DRAFT", "validation_report": None, "error": None})
        return self.repository.save_experiment(value)

    def validate(self, project_id: str, experiment_id: str, *, expected_revision: int | None = None, if_match: str | None = None) -> ExperimentValidation:
        current = self.get(project_id, experiment_id)
        if current.spec_locked:
            raise TrainingConflictError("submitted experiment cannot be revalidated")
        if expected_revision is not None and current.revision != expected_revision:
            raise TrainingPreconditionError("experiment revision is stale")
        if if_match is not None and current.etag != if_match:
            raise TrainingPreconditionError("experiment etag is stale")
        errors: list[dict[str, str]] = []
        spec = current.spec
        template = None
        handler = None
        if self.templates is None:
            raise TrainingUnavailableError("training template registry is not configured")
        template = self.templates.get(spec.execution.template_id, spec.execution.template_version)
        if template is None:
            raise TrainingUnavailableError("requested training template or asset is unavailable")
        handler_lookup = getattr(self.templates, "handler", None)
        handler = handler_lookup(spec.execution.template_id, spec.execution.template_version) if callable(handler_lookup) else None
        if handler is None or template.health != "READY":
            raise TrainingUnavailableError("requested training template or asset is unavailable")
        if template.task_type != spec.task_type:
            errors.append({"path": "task_type", "code": "TEMPLATE_TASK_MISMATCH", "message": "template task type does not match experiment"})
        if spec.target_field not in template.allowed_targets:
            errors.append({"path": "target_field", "code": "TARGET_NOT_ALLOWED", "message": "target field is not allowed by template"})
        if spec.feature_version_id not in template.feature_versions:
            errors.append({"path": "feature_version_id", "code": "FEATURE_VERSION_NOT_ALLOWED", "message": "feature version is not allowed by template"})
        if not _parameters_allowed(spec.parameters, template.parameters_schema):
            errors.append({"path": "parameters", "code": "PARAMETERS_NOT_ALLOWED", "message": "parameters are not declared by the selected template"})
        protocol_ids = {str(item.id) for item in template.evaluation_protocols}
        if spec.evaluation_protocol.id not in protocol_ids:
            errors.append({"path": "evaluation_protocol.id", "code": "EVALUATION_PROTOCOL_NOT_ALLOWED", "message": "evaluation protocol is not allowed by template"})
        if self.data_versions is None:
            raise TrainingUnavailableError("data version port is not configured")
        data = _port_get(self.data_versions, "get", "get_data_version", project_id, spec.data_version_id)
        if not _ready_immutable(data):
            errors.append({"path": "data_version_id", "code": "DATA_VERSION_NOT_READY", "message": "training requires an immutable READY data version"})
        if self.feature_versions is None:
            raise TrainingUnavailableError("feature version port is not configured")
        feature = _port_get(self.feature_versions, "get", "get_feature_version", spec.feature_version_id)
        if not _ready_feature(feature):
            errors.append({"path": "feature_version_id", "code": "FEATURE_VERSION_NOT_FOUND", "message": "feature version does not exist"})
        if self.evaluation_protocols is None:
            raise TrainingUnavailableError("evaluation protocol port is not configured")
        protocol_ref = spec.evaluation_protocol
        protocol = _port_get(self.evaluation_protocols, "get", "get_evaluation_protocol", protocol_ref.id, protocol_ref.version)
        if not _protocol_approved(protocol):
            errors.append({"path": "evaluation_protocol", "code": "EVALUATION_PROTOCOL_NOT_APPROVED", "message": "evaluation protocol must be reviewed"})
        if not _within_budget(spec.resource_budget, template.resource_limits):
            errors.append({"path": "resource_budget", "code": "RESOURCE_BUDGET_EXCEEDED", "message": "resource budget exceeds template limit"})
        split = spec.split
        supported = template.split_schema.get("strategies") if isinstance(template.split_schema, dict) else None
        if supported and split.strategy not in supported:
            errors.append({"path": "split.strategy", "code": "SPLIT_NOT_SUPPORTED", "message": "template does not support this split strategy"})
        if split.strategy != "random":
            errors.append({"path": "split.strategy", "code": "SPLIT_HANDLER_UNAVAILABLE", "message": "this build has no reviewed handler for the requested split strategy"})
        valid = not errors
        report = ExperimentValidation(experiment_id=current.id, revision=current.revision, etag=current.etag, config_hash=current.config_hash, valid=valid, status="READY" if valid else "INVALID", field_errors=errors, warnings=[], resource_estimate={"max_runtime_seconds": spec.resource_budget.max_runtime_seconds})
        self.repository.save_experiment(current.model_copy(update={"status": "READY" if valid else "INVALID", "validation_report": report}))
        return report

    def submit(self, project_id: str, experiment_id: str, request: SubmitRequest | str, *, actor_id: str = "service", expected_revision: int | None = None, if_match: str | None = None, idempotency_key: str | None = None) -> ExperimentAccepted:
        confirmation_id = request.confirmation_id if isinstance(request, SubmitRequest) else request
        current = self.get(project_id, experiment_id)
        if expected_revision is not None and current.revision != expected_revision:
            raise TrainingPreconditionError("experiment revision is stale")
        if if_match is not None and current.etag != if_match:
            raise TrainingPreconditionError("experiment etag is stale")
        if current.job_id is not None:
            # A replay is valid only when the idempotency scope (actor,
            # project, key and confirmation binding) matches the original
            # submission.  Do not turn the presence of a job_id into an
            # authorization bypass for a different actor or confirmation.
            replay_lookup = getattr(self.repository, "get_submission_replay", None)
            if idempotency_key and callable(replay_lookup):
                job = replay_lookup(experiment=current, confirmation_id=confirmation_id, actor_id=actor_id, idempotency_key=idempotency_key)
                if job is not None:
                    return ExperimentAccepted(id=current.id, job_id=job.id, status="QUEUED", resource_ref={"type": "experiment", "id": current.id})
            raise TrainingConflictError("experiment already has a submission")
        report = current.validation_report
        if current.status != "READY" or report is None or not report.valid or report.revision != current.revision or report.config_hash != current.config_hash:
            raise TrainingConflictError("experiment must be validated against its current revision")
        if self.confirmation is None:
            raise ConfirmationRequiredError("training submission requires confirmation")
        resource_ref = {"type": "experiment", "id": current.id}
        try:
            with ExitStack() as stack:
                stack.enter_context(self.repository.transaction())
                confirmation_tx = getattr(self.confirmation, "transaction", None)
                if callable(confirmation_tx):
                    stack.enter_context(confirmation_tx())
                data_tx = getattr(self.data_versions, "transaction", None) if self.data_versions is not None else None
                if callable(data_tx):
                    stack.enter_context(data_tx())
                consume = self.confirmation.consume
                try:
                    consume(confirmation_id=confirmation_id, project_id=project_id, resource_ref=resource_ref, action="training.submit", revision=current.revision, config_hash=current.config_hash, result_ref={"type": "experiment", "id": current.id}, actor_id=actor_id)
                except TypeError as exc:
                    # Keep compatibility with a production confirmation adapter whose
                    # transaction already binds the actor from its request context.
                    if "actor_id" not in str(exc):
                        raise
                    consume(confirmation_id=confirmation_id, project_id=project_id, resource_ref=resource_ref, action="training.submit", revision=current.revision, config_hash=current.config_hash, result_ref={"type": "experiment", "id": current.id})
                if self.data_versions is not None:
                    reference = getattr(self.data_versions, "reference", None) or getattr(self.data_versions, "add_data_version_reference", None)
                    if not callable(reference):
                        raise TrainingUnavailableError("data version reference port is not configured")
                    try:
                        reference(project_id, current.spec.data_version_id, current.id)
                    except TypeError:
                        reference(project_id, current.spec.data_version_id, reference_id=current.id)
                job, _replayed = self.repository.create_submission(experiment=current, confirmation_id=confirmation_id, actor_id=actor_id, idempotency_key=idempotency_key)
        except TrainingError:
            raise
        except Exception as exc:
            # Never expose database/queue/provider exception text through the
            # public training contract.  The original exception remains only
            # in server diagnostics; callers receive a stable safe summary.
            raise TrainingConflictError("训练提交暂不可用", details={"status_code": 503, "retryable": True}) from exc
        return ExperimentAccepted(id=current.id, job_id=job.id, status="QUEUED", resource_ref=resource_ref)

    def cancel(self, project_id: str, experiment_id: str, *, reason: str | None = None, expected_revision: int | None = None, if_match: str | None = None) -> Experiment:
        current = self.get(project_id, experiment_id)
        if expected_revision is not None and current.revision != expected_revision:
            raise TrainingPreconditionError("experiment revision is stale")
        if if_match is not None and current.etag != if_match:
            raise TrainingPreconditionError("experiment etag is stale")
        if current.job_id is None:
            if current.status in {"DRAFT", "INVALID", "READY"}:
                return current
            raise TrainingConflictError("experiment has no cancellable job")
        job = self.repository.get_job(current.job_id)
        if job is None:
            raise TrainingNotFoundError("training job does not exist")
        if job.status == "QUEUED":
            status = "CANCELED"
            job = job.model_copy(update={"status": "CANCELED", "finished_at": now_utc(), "error": {"reason": reason} if reason else None})
            tx = getattr(self.repository, "transaction", None)
            with (tx() if callable(tx) else nullcontext()):
                self.repository.save_job(job)
                cancel_outbox = getattr(self.repository, "cancel_outbox", None)
                if callable(cancel_outbox):
                    cancel_outbox(job.dispatch_id)
                current = current.model_copy(update={"status": "CANCELED", "finished_at": job.finished_at, "error": job.error})
                return self.repository.save_experiment(current)
        if job.status == "RUNNING":
            job = job.model_copy(update={"status": "CANCEL_REQUESTED"})
            tx = getattr(self.repository, "transaction", None)
            with (tx() if callable(tx) else nullcontext()):
                self.repository.save_job(job)
                return self.repository.save_experiment(current.model_copy(update={"status": "CANCEL_REQUESTED"}))
        return current


__all__ = ["TrainingService", "config_hash"]
