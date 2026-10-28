"""Prediction-record export submission, snapshots, and safe format writers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import csv
import hashlib
import io
import json
import re
import struct
import zlib
from datetime import datetime, timezone
from html import escape
from typing import Any
from uuid import uuid4
import zipfile

from sqlalchemy import select
from sqlalchemy.orm import Session

from chrom_platform.artifact_core.models import Artifact
from chrom_platform.artifact_core.storage import ArtifactStore
from chrom_platform.core.dto import ActorContext, ResourceRef
from chrom_platform.infrastructure.models import AuditEvent, Job
from chrom_platform.modules.prediction.persistence import PredictionRow
from chrom_platform.task_core.repository import SQLAlchemyTaskRepository
from chrom_platform.task_core.service import add_job_rows, sync_job_lifecycle
from chrom_platform.task_core.state import JobStatus
from chrom_platform.task_core.outbox_dispatch import LeaseLostError

from .errors import DataNotFoundError, DataUnavailableError, DataValidationError
from .persistence import ExportRow, PredictionRecordRow, _public_prediction_input_snapshot
from .schemas import ExportAccepted, ExportCreate, ExportView


_EXPORT_INCLUDES = frozenset({"input_snapshot", "result", "model_version", "warnings"})
_EXPORT_ERROR_MESSAGES = {
    "EXPORT_CANCELED": "导出已取消",
    "EXPORT_GENERATION_FAILED": "导出生成失败",
    "EXPORT_STORAGE_UNAVAILABLE": "导出存储暂不可用",
}
_SENSITIVE_KEY = re.compile(r"(?:secret|password|token|credential|connection|traceback|stack|exception|absolute.?path|storage.?key)", re.I)
_ABSOLUTE_PATH = re.compile(r"(?:^|[\s\"'])/(?:[^\s]+)|\b[A-Za-z]:\\(?:[^\s]+)")


def _safe_scalar(value: Any) -> bool:
    if value is None or isinstance(value, (bool, int, float)):
        return True
    if not isinstance(value, str):
        return False
    lowered = value.lower()
    return not (
        _ABSOLUTE_PATH.search(value)
        or "traceback (most recent call last)" in lowered
        or "-----begin private key-----" in lowered
        or "password=" in lowered
        or "secret=" in lowered
        or "token=" in lowered
        or "://" in value and "@" in value
    )


def _project_legacy_json(value: Any, *, depth: int = 0) -> Any:
    """Project legacy result JSON to bounded public JSON primitives."""
    if depth > 12:
        raise DataUnavailableError("导出结果快照不可用")
    if _safe_scalar(value):
        return value
    if isinstance(value, list):
        if len(value) > 100_000:
            raise DataUnavailableError("导出结果快照不可用")
        return [_project_legacy_json(item, depth=depth + 1) for item in value]
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or _SENSITIVE_KEY.search(key):
                continue
            output[key] = _project_legacy_json(item, depth=depth + 1)
        return output
    raise DataUnavailableError("导出结果快照不可用")


def _public_export_snapshots(value: object) -> dict[str, Any]:
    """Allowlist public export fields; never serialize persisted JSON directly."""
    if not isinstance(value, Mapping) or not isinstance(value.get("records"), list):
        raise DataUnavailableError("导出来源快照不可用")
    include_raw = value.get("include", [])
    if not isinstance(include_raw, list) or any(
        not isinstance(item, str) or item not in _EXPORT_INCLUDES for item in include_raw
    ):
        raise DataUnavailableError("导出来源快照不可用")
    output: dict[str, Any] = {"records": []}
    if isinstance(value.get("format_version"), str) and _safe_scalar(value["format_version"]):
        output["format_version"] = value["format_version"]
    if isinstance(value.get("generated_at"), str) and _safe_scalar(value["generated_at"]):
        output["generated_at"] = value["generated_at"]
    output["include"] = list(dict.fromkeys(include_raw))
    for raw in value["records"]:
        if not isinstance(raw, Mapping) or not isinstance(raw.get("record_id"), str):
            raise DataUnavailableError("导出来源快照不可用")
        record: dict[str, Any] = {"record_id": raw["record_id"]}
        for key in ("created_at", "status", "source", "chromatography_type", "model_version"):
            item = raw.get(key)
            if isinstance(item, str) and _safe_scalar(item):
                record[key] = item
        if "warnings" in raw:
            warnings = raw["warnings"]
            if not isinstance(warnings, list) or any(not isinstance(item, str) or not _safe_scalar(item) for item in warnings):
                raise DataUnavailableError("导出警告快照不可用")
            record["warnings"] = list(warnings)
        if "input_snapshot" in raw:
            try:
                from pydantic import TypeAdapter
                from chrom_platform.modules.prediction.schemas import PredictionInputSnapshot

                record["input_snapshot"] = TypeAdapter(PredictionInputSnapshot).validate_python(
                    raw["input_snapshot"]
                ).model_dump(mode="json")
            except (TypeError, ValueError):
                # Older non-contract snapshots are omitted instead of echoed.
                pass
        if "result" in raw:
            result = raw["result"]
            if isinstance(result, Mapping) and isinstance(result.get("task_type"), str):
                try:
                    from pydantic import TypeAdapter
                    from chrom_platform.modules.prediction.schemas import PredictionResult

                    record["result"] = TypeAdapter(PredictionResult).validate_python(result).model_dump(mode="json")
                except (TypeError, ValueError):
                    raise DataUnavailableError("导出结果快照不可验证") from None
            else:
                record["result"] = _project_legacy_json(result)
        output["records"].append(record)
    return output


def _public_export_error(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        return {"code": "EXPORT_GENERATION_FAILED", "message": "导出生成失败", "retryable": True}
    code = value.get("code")
    if not isinstance(code, str) or code not in _EXPORT_ERROR_MESSAGES:
        code = "EXPORT_GENERATION_FAILED"
    retryable = value.get("retryable")
    return {
        "code": code,
        "message": _EXPORT_ERROR_MESSAGES[code],
        "retryable": retryable if isinstance(retryable, bool) else True,
    }


def _source_records_visible(session: Session, project_id: str, snapshots: object) -> bool:
    if not isinstance(snapshots, Mapping):
        return False
    records = snapshots.get("records")
    if not isinstance(records, list) or not records:
        return False
    record_ids = [
        item.get("record_id") for item in records
        if isinstance(item, Mapping) and isinstance(item.get("record_id"), str) and item.get("record_id")
    ]
    if len(record_ids) != len(records):
        return False
    visible_ids = set(session.scalars(select(PredictionRecordRow.id).where(
        PredictionRecordRow.project_id == project_id,
        PredictionRecordRow.id.in_(set(record_ids)),
    )).all())
    return visible_ids == set(record_ids)


def _discard_unpublished_export(session: Session, store: ArtifactStore | None, artifact_id: str | None, storage_key: str | None) -> None:
    if store is None or artifact_id is None or storage_key is None:
        return
    try:
        artifact = session.scalar(select(Artifact.id).where(
            Artifact.id == artifact_id,
            Artifact.storage_key == storage_key,
        ))
        if artifact is None:
            store.remove(storage_key)
    except Exception:
        # Keep lease loss as the surfaced control-flow error.  Unreferenced
        # objects remain safe and are covered by the store's orphan sweeper.
        pass


class SQLAlchemyExportArtifactReadPolicy:
    """Recheck export and source-record visibility for Artifact API reads."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self.session_factory = session_factory

    def can_read_export(self, artifact: Artifact, actor: ActorContext) -> bool:
        if not actor.can_access_project(artifact.project_id) or not actor.can("prediction:read"):
            return False
        metadata = artifact.metadata_json if isinstance(artifact.metadata_json, Mapping) else {}
        source_ref = metadata.get("source_ref")
        if not isinstance(source_ref, Mapping) or source_ref.get("type") != "export":
            return False
        export_id = source_ref.get("id")
        if not isinstance(export_id, str) or not export_id:
            return False

        with self.session_factory() as session:
            export = session.scalar(select(ExportRow).where(
                ExportRow.id == export_id,
                ExportRow.project_id == artifact.project_id,
                ExportRow.artifact_id == artifact.id,
                ExportRow.status == JobStatus.SUCCEEDED.value,
            ))
            if export is None:
                return False
            if not _source_records_visible(session, artifact.project_id, export.source_snapshots):
                return False
            try:
                _public_export_snapshots(export.source_snapshots)
                _public_export_error(export.error)
            except DataUnavailableError:
                return False
            return True

    def validate_export_body(self, artifact: Artifact, actor: ActorContext, content: bytes) -> bool:
        """Accept only bytes matching the trusted renderer's public business payload."""
        if len(content) > 50 * 1024 * 1024 or not actor.can_access_project(artifact.project_id):
            return False
        metadata = artifact.metadata_json if isinstance(artifact.metadata_json, Mapping) else {}
        source_ref = metadata.get("source_ref")
        if not isinstance(source_ref, Mapping) or source_ref.get("type") != "export":
            return False
        export_id = source_ref.get("id")
        if not isinstance(export_id, str) or not export_id:
            return False
        with self.session_factory() as session:
            export = session.scalar(select(ExportRow).where(
                ExportRow.id == export_id, ExportRow.project_id == artifact.project_id,
                ExportRow.artifact_id == artifact.id, ExportRow.status == JobStatus.SUCCEEDED.value,
            ))
            if export is None or not _source_records_visible(session, artifact.project_id, export.source_snapshots):
                return False
            try:
                projected = _public_export_snapshots(export.source_snapshots)
                expected, media_type, extension = render_export(export.format, dict(projected))
            except (DataUnavailableError, TypeError, ValueError, OverflowError):
                return False
            if artifact.media_type != media_type:
                return False
            if extension != "xlsx":
                return content == expected
            return _xlsx_business_payload_matches(content, expected)


