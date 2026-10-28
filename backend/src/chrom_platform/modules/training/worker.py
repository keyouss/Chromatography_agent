"""受控训练 Worker。

消息只带稳定的 job/attempt/dispatch 标识；spec、模板和数据引用全部从
持久化读取。模板处理器来自构建时注册表，绝不动态导入或执行请求字段。
"""

from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timezone
from typing import Any, Mapping

from .errors import TrainingConflictError
from .persistence import now_utc
from .ports import CandidateRegistrationPort, TrainingRepository, TrainingTemplateRegistry
from .schemas import CandidateResult, Experiment, TrainingJob, WorkerMessage


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


class TrainingWorker:
    def __init__(self, repository: TrainingRepository, templates: TrainingTemplateRegistry, *, candidate_registration: CandidateRegistrationPort | None = None):
        self.repository = repository
        self.templates = templates
        self.candidate_registration = candidate_registration

    def _transaction(self):
        """Use one caller-owned short transaction for each DB state change."""
        transaction = getattr(self.repository, "transaction", None)
        return transaction() if callable(transaction) else nullcontext()

    def _experiment(self, job: TrainingJob) -> Experiment:
        value = self.repository.get_experiment(job.project_id, job.experiment_id)
        if value is None:
            raise TrainingConflictError("training experiment is missing")
        return value

    def _finish_failure(self, job: TrainingJob, experiment: Experiment, *, status: str, code: str) -> TrainingJob:
        finished = _now()
        job = job.model_copy(update={"status": status, "finished_at": finished, "error": {"code": code}})
        self.repository.save_job(job)
        self.repository.save_experiment(experiment.model_copy(update={"status": status, "finished_at": finished, "error": {"code": code}, "candidate_status": "NOT_REQUESTED", "candidate_model_id": None}))
        return job

    @staticmethod
    def _result(value: Any) -> CandidateResult:
        if isinstance(value, CandidateResult):
            return value
        if isinstance(value, Mapping):
            return CandidateResult.model_validate(value)
        return CandidateResult.model_validate({"artifact_id": getattr(value, "artifact_id"), "manifest": getattr(value, "manifest"), "metrics": getattr(value, "metrics", {})})

    def process(self, message: WorkerMessage) -> TrainingJob:
        # SQL repositories expose a three-phase fenced execution protocol.  The
        # in-memory adapter below remains a deliberately small contract-test
        # fallback and has no lease state.
        if callable(getattr(self.repository, "claim", None)) and callable(getattr(self.repository, "finish", None)):
            return self._process_persistent(message)
        job = self.repository.get_job(message.job_id)
        if job is None:
            raise TrainingConflictError("training job does not exist")
        if job.dispatch_id != message.dispatch_id or job.attempt != message.attempt:
            raise TrainingConflictError("worker message does not match the job attempt")
        if job.status in {"SUCCEEDED", "FAILED", "CANCELED", "TIMED_OUT", "INTERRUPTED"}:
            return job
        # A redelivered message for an already claimed attempt must not run the
        # trusted handler twice. Recovery uses a new attempt/dispatch record.
        if job.status == "RUNNING":
            return job
        experiment = self._experiment(job)
        if job.status == "CANCEL_REQUESTED":
            return self._finish_failure(job, experiment, status="CANCELED", code="CANCELED_BEFORE_START")
        outbox = self.repository.get_outbox(message.dispatch_id)
        if outbox is not None and getattr(outbox, "job_id", None) != job.id:
            raise TrainingConflictError("dispatch record does not match the job")
        running = job.model_copy(update={"status": "RUNNING", "started_at": _now()})
        self.repository.save_job(running)
        self.repository.save_experiment(experiment.model_copy(update={"status": "RUNNING", "started_at": running.started_at}))
        handler_lookup = getattr(self.templates, "handler", None)
        handler = handler_lookup(experiment.spec.execution.template_id, experiment.spec.execution.template_version) if callable(handler_lookup) else None
        template = self.templates.get(experiment.spec.execution.template_id, experiment.spec.execution.template_version)
        if handler is None or template is None or template.health != "READY":
            return self._finish_failure(running, experiment, status="FAILED", code="TEMPLATE_UNAVAILABLE")
        try:
            result = self._result(handler(experiment.spec, attempt=message.attempt))
        except TimeoutError:
            return self._finish_failure(running, experiment, status="TIMED_OUT", code="TRAINING_TIMEOUT")
        except (KeyboardInterrupt, GeneratorExit):
            return self._finish_failure(running, experiment, status="CANCELED", code="TRAINING_CANCELED")
        except Exception:
            return self._finish_failure(running, experiment, status="FAILED", code="TRAINING_EXECUTION_FAILED")
        latest = self.repository.get_job(running.id)
        if latest is not None and latest.status in {"CANCEL_REQUESTED", "CANCELED"}:
            return self._finish_failure(latest, self._experiment(latest), status="CANCELED", code="CANCELED_DURING_TRAINING")
        if running.started_at is not None and (_now() - running.started_at).total_seconds() > experiment.spec.resource_budget.max_runtime_seconds:
            return self._finish_failure(running, experiment, status="TIMED_OUT", code="TRAINING_TIMEOUT")
        finished = _now()
        succeeded = running.model_copy(update={"status": "SUCCEEDED", "finished_at": finished, "result_ref": {"type": "artifact", "id": result.artifact_id}})
        self.repository.save_job(succeeded)
        current = self._experiment(succeeded)
        updated = current.model_copy(update={"status": "SUCCEEDED", "finished_at": finished, "candidate_status": "VALIDATING", "candidate_model_id": None})
        self.repository.save_experiment(updated)
        if self.candidate_registration is None:
            self.repository.save_experiment(updated.model_copy(update={"candidate_status": "REJECTED", "error": {"code": "CANDIDATE_REGISTRATION_UNAVAILABLE"}}))
            return succeeded
        try:
            candidate = self.candidate_registration.register_candidate(project_id=updated.spec.project_id, experiment=updated, result=result)
            candidate_id = candidate if isinstance(candidate, str) else candidate.get("id") if isinstance(candidate, Mapping) else candidate.id
            if not candidate_id:
                raise ValueError("candidate registration returned no id")
            self.repository.save_experiment(updated.model_copy(update={"candidate_status": "CREATED", "candidate_model_id": str(candidate_id)}))
        except Exception:
            self.repository.save_experiment(updated.model_copy(update={"candidate_status": "REJECTED", "error": {"code": "CANDIDATE_VALIDATION_FAILED"}}))
        return succeeded

    def _process_persistent(self, message: WorkerMessage) -> TrainingJob:
        """Claim, execute a build-time handler, then fenced-finish.

        Only the message identifiers cross the queue.  The experiment spec is
        loaded after claim, and every finish checks the lease/fencing token so
        an expired or superseded worker cannot write a terminal result.
        """
        job = self.repository.get_job(message.job_id)
        if job is None:
            raise TrainingConflictError("training job does not exist")
        if job.dispatch_id != message.dispatch_id or job.attempt != message.attempt:
            raise TrainingConflictError("worker message does not match the job attempt")
        if job.status in {"SUCCEEDED", "FAILED", "CANCELED", "TIMED_OUT", "INTERRUPTED"}:
            return job
        if job.status == "RUNNING":
            return job
        # Claim must finish before the potentially long template inference.
        # The repository owns no Session lifecycle; the surrounding worker
        # runner remains responsible for committing/rolling back the caller's
        # Session.
        with self._transaction():
            claim = self.repository.claim(message)
        if claim is None:
            # A competing claim or stale dispatch is a no-op at the database
            # layer.  The caller still receives a deterministic conflict.
            raise TrainingConflictError("worker message could not claim the queued attempt")
        claimed_job = claim.job
        experiment = claim.experiment

        def finish(status: str, *, result: CandidateResult | None = None, error: dict[str, Any] | None = None) -> TrainingJob:
            # Finish is another short transaction after inference.  A stale
            # lease is checked before any write by the repository.
            with self._transaction():
                applied = self.repository.finish(message, execution_token=claim.execution_token, fencing_token=claim.fencing_token, status=status, result=result, error=error)
            if not applied:
                current = self.repository.get_job(message.job_id)
                if current is None:
                    raise TrainingConflictError("training job disappeared during execution")
                return current
            current = self.repository.get_job(message.job_id)
            if current is None:
                raise TrainingConflictError("training job disappeared after execution")
            return current

        handler_lookup = getattr(self.templates, "handler", None)
        handler = handler_lookup(experiment.spec.execution.template_id, experiment.spec.execution.template_version) if callable(handler_lookup) else None
        template = self.templates.get(experiment.spec.execution.template_id, experiment.spec.execution.template_version)
        if handler is None or template is None or template.health != "READY":
            return finish("FAILED", error={"code": "TEMPLATE_UNAVAILABLE"})
        try:
            result = self._result(handler(experiment.spec, attempt=message.attempt))
        except TimeoutError:
            return finish("TIMED_OUT", error={"code": "TRAINING_TIMEOUT"})
        except (KeyboardInterrupt, GeneratorExit):
            return finish("CANCELED", error={"code": "TRAINING_CANCELED"})
        except Exception:
            return finish("FAILED", error={"code": "TRAINING_EXECUTION_FAILED"})
        latest = self.repository.get_job(message.job_id)
        if latest is not None and latest.status in {"CANCEL_REQUESTED", "CANCELED"}:
            return finish("CANCELED", error={"code": "CANCELED_DURING_TRAINING"})
        if claimed_job.started_at is not None and (_now() - _aware(claimed_job.started_at)).total_seconds() > experiment.spec.resource_budget.max_runtime_seconds:
            return finish("TIMED_OUT", error={"code": "TRAINING_TIMEOUT"})
        succeeded = finish("SUCCEEDED", result=result)
        if succeeded.status != "SUCCEEDED":
            return succeeded
        current = self.repository.get_experiment(experiment.spec.project_id, experiment.id) or experiment
        if self.candidate_registration is None:
            self.repository.save_experiment(current.model_copy(update={"candidate_status": "REJECTED", "error": {"code": "CANDIDATE_REGISTRATION_UNAVAILABLE"}}))
            return succeeded
        try:
            candidate = self.candidate_registration.register_candidate(project_id=experiment.spec.project_id, experiment=current, result=result)
            candidate_id = candidate if isinstance(candidate, str) else candidate.get("id") if isinstance(candidate, Mapping) else candidate.id
            if not candidate_id:
                raise ValueError("candidate registration returned no id")
            self.repository.save_experiment(current.model_copy(update={"candidate_status": "CREATED", "candidate_model_id": str(candidate_id), "error": None}))
        except Exception:
            self.repository.save_experiment(current.model_copy(update={"candidate_status": "REJECTED", "error": {"code": "CANDIDATE_VALIDATION_FAILED"}}))
        return succeeded

    run = process
    handle = process


__all__ = ["TrainingWorker"]
