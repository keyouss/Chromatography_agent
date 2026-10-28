from __future__ import annotations

import base64
import hashlib
import hmac
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from chrom_platform.core.cursor import CursorError, SignedCursorCodec
from chrom_platform.core.dto import ActorContext
from chrom_platform.core.errors import APIError
from chrom_platform.modules.prediction.batch_service import (
    BatchPredictionService,
    BatchRowResultRow,
    BatchServiceError,
    BatchValidationItemRow,
)
from chrom_platform.modules.prediction.batch_router import create_router
from chrom_platform.modules.prediction.batch import BatchRowsPage
from test_batch_contracts import _batch_db


_TEST_CODEC = SignedCursorCodec(b"t" * 32)


def _signed_raw_cursor(raw: bytes) -> str:
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return _signed_body_cursor(body)


def _signed_body_cursor(body: str) -> str:
    signature = base64.urlsafe_b64encode(hmac.new(_TEST_CODEC.key, body.encode("ascii"), hashlib.sha256).digest()).rstrip(b"=").decode("ascii")
    return f"{body}.{signature}"


def _seed_rows(session, batch, row_ids: tuple[str, ...] = ("z-row", "a-row", "m-row")) -> None:
    for source_row_number, row_id in enumerate(row_ids, start=1):
        status = "INVALID" if source_row_number == 2 else "VALID"
        session.add(BatchValidationItemRow(
            id=f"item-{row_id}", project_id=batch.project_id, validation_id=batch.validation_id,
            row_id=row_id, source_row_number=source_row_number, status=status,
            normalized_input_hash="a" * 64, resolved_input_snapshot=None,
            resolved_input_hash=None, field_errors=[], warnings=[],
        ))
        session.add(BatchRowResultRow(
            id=f"result-{row_id}-1", project_id=batch.project_id, batch_id=batch.id,
            validation_id=batch.validation_id, row_id=row_id, source_row_number=source_row_number,
            input_snapshot={"row": source_row_number}, result={"attempt": 1}, error=None,
            warnings=[], attempt=1, status="FAILED" if source_row_number in {1, 2} else "SUCCEEDED",
        ))
    # A retry projection must not add a second logical result row to the page.
    session.add(BatchRowResultRow(
        id="result-z-row-2", project_id=batch.project_id, batch_id=batch.id,
        validation_id=batch.validation_id, row_id="z-row", source_row_number=1,
        input_snapshot={"row": 1}, result={"attempt": 2}, error=None,
        warnings=[], attempt=2, status="SUCCEEDED",
    ))
    session.flush()


def test_validation_rows_pagination_follows_source_order_with_opaque_ids() -> None:
    _engine, session, ctx, batch, _job = _batch_db()
    _seed_rows(session, batch)
    service = BatchPredictionService(session, enabled_batch_types={"gc"})

    seen: list[tuple[str, int]] = []
    cursor = None
    while True:
        page = service.validation_rows(ctx, batch.id, status_filter=None, cursor=cursor, limit=1, cursor_codec=_TEST_CODEC)
        seen.extend((row.row_id, row.source_row_number) for row in page.rows)
        if not page.has_more:
            break
        cursor = page.next_cursor

    assert seen == [("z-row", 1), ("a-row", 2), ("m-row", 3)]
    assert len({row_id for row_id, _ in seen}) == len(seen)

    filtered = service.validation_rows(ctx, batch.id, status_filter="VALID", cursor=None, limit=1, cursor_codec=_TEST_CODEC)
    assert [(row.row_id, row.source_row_number) for row in filtered.rows] == [("z-row", 1)]
    following = service.validation_rows(ctx, batch.id, status_filter="VALID", cursor=filtered.next_cursor, limit=1, cursor_codec=_TEST_CODEC)
    assert [(row.row_id, row.source_row_number) for row in following.rows] == [("m-row", 3)]
    with pytest.raises(BatchServiceError) as mismatch:
        service.validation_rows(ctx, batch.id, status_filter="INVALID", cursor=filtered.next_cursor, limit=10, cursor_codec=_TEST_CODEC)
    assert mismatch.value.status_code == 400 and mismatch.value.code == "VALIDATION_FAILED"