def _xlsx_business_payload_matches(content: bytes, expected: bytes) -> bool:
    """Compare current renderer XML while allowing harmless ZIP timestamps/compression."""
    allowed = {
        "[Content_Types].xml", "_rels/.rels", "xl/workbook.xml",
        "xl/_rels/workbook.xml.rels", "xl/worksheets/sheet1.xml",
    }
    if len(content) > 50 * 1024 * 1024:
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as actual_zip, zipfile.ZipFile(io.BytesIO(expected)) as expected_zip:
            actual_infos = actual_zip.infolist()
            expected_names = set(expected_zip.namelist())
            if (
                actual_zip.comment or len(actual_infos) != len(allowed)
                or {item.filename for item in actual_infos} != allowed
                or expected_names != allowed
                or len({item.filename for item in actual_infos}) != len(actual_infos)
                or sum(item.file_size for item in actual_infos) > 10 * 1024 * 1024
                or any(item.flag_bits & 1 or item.comment or item.extra or item.file_size > 10 * 1024 * 1024 for item in actual_infos)
            ):
                return False
            # Current exports use contiguous, non-encrypted local records with
            # no ZIP extras/descriptors. Verify every byte outside compressed
            # member payloads belongs to that supported container layout.
            cursor = 0
            compressed_members: list[tuple[zipfile.ZipInfo, bytes]] = []
            for info in actual_infos:
                if info.header_offset != cursor or cursor + 30 > len(content):
                    return False
                signature, _version, flags, method, _time, _date, crc, compressed, size, name_len, extra_len = struct.unpack_from("<IHHHHHIIIHH", content, cursor)
                if signature != 0x04034B50 or flags != 0 or extra_len != 0 or method != info.compress_type:
                    return False
                name_start = cursor + 30
                data_start = name_start + name_len + extra_len
                name_bytes = content[name_start:name_start + name_len]
                if name_bytes.decode("utf-8") != info.filename or (crc, compressed, size) != (info.CRC, info.compress_size, info.file_size):
                    return False
                compressed_members.append((info, content[data_start:data_start + compressed]))
                cursor = data_start + compressed
            for info, compressed_data in compressed_members:
                if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                    return False
                actual_member = actual_zip.read(info.filename)
                expected_member = expected_zip.read(info.filename)
                if actual_member != expected_member:
                    return False
                if info.compress_type == zipfile.ZIP_STORED:
                    if compressed_data != actual_member:
                        return False
                else:
                    inflater = zlib.decompressobj(-15)
                    inflated = inflater.decompress(compressed_data, 10 * 1024 * 1024 + 1)
                    if len(inflated) > 10 * 1024 * 1024:
                        return False
                    inflated += inflater.flush()
                    if not inflater.eof or inflater.unused_data or inflater.unconsumed_tail or inflated != actual_member:
                        return False
            # The supported renderer never emits ZIP64 or a trailing payload.
            eocd = content.rfind(b"PK\x05\x06")
            if eocd < 0 or eocd + 22 > len(content):
                return False
            comment_length = int.from_bytes(content[eocd + 20:eocd + 22], "little")
            if eocd + 22 + comment_length != len(content) or comment_length != 0:
                return False
            central_size = int.from_bytes(content[eocd + 12:eocd + 16], "little")
            central_offset = int.from_bytes(content[eocd + 16:eocd + 20], "little")
            if cursor != central_offset or central_offset + central_size != eocd:
                return False
            central_cursor = central_offset
            for info in actual_infos:
                if central_cursor + 46 > eocd:
                    return False
                fields = struct.unpack_from("<IHHHHHHIIIHHHHHII", content, central_cursor)
                signature, _made_by, _needed, flags, method, _time, _date, crc, compressed, size, name_len, extra_len, note_len, disk, _internal, _external, local_offset = fields
                if (
                    signature != 0x02014B50 or flags != 0 or method != info.compress_type
                    or extra_len != 0 or note_len != 0 or disk != 0
                    or (crc, compressed, size, local_offset) != (info.CRC, info.compress_size, info.file_size, info.header_offset)
                ):
                    return False
                name_start = central_cursor + 46
                if content[name_start:name_start + name_len].decode("utf-8") != info.filename:
                    return False
                central_cursor = name_start + name_len + extra_len + note_len
            return central_cursor == eocd
    except (OSError, ValueError, UnicodeDecodeError, struct.error, zlib.error, zipfile.BadZipFile, RuntimeError):
        return False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(value: datetime) -> str:
    normalized = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return normalized.isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _audit(session: Session, *, project_id: str, actor_id: str, auth_method: str, interaction: str, request_id: str, action: str, export_id: str, job_id: str, result: str, after: dict[str, Any]) -> None:
    session.add(AuditEvent(
        id=_id("audit"), project_id=project_id, actor_id=actor_id,
        auth_method=auth_method, interaction=interaction, request_id=request_id,
        action=action, resource_type="export", resource_id=export_id,
        resource_ref={"type": "export", "id": export_id}, before_summary={},
        after_summary=after, confirmation_id=None, config_hash=None, job_id=job_id,
        result=result, timestamp=_now(),
    ))


