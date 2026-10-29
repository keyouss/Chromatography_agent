"""Pure CSV/XLSX row preparation for batch prediction."""

from __future__ import annotations

import csv
import io
import json
import zipfile
import xml.etree.ElementTree as ET
from typing import Any

from .batch_errors import BatchServiceError


MAX_BATCH_ROWS = 10_000
_IDENTITY_FIELDS = frozenset({"structure_ref", "structure_ref_a", "structure_ref_b", "smiles", "smiles_a", "smiles_b"})


def _decode_upload_cell(value: str, target: str) -> object:
    text = value.strip()
    if text.lower() == "null":
        return None
    if target in {"solvent_components", "modifier_components", "column_ids", "temperature_program"}:
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{target} must be JSON") from exc
    if target in {"sample_mass_mg", "loading_solvent_volume_ul", "flow_rate_ml_min", "isopropanol_fraction", "modifier_total_fraction", "fraction_1", "fraction_2"}:
        try:
            return float(text)
        except ValueError as exc:
            raise ValueError(f"{target} must be numeric") from exc
    if target == "top_k":
        try:
            return int(text)
        except ValueError as exc:
            raise ValueError("top_k must be an integer") from exc
    return text


def _validated_rows(records: list[tuple[int, dict[str, str]]], *, field_mapping: dict[str, str], row_model: type[Any], expected_fields: list[str], common_input: dict[str, Any] | None = None) -> list[tuple[int, dict[str, Any] | None, dict[str, str] | None]]:
    if len(records) - 1 > MAX_BATCH_ROWS:
        raise BatchServiceError(422, "BATCH_ROW_LIMIT_EXCEEDED", f"批量行数不能超过 {MAX_BATCH_ROWS}")
    identity = set(expected_fields) & _IDENTITY_FIELDS
    if (not set(field_mapping).issubset(expected_fields)
            or not identity.issubset(field_mapping)
            or len(set(field_mapping.values())) != len(field_mapping)):
        raise BatchServiceError(422, "BATCH_FIELDS_INVALID", "字段映射仅可包含模板字段，结构身份列必须来自每一行")
    common = dict(common_input or {})
    if not set(common).issubset(set(expected_fields) - identity):
        raise BatchServiceError(422, "BATCH_COMMON_INPUT_INVALID", "common_input 只能提供模板允许的非身份字段")
    try:
        columns = list(records[0][1]) if records else []
    except Exception as exc:
        raise BatchServiceError(422, "ARTIFACT_PARSE_FAILED", "批量制品表头无法读取") from exc
    if len(columns) != len(set(columns)) or set(columns) != set(field_mapping.values()):
        raise BatchServiceError(422, "UNKNOWN_BATCH_FIELDS", "上传文件列必须与对应模板字段完全一致")
    rows: list[tuple[int, dict[str, Any] | None, dict[str, str] | None]] = []
    for source_row_number, source in records[1:]:
        try:
            mapped = {}
            for target, column in field_mapping.items():
                cell = source.get(column, "")
                if not cell.strip():
                    continue
                mapped[target] = _decode_upload_cell(cell, target)
            # Merge by presence: a row value, including explicit null, wins;
            # only an absent/blank cell falls back to common_input.
            resolved = {**common, **mapped}
            rows.append((source_row_number, row_model.model_validate(resolved).model_dump(mode="json"), None))
        except Exception as exc:
            rows.append((source_row_number, None, {"code": "BATCH_ROW_INVALID", "message": f"第 {source_row_number} 行不符合 {row_model.__name__}"}))
    if not rows:
        raise BatchServiceError(422, "BATCH_EMPTY", "批量制品没有数据行")
    return rows