def test_result_rows_pagination_deduplicates_latest_attempt_and_filters_status() -> None:
    _engine, session, ctx, batch, _job = _batch_db()
    _seed_rows(session, batch)
    service = BatchPredictionService(session, enabled_batch_types={"gc"})

    seen = []
    cursor = None
    while True:
        page = service.rows(ctx, batch.id, status_filter=None, cursor=cursor, limit=1, cursor_codec=_TEST_CODEC)
        seen.extend(page.rows)
        if not page.has_more:
            break
        cursor = page.next_cursor

    assert [(row.row_id, row.source_row_number, row.attempt) for row in seen] == [
        ("z-row", 1, 2), ("a-row", 2, 1), ("m-row", 3, 1),
    ]
    assert len({row.row_id for row in seen}) == len(seen)

    filtered = service.rows(ctx, batch.id, status_filter="SUCCEEDED", cursor=None, limit=1, cursor_codec=_TEST_CODEC)
    assert [(row.row_id, row.attempt) for row in filtered.rows] == [("z-row", 2)]
    following = service.rows(ctx, batch.id, status_filter="SUCCEEDED", cursor=filtered.next_cursor, limit=1, cursor_codec=_TEST_CODEC)
    assert [(row.row_id, row.attempt) for row in following.rows] == [("m-row", 1)]

    with pytest.raises(BatchServiceError) as unknown:
        service.rows(ctx, batch.id, status_filter=None, cursor="missing-row", limit=10, cursor_codec=_TEST_CODEC)
    assert unknown.value.status_code == 400 and unknown.value.code == "VALIDATION_FAILED"


def test_signed_cursor_rejects_tampering_scope_and_noncanonical_payload() -> None:
    token = _TEST_CODEC.encode(after=(17, "brow_é"), scope="a" * 64)
    assert _TEST_CODEC.decode(token, scope="a" * 64) == (17, "brow_é")
    with pytest.raises(CursorError):
        _TEST_CODEC.decode(token, scope="b" * 64)
    body, signature = token.split(".")
    with pytest.raises(CursorError):
        _TEST_CODEC.decode(body[:-1] + ("A" if body[-1] != "A" else "B") + "." + signature, scope="a" * 64)

    with pytest.raises(CursorError):
        _TEST_CODEC.decode(_signed_raw_cursor(b'{"after":[1,"row"],"after":[1,"row"],"scope":"' + b"a" * 64 + b'","v":1}'), scope="a" * 64)


@pytest.mark.parametrize("raw", [
    b'{"after":[1,"row"],"extra":true,"scope":"' + b"a" * 64 + b'","v":1}',
    b'{"after":[true,"row"],"scope":"' + b"a" * 64 + b'","v":1}',
    b'{"after":[1,"row"],"scope":"' + b"a" * 64 + b'","v":2}',
    b'{"after":[1,"\\ud800"],"scope":"' + b"a" * 64 + b'","v":1}',
    b'{"after":[1,"row"],"scope":"' + b"a" * 64 + b'","v":1}\xff',
    b'{ "after":[1,"row"],"scope":"' + b"a" * 64 + b'","v":1}',
])
def test_signed_cursor_rejects_authenticated_noncanonical_and_invalid_payloads(raw: bytes) -> None:
    with pytest.raises(CursorError):
        _TEST_CODEC.decode(_signed_raw_cursor(raw), scope="a" * 64)


def test_signed_cursor_rejects_noncanonical_base64url_with_valid_mac() -> None:
    raw = b'{"after":[1,"row"],"scope":"' + b"a" * 64 + b'","v":1}'
    canonical = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    alternatives = [canonical[:-1] + char for char in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-" if char != canonical[-1]]
    noncanonical = next(body for body in alternatives if base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)) == raw)
    with pytest.raises(CursorError):
        _TEST_CODEC.decode(_signed_body_cursor(noncanonical), scope="a" * 64)


def test_signed_cursor_position_domain_and_worst_case_unicode_length() -> None:
    row_id = "𐀀" * 96
    token = _TEST_CODEC.encode(after=(2_147_483_647, row_id), scope="f" * 64)
    assert len(token) <= 1024
    assert _TEST_CODEC.decode(token, scope="f" * 64) == (2_147_483_647, row_id)
    with pytest.raises(CursorError):
        _TEST_CODEC.encode(after=(True, "row"), scope="f" * 64)
    with pytest.raises(CursorError):
        _TEST_CODEC.encode(after=(2_147_483_648, "row"), scope="f" * 64)
    with pytest.raises(CursorError):
        _TEST_CODEC.encode(after=(1, "𐀀" * 97), scope="f" * 64)


