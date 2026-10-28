import inspect
from types import SimpleNamespace

from chrom_platform.core.dto import ActorContext
from chrom_platform.task_core.schemas import JobCancelRequest, JobRetryRequest
from chrom_platform.task_core.router import create_router
from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import Response


def test_task_router_factory_is_not_application_registration():
    router = create_router()
    assert not any(route.path == "/jobs" and "POST" in route.methods for route in router.routes)
    assert {route.path for route in router.routes} == {
        "/jobs",
        "/jobs/{job_id}",
        "/jobs/{job_id}/cancel",
        "/jobs/{job_id}/retry",
        "/jobs/{job_id}/attempts",
        "/jobs/{job_id}/events",
        "/jobs/{job_id}/events/stream",
        "/confirmations/{confirmation_id}",
        "/confirmations/previews",
        "/confirmations",
        "/confirmations/{confirmation_id}/revoke",
    }


def test_mutating_headers_are_required_in_openapi():
    app = FastAPI()
    app.include_router(create_router())
    parameters = app.openapi()["paths"]["/jobs/{job_id}/cancel"]["post"]["parameters"]
    headers = {item["name"]: item for item in parameters if item["in"] == "header"}
    assert [item["name"] for item in parameters if item["in"] == "header"] == ["If-Match", "Idempotency-Key"]
    assert headers["If-Match"]["required"] is True
    assert headers["Idempotency-Key"]["required"] is True
    assert headers["Idempotency-Key"]["schema"]["maxLength"] == 128


def test_accepted_job_mutations_declare_location_response_header():
    app = FastAPI()
    app.include_router(create_router())
    spec = app.openapi()

    for path in ("/jobs/{job_id}/cancel", "/jobs/{job_id}/retry"):
        location = spec["paths"][path]["post"]["responses"]["202"]["headers"]["Location"]
        assert location["required"] is True
        assert location["schema"] == {"type": "string", "format": "uri-reference"}


def test_sync_task_endpoints_are_dispatched_by_fastapi_threadpool():
    router = create_router()

    assert router.routes
    assert all(not inspect.iscoroutinefunction(route.endpoint) for route in router.routes)


def test_accepted_job_mutations_set_location_for_replayed_results():
    app = FastAPI()
    app.state.actor_context = ActorContext(
        actor_id="actor-1",
        tenant_id=None,
        project_ids=["project-1"],
        auth_method="deployment_default",
        permissions=[],
        policy_version="test",
    )

    class Service:
        def cancel(self, *_args, **_kwargs):
            return SimpleNamespace(data=SimpleNamespace(id="job-1"), replayed=True)

        def retry(self, *_args, **_kwargs):
            return SimpleNamespace(data=SimpleNamespace(id="job-1"), replayed=True)

    router = create_router(job_service=Service())

    def request_for(path: str) -> Request:
        return Request(
            {
                "type": "http",
                "method": "POST",
                "path": path,
                "headers": [(b"if-match", b'"1"'), (b"idempotency-key", b"request-1")],
                "query_string": b"",
                "scheme": "http",
                "server": ("testserver", 80),
                "client": ("testclient", 1),
                "app": app,
                "state": {},
            }
        )

    endpoints = {route.path: route.endpoint for route in router.routes}
    for path, payload in (
        ("/jobs/{job_id}/cancel", JobCancelRequest()),
        ("/jobs/{job_id}/retry", JobRetryRequest()),
    ):
        response = Response()
        endpoints[path]("job-1", payload, request_for(path), response, Service())
        assert response.headers["location"] == "/api/v1/jobs/job-1"
        assert response.headers["idempotency-replayed"] == "true"