def _parse_csv(content: bytes, *, field_mapping: dict[str, str], row_model: type[Any], expected_fields: list[str], common_input: dict[str, Any] | None = None) -> list[tuple[int, dict[str, Any] | None, dict[str, str] | None]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise BatchServiceError(422, "ARTIFACT_PARSE_FAILED", "批量制品必须是 UTF-8 CSV") from exc
    reader = csv.reader(io.StringIO(text, newline=""))
    raw: list[list[str]] = []
    for values in reader:
        raw.append(values)
        # Header + 10,000 rows is accepted; the next row proves overflow.
        if len(raw) > MAX_BATCH_ROWS + 1:
            raise BatchServiceError(422, "BATCH_ROW_LIMIT_EXCEEDED", f"批量行数不能超过 {MAX_BATCH_ROWS}")
    if not raw:
        raise BatchServiceError(422, "BATCH_EMPTY", "批量制品没有表头")
    header = raw[0]
    if len(header) != len(set(header)):
        raise BatchServiceError(422, "UNKNOWN_BATCH_FIELDS", "上传文件列名不能重复")
    records: list[tuple[int, dict[str, str]]] = [(1, {column: column for column in header})]
    for index, values in enumerate(raw[1:], start=2):
        records.append((index, dict(zip(header, values)) if len(values) == len(header) else {column: "" for column in header}))
    return _validated_rows(records, field_mapping=field_mapping, row_model=row_model, expected_fields=expected_fields, common_input=common_input)


_XLSX_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _xlsx_cell_value(cell: ET.Element, shared: list[str]) -> str:
    kind = cell.attrib.get("t")
    if kind == "inlineStr":
        return "".join(item.text or "" for item in cell.findall(f"{_XLSX_NS}is/{_XLSX_NS}t"))
    value = cell.find(f"{_XLSX_NS}v")
    raw = "" if value is None else (value.text or "")
    if kind == "s":
        try:
            return shared[int(raw)]
        except (ValueError, IndexError):
            raise BatchServiceError(422, "ARTIFACT_PARSE_FAILED", "XLSX 共享字符串索引无效")
    return raw


def _parse_xlsx(content: bytes, *, field_mapping: dict[str, str], row_model: type[Any], expected_fields: list[str], common_input: dict[str, Any] | None = None) -> list[tuple[int, dict[str, Any] | None, dict[str, str] | None]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except (zipfile.BadZipFile, OSError) as exc:
        raise BatchServiceError(422, "ARTIFACT_PARSE_FAILED", "XLSX 制品结构无效") from exc
    with archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.findall(f"{_XLSX_NS}si"):
                shared.append("".join(text.text or "" for text in item.iter(f"{_XLSX_NS}t")))
        sheets = sorted(name for name in archive.namelist() if name.startswith("xl/worksheets/") and name.endswith(".xml"))
        if not sheets:
            raise BatchServiceError(422, "ARTIFACT_PARSE_FAILED", "XLSX 没有工作表")
        root = ET.fromstring(archive.read(sheets[0]))
        records: list[tuple[int, dict[str, str]]] = []
        for index, row in enumerate(root.findall(f".//{_XLSX_NS}row"), start=1):
            if index > MAX_BATCH_ROWS + 1:
                raise BatchServiceError(422, "BATCH_ROW_LIMIT_EXCEEDED", f"批量行数不能超过 {MAX_BATCH_ROWS}")
            source_number = int(row.attrib.get("r", index))
            values: dict[str, str] = {}
            for cell in row.findall(f"{_XLSX_NS}c"):
                ref = cell.attrib.get("r", "")
                column = "".join(char for char in ref if char.isalpha())
                if column:
                    values[column] = _xlsx_cell_value(cell, shared)
            if index == 1:
                header_by_column = values
                if len(values) != len(set(values.values())):
                    raise BatchServiceError(422, "UNKNOWN_BATCH_FIELDS", "上传文件列名不能重复")
                records.append((source_number, {value: value for value in values.values()}))
            else:
                records.append((source_number, {header_by_column.get(key, key): value for key, value in values.items()}))
        if not records:
            raise BatchServiceError(422, "BATCH_EMPTY", "批量制品没有数据行")
        return _validated_rows(records, field_mapping=field_mapping, row_model=row_model, expected_fields=expected_fields, common_input=common_input)
