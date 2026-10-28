"""Pure, bounded CSV validation for explicitly trusted data-domain profiles."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .import_report import CsvImportCounts, CsvImportIssue, CsvImportValidationReport
from .schemas import DataDomainSchema, DataImport

MAX_CSV_BYTES = 16 * 1024 * 1024
MAX_RECORDS = 10_000
MAX_COLUMNS = 64
MAX_ISSUES = 500
PARSER_REVISION = "csv-import-parser-1"


class NumericBounds(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    minimum: Decimal | None = None
    maximum: Decimal | None = None

    @model_validator(mode="after")
    def valid_bounds(self) -> "NumericBounds":
        for value in (self.minimum, self.maximum):
            if value is not None and not value.is_finite():
                raise ValueError("field bounds must be finite")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("field minimum exceeds maximum")
        return self


class TrustedCsvProfile(BaseModel):
    """Build-owned CSV rules; no profile is inferred from the uploaded file."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    profile_id: str = Field(min_length=1, max_length=96)
    domain_id: str = Field(min_length=1, max_length=96)
    schema_version: str = Field(min_length=1, max_length=64)
    encoding: str = Field(min_length=1, max_length=64)
    field_bounds: dict[str, NumericBounds] = Field(default_factory=dict)
    measurement_id_field: str | None = Field(default=None, min_length=1, max_length=128)
    smiles_field: str | None = Field(default=None, min_length=1, max_length=128)


@dataclass(frozen=True)
class ParsedCsvRow:
    source_row_number: int
    identity_key: str
    normalized_values: Mapping[str, Any]
    normalized_structure: Mapping[str, Any] | None


@dataclass(frozen=True)
class CsvImportParseResult:
    rows: tuple[ParsedCsvRow, ...]
    report: CsvImportValidationReport