class SQLAlchemyExportRepository:
    """Caller-owned transaction adapter for export snapshots and Jobs."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, request: ExportCreate, *, actor: ActorContext, request_id: str) -> ExportAccepted:
        if not actor.can_access_project(request.project_id):
            raise DataNotFoundError("项目不存在或当前主体不可见")
        if not actor.can("prediction:read"):
            raise DataValidationError("当前主体无权导出预测记录")
        if any(ref.type != "prediction_record" for ref in request.resource_refs):
            raise DataValidationError("本导出仅支持 prediction_record")

        records: list[PredictionRecordRow] = []
        for ref in request.resource_refs:
            row = self.session.scalar(select(PredictionRecordRow).where(
                PredictionRecordRow.project_id == request.project_id,
                PredictionRecordRow.id == ref.id,
            ))
            if row is None:
                raise DataNotFoundError("预测记录不存在或当前主体不可见")
            records.append(row)

        generated_at = _now()
        items: list[dict[str, Any]] = []
        for row in records:
            item: dict[str, Any] = {
                "record_id": row.id,
                "created_at": _iso_utc(row.created_at),
                "status": row.status,
                "source": row.source,
                "chromatography_type": row.chromatography_type,
            }
            if "input_snapshot" in request.include:
                item["input_snapshot"] = _public_prediction_input_snapshot(row.input_snapshot)
            if "model_version" in request.include:
                snapshot = dict(row.model_snapshot or {})
                item["model_version"] = snapshot.get("version", snapshot.get("model_version"))
            if "warnings" in request.include:
                item["warnings"] = list(row.warnings or [])
            if "result" in request.include:
                prediction = None
                ref_id = (row.result_ref or {}).get("id") if isinstance(row.result_ref, dict) else None
                prediction_id = row.prediction_id or ref_id
                if prediction_id:
                    prediction = self.session.scalar(select(PredictionRow).where(
                        PredictionRow.project_id == request.project_id,
                        PredictionRow.id == prediction_id,
                    ))
                if prediction is None or prediction.status != JobStatus.SUCCEEDED.value or prediction.result is None:
                    raise DataUnavailableError("所选预测记录没有可导出的成功结果")
                item["result"] = dict(prediction.result)
            items.append(item)

        snapshots = {
            "format_version": "1.0",
            "generated_at": generated_at.isoformat(),
            "include": list(request.include),
            "records": items,
        }
        export_id = _id("export")
        job = add_job_rows(
            SQLAlchemyTaskRepository(lambda: self.session), self.session,
            project_id=request.project_id, owner_id=actor.actor_id, kind="export",
            resource_ref=ResourceRef(type="export", id=export_id), cancelable=True,
            retryable=True, resume_mode="restart", deadline_at=None,
        )
        self.session.add(ExportRow(
            id=export_id, project_id=request.project_id, job_id=job.id,
            format=request.format, format_version="1.0", source_snapshots=snapshots,
            status=JobStatus.QUEUED.value, artifact_id=None, error=None,
            created_at=generated_at, completed_at=None,
        ))
        _audit(
            self.session, project_id=request.project_id, actor_id=actor.actor_id,
            auth_method=actor.auth_method, interaction="human", request_id=request_id,
            action="records.export", export_id=export_id, job_id=job.id, result="SUCCEEDED",
            after={"status": "QUEUED", "format": request.format, "format_version": "1.0", "record_count": len(items), "include": list(request.include)},
        )
        self.session.flush()
        return ExportAccepted(id=export_id, job_id=job.id, status="QUEUED", resource_ref=ResourceRef(type="export", id=export_id))

    def get(self, export_id: str, *, actor: ActorContext) -> ExportView:
        row = self.session.scalar(select(ExportRow).where(ExportRow.id == export_id))
        if row is None or not actor.can_access_project(row.project_id):
            raise DataNotFoundError("导出任务不存在或当前主体不可见")
        if not actor.can("prediction:read"):
            raise DataNotFoundError("导出任务不存在或当前主体不可见")
        if not _source_records_visible(self.session, row.project_id, row.source_snapshots):
            raise DataNotFoundError("导出任务不存在或当前主体不可见")
        job = self.session.scalar(select(Job).where(Job.id == row.job_id, Job.project_id == row.project_id))
        if job is None:
            raise DataUnavailableError("导出任务状态不可用")
        return ExportView(
            id=row.id, job_id=row.job_id, status=job.status, format=row.format,
            format_version=row.format_version, source_snapshots=_public_export_snapshots(row.source_snapshots),
            artifact_id=row.artifact_id, error=_public_export_error(row.error), created_at=row.created_at,
            completed_at=row.completed_at,
        )


def _safe_cell(value: Any) -> str:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    elif value is None:
        text = ""
    else:
        text = str(value)
    probe = text.lstrip(" \t\r\n")
    if probe.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text


def _columns(snapshots: dict[str, Any]) -> list[str]:
    base = ["format_version", "generated_at", "record_id", "created_at", "status", "source", "chromatography_type"]
    selected = snapshots.get("include", [])
    return base + [name for name in ("input_snapshot", "result", "model_version", "warnings") if name in selected]


def _csv_bytes(snapshots: dict[str, Any]) -> bytes:
    output = io.StringIO(newline="")
    records = snapshots["records"]
    columns = _columns(snapshots)
    writer = csv.DictWriter(output, fieldnames=columns, lineterminator="\r\n", extrasaction="ignore")
    writer.writeheader()
    for item in records:
        writer.writerow({key: _safe_cell(snapshots.get(key, item.get(key))) for key in columns})
    return output.getvalue().encode("utf-8-sig")


def _xlsx_bytes(snapshots: dict[str, Any]) -> bytes:
    columns = _columns(snapshots)
    rows = [columns]
    for item in snapshots["records"]:
        rows.append([_safe_cell(snapshots.get(key, item.get(key))) for key in columns])
    def col(n: int) -> str:
        out = ""
        while n:
            n, rem = divmod(n - 1, 26)
            out = chr(65 + rem) + out
        return out
    xml_rows = []
    for row_index, values in enumerate(rows, 1):
        cells = []
        for col_index, value in enumerate(values, 1):
            ref = f"{col(col_index)}{row_index}"
            cells.append(f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(str(value))}</t></is></c>')
        xml_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheetData>{"".join(xml_rows)}</sheetData></worksheet>')
    workbook = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="records" sheetId="1" r:id="rId1"/></sheets></workbook>')
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>')
    workbook_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '</Relationships>')
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return output.getvalue()


def render_export(format: str, source_snapshots: dict[str, Any]) -> tuple[bytes, str, str]:
    source_snapshots = _public_export_snapshots(source_snapshots)
    envelope = {"format_version": "1.0", **source_snapshots}
    if format == "json":
        return json.dumps(envelope, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).encode("utf-8"), "application/json", "json"
    if format == "csv":
        return _csv_bytes(source_snapshots), "text/csv; charset=utf-8", "csv"
    if format == "xlsx":
        return _xlsx_bytes(source_snapshots), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
    raise ValueError("EXPORT_FORMAT_UNSUPPORTED")


def execute_export_job(session: Session, *, job: Job, store: ArtifactStore | None, dispatch_id: str, lease_guard: object | None = None) -> dict[str, Any]:
    """Generate and publish one export under the existing Job transaction."""
    row = session.scalar(select(ExportRow).where(ExportRow.id == str((job.resource_ref or {}).get("id", "")), ExportRow.project_id == job.project_id).with_for_update())
    if row is None or row.job_id != job.id or (job.resource_ref or {}).get("type") != "export":
        raise RuntimeError("EXPORT_JOB_BINDING_INVALID")
    now = _now()
    if lease_guard is not None:
        lease_guard.check()
    if job.status == JobStatus.CANCEL_REQUESTED.value:
        row.status, row.error, row.completed_at = JobStatus.CANCELED.value, {"code": "EXPORT_CANCELED", "message": "导出已取消", "retryable": True}, now
        if sync_job_lifecycle(SQLAlchemyTaskRepository(lambda: session), session, job_id=job.id, status=JobStatus.CANCELED, attempt=job.attempt, expected_attempt=job.attempt, dispatch_id=dispatch_id, resource_ref=job.resource_ref, error=row.error, finished_at=now) is None:
            raise RuntimeError("EXPORT_JOB_FENCE_INVALID")
        return {"status": "CANCELED"}
    if sync_job_lifecycle(SQLAlchemyTaskRepository(lambda: session), session, job_id=job.id, status=JobStatus.RUNNING, attempt=job.attempt, expected_attempt=job.attempt, dispatch_id=dispatch_id, resource_ref=job.resource_ref, started_at=now) is None:
        raise RuntimeError("EXPORT_JOB_FENCE_INVALID")
    row.status, row.error, row.completed_at = JobStatus.RUNNING.value, None, None
    savepoint = session.begin_nested()
    artifact_id: str | None = None
    storage_key: str | None = None
    try:
        if store is None or not store.configured:
            raise RuntimeError("EXPORT_STORAGE_UNAVAILABLE")
        if lease_guard is not None:
            lease_guard.check()
        body, media_type, extension = render_export(row.format, dict(row.source_snapshots or {}))
        artifact_id = "artifact_" + hashlib.sha256((row.id + ":export:1.0").encode("utf-8")).hexdigest()[:32]
        storage_key = store.key_for(artifact_id)
        storage_key, size, digest = store.put(artifact_id, body, expected_size=len(body))
        if lease_guard is not None:
            lease_guard.check()
    except LeaseLostError:
        if savepoint.is_active:
            savepoint.rollback()
        _discard_unpublished_export(session, store, artifact_id, storage_key)
        raise
    except Exception as exc:
        if savepoint.is_active:
            savepoint.rollback()
        if lease_guard is not None:
            lease_guard.check()
        code = str(exc) if re.fullmatch(r"[A-Z][A-Z0-9_]{2,95}", str(exc)) else "EXPORT_GENERATION_FAILED"
        error = {"code": code, "message": "导出生成失败", "retryable": True}
        row.status, row.error, row.completed_at = JobStatus.FAILED.value, error, _now()
        if sync_job_lifecycle(SQLAlchemyTaskRepository(lambda: session), session, job_id=job.id, status=JobStatus.FAILED, attempt=job.attempt, expected_attempt=job.attempt, dispatch_id=dispatch_id, resource_ref=job.resource_ref, error=error, finished_at=row.completed_at) is None:
            raise RuntimeError("EXPORT_JOB_FENCE_INVALID") from exc
        _audit(session, project_id=row.project_id, actor_id=job.owner_id, auth_method="service", interaction="worker", request_id=f"worker:{job.id}", action="export.generate", export_id=row.id, job_id=job.id, result="FAILED", after={"status": row.status, "error_code": code})
        session.flush()
        if lease_guard is not None:
            lease_guard.check()
        return {"status": "FAILED", "error": error}

    try:
        artifact = session.scalar(select(Artifact).where(Artifact.id == artifact_id, Artifact.project_id == row.project_id).with_for_update())
        if artifact is None:
            artifact = Artifact(
                id=artifact_id, project_id=row.project_id, kind="export_report",
                title=f"prediction-records-{row.id}.{extension}", media_type=media_type,
                size_bytes=size, sha256=digest, storage_key=storage_key,
                status="AVAILABLE", scan_status="PASSED",
                metadata_json={"file_name": f"prediction-records-{row.id}.{extension}", "source_ref": {"type": "export", "id": row.id}, "generation": "server_export_v1"},
                created_by=job.owner_id,
            )
            session.add(artifact)
        else:
            if artifact.sha256 != digest or artifact.size_bytes != size:
                raise RuntimeError("EXPORT_ARTIFACT_CONTENT_CONFLICT")
            artifact.status, artifact.scan_status = "AVAILABLE", "PASSED"
        row.artifact_id = artifact_id
        row.status, row.error, row.completed_at = JobStatus.SUCCEEDED.value, None, _now()
        result_ref = {"type": "artifact", "id": artifact_id}
        if sync_job_lifecycle(SQLAlchemyTaskRepository(lambda: session), session, job_id=job.id, status=JobStatus.SUCCEEDED, attempt=job.attempt, expected_attempt=job.attempt, dispatch_id=dispatch_id, resource_ref=job.resource_ref, result_ref=result_ref, finished_at=row.completed_at) is None:
            raise RuntimeError("EXPORT_JOB_FENCE_INVALID")
        _audit(session, project_id=row.project_id, actor_id=job.owner_id, auth_method="service", interaction="worker", request_id=f"worker:{job.id}", action="export.generate", export_id=row.id, job_id=job.id, result="SUCCEEDED", after={"status": row.status, "artifact_id": artifact_id, "sha256": digest, "size_bytes": size})
        session.flush()
        if lease_guard is not None:
            lease_guard.check()
        savepoint.commit()
        return {"status": "SUCCEEDED", "artifact_id": artifact_id, "sha256": digest}
    except LeaseLostError:
        if savepoint is not None and savepoint.is_active:
            savepoint.rollback()
        _discard_unpublished_export(session, store, artifact_id, storage_key)
        raise
    except Exception:
        if savepoint.is_active:
            savepoint.rollback()
        raise


__all__ = ["SQLAlchemyExportRepository", "execute_export_job", "render_export"]