def test_batch_codec_is_required_only_for_runtime_batch_builds(monkeypatch: pytest.MonkeyPatch) -> None:
    from chrom_platform.app import _batch_cursor_codec, _contract_generation_rejection

    monkeypatch.delenv("CHROM_BATCH_CURSOR_SIGNING_KEY", raising=False)
    batch_manifest = SimpleNamespace(prediction_tools={"tlc": ["single", "batch"]})
    no_batch_manifest = SimpleNamespace(prediction_tools={"gc": ["single"]})
    assert _batch_cursor_codec(batch_manifest, contract_generation=True) is None
    assert _contract_generation_rejection("/api/v1/batch-predictions/batch-test/rows").status_code == 503
    assert _contract_generation_rejection("/openapi.json") is None
    assert _batch_cursor_codec(no_batch_manifest, contract_generation=False) is None
    with pytest.raises(RuntimeError, match="batch cursor signing key"):
        _batch_cursor_codec(batch_manifest, contract_generation=False)
    encoded = base64.urlsafe_b64encode(b"k" * 32).rstrip(b"=").decode("ascii")
    monkeypatch.setenv("CHROM_BATCH_CURSOR_SIGNING_KEY", encoded)
    codec = _batch_cursor_codec(batch_manifest, contract_generation=False)
    assert codec is not None and codec.key == b"k" * 32
    assert "kkkk" not in repr(codec)


@pytest.mark.parametrize("endpoint", ["/batch-predictions/{batch_id}/rows", "/batch-predictions/{batch_id}/validation/rows"])
@pytest.mark.parametrize("cursor", ["", "x" * 1025])
def test_batch_cursor_http_bad_length_is_400(endpoint: str, cursor: str) -> None:
    app = FastAPI()
    app.state.batch_cursor_codec = _TEST_CODEC
    app.include_router(create_router(service=object()), prefix="/api/v1")

    @app.exception_handler(APIError)
    def api_error_handler(_request, exc: APIError):
        return JSONResponse(status_code=exc.status_code, content={"code": exc.payload.code})

    with TestClient(app) as client:
        response = client.get("/api/v1" + endpoint.format(batch_id="batch-test"), params={"cursor": cursor})
    assert response.status_code == 400 and response.json()["code"] == "VALIDATION_FAILED"


def test_batch_rows_http_signs_and_rejects_tampered_cursor() -> None:
    app = FastAPI()
    app.state.batch_cursor_codec = _TEST_CODEC
    app.state.actor_context = ActorContext(actor_id="actor-test", project_ids=["project-test"], auth_method="service", permissions=["*"], policy_version="test")
    class HttpBatchService:
        def rows(self, _ctx, _batch_id, *, status_filter, cursor, limit, cursor_codec):
            scope = "a" * 64
            if cursor is not None:
                try:
                    cursor_codec.decode(cursor, scope=scope)
                except CursorError as exc:
                    raise BatchServiceError(400, "VALIDATION_FAILED", "分页游标无效或与当前筛选不匹配") from exc
            next_cursor = cursor_codec.encode(after=(2, "brow_two"), scope=scope) if cursor is None else None
            return BatchRowsPage(rows=[], next_cursor=next_cursor, has_more=cursor is None)

    app.include_router(create_router(service=HttpBatchService()), prefix="/api/v1")

    @app.exception_handler(APIError)
    def api_error_handler(_request, exc: APIError):
        return JSONResponse(status_code=exc.status_code, content={"code": exc.payload.code})

    with TestClient(app) as client:
        first = client.get("/api/v1/batch-predictions/batch-test/rows", params={"limit": 1})
        assert first.status_code == 200
        cursor = first.json()["data"]["next_cursor"]
        assert cursor and len(cursor) <= 1024
        tampered = cursor[:-1] + ("A" if cursor[-1] != "A" else "B")
        rejected = client.get("/api/v1/batch-predictions/batch-test/rows", params={"cursor": tampered})
    assert rejected.status_code == 400 and rejected.json()["code"] == "VALIDATION_FAILED"


def test_batch_rows_cursor_is_invalidated_by_retry_revision_change() -> None:
    _engine, session, ctx, batch, _job = _batch_db()
    _seed_rows(session, batch)
    service = BatchPredictionService(session, enabled_batch_types={"gc"})
    first = service.rows(ctx, batch.id, status_filter=None, cursor=None, limit=1, cursor_codec=_TEST_CODEC)
    assert first.next_cursor is not None
    batch.revision += 1
    with pytest.raises(BatchServiceError) as stale:
        service.rows(ctx, batch.id, status_filter=None, cursor=first.next_cursor, limit=1, cursor_codec=_TEST_CODEC)
    assert stale.value.status_code == 400 and stale.value.code == "VALIDATION_FAILED"



