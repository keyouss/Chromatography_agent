from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from chrom_platform.core.dto import ActorContext
from chrom_platform.core.errors import APIError
from chrom_platform.core.idempotency import BeginResult, IdempotencyRecord
from chrom_platform.modules.data_center.schemas import (
    AsyncAccepted,
    DataDomainSchema,
    DataImportCreate,
    DomainField,
)
from chrom_platform.modules.data_center.service import DataImportService, StaticDataDomainRegistry
from chrom_platform.modules.data_center.testing import InMemoryDataRepository
from chrom_platform.modules.data_center.errors import DataValidationError
from chrom_platform.modules.data_center.router import create_router
from chrom_platform.modules.data_center.import_workflow import SQLAlchemyImportWorkflow
from chrom_platform.task_core.service import OperationContext, TaskError


class _Idempotency:
    def __init__(self):
        self.rows = {}

    def get(self, scope):
        return self.rows.get(scope)

    def begin(self, scope, payload_hash):
        if scope in self.rows:
            return BeginResult(self.rows[scope], False)
        row = IdempotencyRecord(payload_hash=payload_hash, status="IN_PROGRESS")
        self.rows[scope] = row
        return BeginResult(row, True)

    def complete(self, scope, payload_hash, status_code, response):
        self.rows[scope] = IdempotencyRecord(payload_hash, "COMPLETED", status_code, response)

    def fail(self, scope, payload_hash, error_code):
        self.rows[scope] = IdempotencyRecord(payload_hash, "FAILED")


class _Workflow:
    def __init__(self):
        self.calls = []

    def submit_validation(self, ctx, project_id, import_id, *, if_match):
        self.calls.append(("validate", project_id, import_id, if_match))
        return AsyncAccepted(id=import_id, status="QUEUED", job_id="job-fixture-1", resource_ref={"type": "data_import", "id": import_id})


def _domain(**updates):
    values = {
        "id": "fixture-domain", "version": "1.0", "chromatography_type": "sfc",
        "record_schema": {"sample_id": DomainField(type="string")},
        "unit_definitions": {}, "identity_fields": ["sample_id"],
        "deduplication_policy": "reject", "supported_import_modes": ["append"],
    }
    values.update(updates)
    return DataDomainSchema(**values)


def _body(mode="append"):
    return {
        "project_id": "project-a", "domain_id": "fixture-domain", "artifact_id": "artifact-fixture",
        "schema_version": "1.0", "import_mode": mode,
        "field_mapping": {"sample_id": "sample"}, "source_description": "fixture only",
    }


def test_validation_route_returns_business_location_and_replays_same_real_job_id():
    app = FastAPI()
    app.state.actor_context = ActorContext(
        actor_id="actor-fixture", tenant_id=None, project_ids=["project-a"],
        auth_method="deployment_default", permissions=["*"], policy_version="fixture-v1",
    )
    app.state.idempotency_store = _Idempotency()
    app.add_exception_handler(APIError, lambda _request, exc: JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.payload.model_dump(mode="json"), "request_id": "fixture"},
    ))
    domain = _domain()
    workflow = _Workflow()
    service = DataImportService(InMemoryDataRepository(), StaticDataDomainRegistry([domain]))
    app.include_router(create_router(
        service=service, domain_registry=StaticDataDomainRegistry([domain]),
        idempotency_store=app.state.idempotency_store, import_workflow=workflow,
    ), prefix="/api/v1")
    with TestClient(app) as client:
        created = client.post("/api/v1/data-imports", headers={"Idempotency-Key": "create-fixture"}, json=_body())
        assert created.status_code == 201
        data_import = created.json()["data"]
        endpoint = f"/api/v1/data-imports/{data_import['id']}/validate"
        headers = {"If-Match": data_import["etag"], "Idempotency-Key": "validate-fixture"}
        response = client.post(endpoint, headers=headers, json={})
        replay = client.post(endpoint, headers=headers, json={})
    assert response.status_code == replay.status_code == 202
    assert response.headers["Location"] == f"/api/v1/data-imports/{data_import['id']}"
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert response.json()["data"] == replay.json()["data"]
    accepted = response.json()["data"]
    assert accepted["id"] == data_import["id"]
    assert accepted["job_id"] == "job-fixture-1"
    assert accepted["resource_ref"] == {"type": "data_import", "id": data_import["id"]}
    assert workflow.calls == [("validate", "project-a", data_import["id"], data_import["etag"])]


def test_upsert_and_update_existing_are_rejected_in_first_slice():
    service = DataImportService(
        InMemoryDataRepository(),
        StaticDataDomainRegistry([_domain(supported_import_modes=["append", "upsert"])]),
    )
    try:
        service.create(DataImportCreate.model_validate(_body("upsert")))
    except DataValidationError:
        pass
    else:
        raise AssertionError("upsert must fail closed in the append-only slice")

    service = DataImportService(
        InMemoryDataRepository(),
        StaticDataDomainRegistry([_domain(deduplication_policy="update_existing", supported_import_modes=["append"])]),
    )
    try:
        service.create(DataImportCreate.model_validate(_body()))
    except DataValidationError:
        pass
    else:
        raise AssertionError("update_existing must fail closed in the append-only slice")


def test_workflow_checks_project_and_write_permission_before_database_or_job_access():
    class _Session:
        scalar_calls = 0

        def scalar(self, _statement):
            self.scalar_calls += 1
            raise AssertionError("authorization must precede database access")

    session = _Session()
    workflow = SQLAlchemyImportWorkflow(session, None, None)

    no_access = ActorContext(
        actor_id="actor-fixture", tenant_id=None, project_ids=["other-project"],
        auth_method="deployment_default", permissions=["data:write"], policy_version="fixture-v1",
    )
    try:
        workflow.submit_validation(OperationContext(actor=no_access), "project-a", "di-fixture", if_match="etag")
    except TaskError as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError("cross-project write must be rejected")

    no_write = no_access.model_copy(update={"project_ids": ["project-a"], "permissions": ["data:read"]})
    try:
        workflow.submit_validation(OperationContext(actor=no_write), "project-a", "di-fixture", if_match="etag")
    except TaskError as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError("missing write permission must be rejected")
    assert session.scalar_calls == 0