def make_identity_key(domain: DataDomainSchema, values: Mapping[str, Any]) -> str:
    """Return the stable key representation used by this parser and its caller."""
    def canonical(field_name: str) -> Any:
        field_type = domain.record_schema[field_name].type
        value = values.get(field_name)
        if value is None or value == "" or isinstance(value, str) and not value.strip():
            raise ValueError("identity values must be non-empty")
        if field_type in {"integer", "number"}:
            if isinstance(value, bool):
                raise ValueError("boolean is not a numeric identity")
            try:
                numeric = value if isinstance(value, Decimal) else Decimal(str(value))
            except (InvalidOperation, ValueError, TypeError) as exc:
                raise ValueError("numeric identity is invalid") from exc
            if not numeric.is_finite() or field_type == "integer" and numeric != numeric.to_integral_value():
                raise ValueError("numeric identity must be finite and match its schema type")
            if numeric == 0:
                return {"$decimal": [0, "0", 0]}
            sign, digits, exponent = numeric.as_tuple()
            digits = list(digits)
            while digits and digits[-1] == 0:
                digits.pop()
                exponent += 1
            return {"$decimal": [sign, "".join(str(digit) for digit in digits), exponent]}
        if field_type == "string" and not isinstance(value, str):
            raise ValueError("string identity must remain a string")
        if field_type == "boolean" and not isinstance(value, bool):
            raise ValueError("boolean identity must be a boolean")
        if field_type in {"date", "datetime"} and not isinstance(value, str):
            raise ValueError("date identity must be serialized as a string")
        return value

    return json.dumps(
        [domain.id, domain.version, *(canonical(field) for field in domain.identity_fields)],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _issue(
    *, row_number: int, code: str, message: str, field: str | None = None,
    raw_value: str | None = None, measurement_id: str | None = None,
) -> CsvImportIssue:
    raw = raw_value or ""
    return CsvImportIssue(
        source_row_number=row_number,
        measurement_id=measurement_id[:128] if measurement_id else None,
        field=field[:128] if field else None,
        raw_value_preview=raw[:256],
        raw_value_sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest() if raw_value is not None else None,
        raw_value_truncated=len(raw) > 256,
        code=code,
        message=message,
    )


def _typed_value(raw: str, field_type: str) -> Any:
    if field_type == "string":
        return raw
    if field_type == "integer":
        value = Decimal(raw)
        if not value.is_finite() or value != value.to_integral_value():
            raise ValueError("INVALID_INTEGER")
        return int(value)
    if field_type == "number":
        value = Decimal(raw)
        if not value.is_finite():
            raise ValueError("NON_FINITE_NUMBER")
        return value
    if field_type == "boolean":
        value = raw.casefold()
        if value == "true":
            return True
        if value == "false":
            return False
        raise ValueError("INVALID_BOOLEAN")
    if field_type == "date":
        return date.fromisoformat(raw).isoformat()
    if field_type == "datetime":
        return datetime.fromisoformat(raw).isoformat()
    raise ValueError("UNSUPPORTED_FIELD_TYPE")


def _profile_problem(
    profile: TrustedCsvProfile | None,
    data_import: DataImport,
    domain: DataDomainSchema,
    normalize_structure: Callable[[str], Mapping[str, Any]] | None,
) -> tuple[str, str] | None:
    if profile is None:
        return "TRUSTED_PROFILE_REQUIRED", "没有匹配的受信 CSV profile"
    if (profile.domain_id, profile.schema_version) != (domain.id, domain.version):
        return "TRUSTED_PROFILE_MISMATCH", "受信 profile 与数据域 schema 不匹配"
    if (data_import.domain_id, data_import.schema_version) != (domain.id, domain.version):
        return "IMPORT_DOMAIN_MISMATCH", "导入草稿与数据域 schema 不匹配"
    for field_name in profile.field_bounds:
        field = domain.record_schema.get(field_name)
        if field is None or field.type not in {"integer", "number"}:
            return "INVALID_TRUSTED_PROFILE", "受信 profile 的数值范围字段不受数据域 schema 支持"
    for field_name in (profile.measurement_id_field, profile.smiles_field):
        if field_name is not None and field_name not in domain.record_schema:
            return "INVALID_TRUSTED_PROFILE", "受信 profile 引用了数据域 schema 中不存在的字段"
    if profile.measurement_id_field is not None and profile.measurement_id_field not in domain.identity_fields:
        return "INVALID_TRUSTED_PROFILE", "measurement_id 必须属于数据域身份字段"
    if profile.measurement_id_field is not None and domain.record_schema[profile.measurement_id_field].type != "string":
        return "INVALID_TRUSTED_PROFILE", "measurement_id 字段必须是 string"
    if profile.smiles_field is not None and domain.record_schema[profile.smiles_field].type != "string":
        return "INVALID_TRUSTED_PROFILE", "smiles 字段必须是 string"
    if profile.smiles_field is not None and not callable(normalize_structure):
        return "STRUCTURE_NORMALIZER_UNAVAILABLE", "结构规范化服务未配置"
    return None


def parse_csv_import(
    content: bytes,
    data_import: DataImport,
    domain: DataDomainSchema,
    profile: TrustedCsvProfile | None,
    normalize_structure: Callable[[str], Mapping[str, Any]] | None = None,
    existing_identity_keys: set[str] | frozenset[str] = frozenset(),
) -> CsvImportParseResult:
    """Parse and validate one bounded CSV payload without I/O or registration."""
    source_sha256 = hashlib.sha256(content).hexdigest()
    issues: list[CsvImportIssue] = []
    total_issues = 0

    def add_issue(**kwargs: Any) -> None:
        nonlocal total_issues
        total_issues += 1
        if len(issues) < MAX_ISSUES:
            issues.append(_issue(**kwargs))

    def finish(
        status: str, rows: list[ParsedCsvRow], row_count: int, counts: CsvImportCounts,
    ) -> CsvImportParseResult:
        report = CsvImportValidationReport(
            status=status,
            project_id=data_import.project_id,
            import_id=data_import.id,
            revision=data_import.revision,
            config_hash=data_import.config_hash,
            domain_id=domain.id,
            domain_schema_version=domain.version,
            source_artifact_id=data_import.artifact_id,
            source_sha256=source_sha256,
            encoding=profile.encoding if profile is not None else None,
            parser_revision=PARSER_REVISION,
            row_count=row_count,
            counts=counts,
            issues=tuple(issues),
            total_issue_count=total_issues,
            issues_truncated=total_issues > len(issues),
        )
        # Fail closed if even bounded report metadata unexpectedly exceeds its cap.
        report.to_json_bytes()
        return CsvImportParseResult(tuple(rows), report)

    if len(content) > MAX_CSV_BYTES:
        add_issue(row_number=1, code="CSV_TOO_LARGE", message="CSV 超过 16 MiB 限制")
        return finish("INVALID", [], 0, CsvImportCounts(new=0, updated=0, unchanged=0, invalid=0, duplicate=0))
    if data_import.import_mode != "append" or domain.deduplication_policy != "reject":
        add_issue(row_number=1, code="UNSUPPORTED_IMPORT_MODE", message="当前 CSV parser 仅支持 append + reject")
        return finish("UNSUPPORTED_IMPORT_MODE", [], 0, CsvImportCounts(new=0, updated=0, unchanged=0, invalid=0, duplicate=0))
    problem = _profile_problem(profile, data_import, domain, normalize_structure)
    if problem:
        code, message = problem
        add_issue(row_number=1, code=code, message=message)
        status = "UNAVAILABLE" if code in {"TRUSTED_PROFILE_REQUIRED", "STRUCTURE_NORMALIZER_UNAVAILABLE"} else "INVALID"
        return finish(status, [], 0, CsvImportCounts(new=0, updated=0, unchanged=0, invalid=0, duplicate=0))
    assert profile is not None
    try:
        text = content.decode(profile.encoding, errors="strict")
    except (LookupError, UnicodeDecodeError):
        add_issue(row_number=1, code="ENCODING_ERROR", message="CSV 无法按受信 profile 指定编码严格解码")
        return finish("INVALID", [], 0, CsvImportCounts(new=0, updated=0, unchanged=0, invalid=0, duplicate=0))
    if "\x00" in text:
        add_issue(row_number=1, code="NUL_BYTE", message="CSV 含有 NUL 字符")
        return finish("INVALID", [], 0, CsvImportCounts(new=0, updated=0, unchanged=0, invalid=0, duplicate=0))

    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        headers = next(reader)
    except StopIteration:
        add_issue(row_number=1, code="EMPTY_CSV", message="CSV 缺少表头")
        return finish("INVALID", [], 0, CsvImportCounts(new=0, updated=0, unchanged=0, invalid=0, duplicate=0))
    except csv.Error:
        add_issue(row_number=max(1, reader.line_num), code="MALFORMED_CSV", message="CSV 表头格式无效")
        return finish("INVALID", [], 0, CsvImportCounts(new=0, updated=0, unchanged=0, invalid=0, duplicate=0))

    if len(headers) > MAX_COLUMNS:
        add_issue(row_number=1, code="TOO_MANY_COLUMNS", message="CSV 列数超过 64")
    if any(not header.strip() for header in headers):
        add_issue(row_number=1, code="EMPTY_HEADER", message="CSV 表头含空列名")
    if len(set(headers)) != len(headers):
        add_issue(row_number=1, code="DUPLICATE_HEADER", message="CSV 表头含重复列名")
    mapping = data_import.field_mapping
    unknown_targets = set(mapping) - set(domain.record_schema)
    if unknown_targets:
        add_issue(row_number=1, code="UNKNOWN_TARGET_FIELD", message="映射目标不属于受信数据域")
    required = {key for key, field in domain.record_schema.items() if field.required}
    missing = required - set(mapping)
    missing_identity = set(domain.identity_fields) - set(mapping)
    missing_profile_fields = {key for key in (profile.measurement_id_field, profile.smiles_field) if key is not None} - set(mapping)
    if missing or missing_identity or missing_profile_fields:
        add_issue(row_number=1, code="MISSING_FIELD_MAPPING", message="字段映射缺少必填、身份或 profile 字段")
    sources = list(mapping.values())
    if len(set(sources)) != len(sources):
        add_issue(row_number=1, code="DUPLICATE_SOURCE_MAPPING", message="多个目标字段映射到同一上传列")
    absent_sources = set(sources) - set(headers)
    if absent_sources:
        add_issue(row_number=1, code="MISSING_SOURCE_COLUMN", message="CSV 缺少映射所需列")
    for target in mapping:
        field = domain.record_schema.get(target)
        if field is None:
            continue
        declared_unit = domain.unit_definitions.get(target)
        if field.unit and declared_unit and field.unit != declared_unit:
            add_issue(row_number=1, code="UNIT_SCHEMA_MISMATCH", message="字段单位与数据域单位定义不一致", field=target)
    if total_issues:
        return finish("INVALID", [], 0, CsvImportCounts(new=0, updated=0, unchanged=0, invalid=0, duplicate=0))

    source_indexes = {header: index for index, header in enumerate(headers)}
    seen: set[str] = set()
    parsed: list[ParsedCsvRow] = []
    new_count = invalid_count = duplicate_count = row_count = 0
    previous_line = reader.line_num
    try:
        for source_values in reader:
            row_count += 1
            row_number = previous_line + 1
            previous_line = reader.line_num
            if row_count > MAX_RECORDS:
                add_issue(row_number=row_number, code="TOO_MANY_RECORDS", message="CSV 记录数超过 10000")
                invalid_count += 1
                break
            if len(source_values) != len(headers):
                add_issue(row_number=row_number, code="ROW_WIDTH_MISMATCH", message="CSV 行的列数与表头不一致")
                invalid_count += 1
                continue

            values: dict[str, Any] = {}
            row_issue_start = total_issues
            raw_by_target: dict[str, str] = {}
            measurement_id: str | None = None
            if profile.measurement_id_field:
                source_name = mapping.get(profile.measurement_id_field)
                if source_name is not None:
                    candidate = source_values[source_indexes[source_name]]
                    if candidate.strip() and len(candidate) <= 128:
                        measurement_id = candidate
            invalid_fields: set[str] = set()
            structure: Mapping[str, Any] | None = None
            for target, source_name in mapping.items():
                field = domain.record_schema[target]
                raw = source_values[source_indexes[source_name]]
                raw_by_target[target] = raw
                if raw == "" and not field.required:
                    values[target] = None
                    if target in domain.identity_fields:
                        add_issue(row_number=row_number, code="IDENTITY_VALUE_MISSING", message="身份字段不能为空", field=target, raw_value=raw, measurement_id=measurement_id)
                        invalid_fields.add(target)
                    continue
                if raw == "" and field.required:
                    add_issue(row_number=row_number, code="REQUIRED_VALUE_MISSING", message="必填字段为空", field=target, raw_value=raw, measurement_id=measurement_id)
                    invalid_fields.add(target)
                    continue
                try:
                    value = _typed_value(raw, field.type)
                except (InvalidOperation, ValueError, OverflowError):
                    code = "NON_FINITE_NUMBER" if field.type in {"integer", "number"} and raw.strip().casefold() in {"nan", "inf", "+inf", "-inf", "infinity", "+infinity", "-infinity"} else "INVALID_FIELD_VALUE"
                    add_issue(row_number=row_number, code=code, message="字段值不符合受信 schema 类型", field=target, raw_value=raw, measurement_id=measurement_id)
                    invalid_fields.add(target)
                    continue
                bounds = profile.field_bounds.get(target)
                if bounds is not None:
                    numeric_value = Decimal(value)
                    if (bounds.minimum is not None and numeric_value < bounds.minimum) or (bounds.maximum is not None and numeric_value > bounds.maximum):
                        add_issue(row_number=row_number, code="VALUE_OUT_OF_BOUNDS", message="字段值超出受信 profile 范围，原值未修改", field=target, raw_value=raw, measurement_id=measurement_id)
                values[target] = value

            if profile.measurement_id_field:
                value = values.get(profile.measurement_id_field)
                if isinstance(value, str) and value.strip() and len(value) <= 128:
                    measurement_id = value
                elif value is not None:
                    add_issue(row_number=row_number, code="INVALID_MEASUREMENT_ID", message="measurement_id 必须为非空且不超过 128 字符", field=profile.measurement_id_field, raw_value=raw_by_target.get(profile.measurement_id_field, ""), measurement_id=measurement_id)

            if profile.smiles_field:
                smiles = values.get(profile.smiles_field)
                if isinstance(smiles, str) and smiles.strip():
                    try:
                        normalized = normalize_structure(smiles)  # type: ignore[misc]
                        if not isinstance(normalized, Mapping):
                            raise TypeError("structure normalizer returned a non-mapping")
                        structure = dict(normalized)
                    except Exception:
                        add_issue(row_number=row_number, code="STRUCTURE_NORMALIZATION_FAILED", message="结构无法按受信规范化服务处理", field=profile.smiles_field, raw_value=raw_by_target.get(profile.smiles_field, ""), measurement_id=measurement_id)

            identity_values = {key: values.get(key) for key in domain.identity_fields}
            for identity_field, identity_value in identity_values.items():
                if identity_field not in invalid_fields and (
                    identity_value is None
                    or identity_value == ""
                    or isinstance(identity_value, str) and not identity_value.strip()
                ):
                    add_issue(row_number=row_number, code="IDENTITY_VALUE_MISSING", message="身份字段不能为空", field=identity_field, raw_value=raw_by_target.get(identity_field, ""), measurement_id=measurement_id)
                    invalid_fields.add(identity_field)
            identity_ready = all(
                value is not None and value != "" and not (isinstance(value, str) and not value.strip())
                for value in identity_values.values()
            )
            key = make_identity_key(domain, identity_values) if identity_ready else ""
            duplicate = bool(key and (key in existing_identity_keys or key in seen))
            if key:
                seen.add(key)
            has_row_errors = total_issues > row_issue_start
            if duplicate:
                add_issue(row_number=row_number, code="DUPLICATE_IDENTITY", message="身份键已存在或在本文件重复", field=profile.measurement_id_field, raw_value=raw_by_target.get(profile.measurement_id_field, "") if profile.measurement_id_field else None, measurement_id=measurement_id)
            if has_row_errors:
                invalid_count += 1
                continue
            if duplicate:
                duplicate_count += 1
                continue
            new_count += 1
            parsed.append(ParsedCsvRow(row_number, key, values, structure))
    except csv.Error:
        row_count += 1
        add_issue(row_number=max(1, reader.line_num), code="MALFORMED_CSV", message="CSV 行格式无效")
        invalid_count += 1

    if row_count == 0 and total_issues == 0:
        add_issue(row_number=1, code="EMPTY_CSV", message="CSV 不含数据记录")
    status = "VALID" if total_issues == 0 and row_count > 0 else "INVALID"
    counts = CsvImportCounts(new=new_count, updated=0, unchanged=0, invalid=invalid_count, duplicate=duplicate_count)
    return finish(status, parsed, row_count, counts)


__all__ = [
    "CsvImportParseResult", "NumericBounds", "ParsedCsvRow", "TrustedCsvProfile",
    "make_identity_key", "parse_csv_import",
]
