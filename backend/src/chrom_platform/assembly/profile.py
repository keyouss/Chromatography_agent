"""Build an independently importable, physically trimmed backend profile.

The development checkout intentionally contains every platform module.  This
assembler creates a release-shaped directory from one validated build
manifest; the output is the only package a trimmed runtime needs to import.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import re
import shutil
from pathlib import Path
from typing import Iterable

from .manifest import BuildManifest, load_manifest


_PREDICTION_TYPES = ("tlc", "cc", "hplc", "chiral_lc", "sfc", "gc")
_TYPE_NAMES = {
    "tlc": (
        "SolventComponent", "TlcSolventComponent", "TlcInput", "TlcSolventOption", "TlcCandidateSystem", "TlcRange", "TlcReverseLimits", "TlcConditionOptions",
        "TlcPredictionCreate", "TlcConditionComparisonCreate", "TlcReverseCreate",
        "TlcResultRow", "TlcPredictionResult", "TlcConditionComparisonResult", "TlcReverseResult",
    ),
    "cc": ("CcInput", "CcPredictionCreate", "CcResultRow", "CcPredictionResult", "derive_cc_time_seconds"),
    "hplc": ("HplcInput", "HplcInputV10", "HplcPredictionCreate", "HplcPredictionReadV10", "HplcResultRow", "HplcPredictionResult", "validate_hplc_execution_binding"),
    "chiral_lc": ("ChiralLcConditions", "ChiralLcInput", "ChiralLcPredictionCreate", "ChiralLcResultRow", "ChiralLcPredictionResult"),
    "sfc": (
        "ModifierComponent", "_SfcConditionInput", "SfcSingleColumnInput", "SfcFullColumnInput",
        "SfcSingleColumnPredictionCreate", "SfcFullColumnPredictionCreate", "SfcCandidateConditions",
        "SfcSingleColumnPredictionReadV10", "SfcFullColumnPredictionReadV10",
        "SfcSingleColumnExecutionV10", "SfcFullColumnExecutionV10",
        "SfcCandidateScopeView", "SfcConditionOption", "SfcAllColumnDomain", "SfcSingleColumnDomain", "SfcDomain", "SfcConditionsOptions", "SfcCandidateAxis", "SfcCandidateScopeCreate",
        "SfcCandidateScopePolicy", "SfcCandidateScopeProvenance", "SfcCandidateScopePreview",
        "SfcResultRow", "SfcPredictionResult",
    ),
    "gc": ("GcLinearTemperatureProgram", "GcSeriesTemperatureProgram", "GcTemperatureProgram", "GcInput", "GcExactProfileModelSelection", "GcPredictionCreate", "GcPredictionReadV10", "GcResultRow", "GcPredictionResult", "validate_gc_execution_binding"),
}
_COMMON_NAMES = {
    "PredictionStrictModel", "Id", "BusinessScopeId", "StructureRefId", "Sha256",
    "Warning", "WarningDTO", "PublicErrorSummary", "PredictionError", "ModelSnapshot",
    "ChampionModelSelection", "ExactModelSelection", "ModelSelection", "PredictionCreateBase", "Metric",
    "Visualization", "Applicability", "PredictionAccepted", "BatchAccepted",
    "build_prediction_execution_request",
}
_PREDICTION_ALIASES = {"PredictionCreate", "PredictionRequest", "PredictionExecutionRequest", "TypedPredictionResult", "PredictionResult", "SfcPredictionCreate", "SfcPredictionRequest", "CCInput", "HPLCInput", "SFCInput", "GCInput", "ChiralLCInput", "HplcPredictionInput", "ChiralLcPredictionInput", "Error"}


def _node_name(node: ast.AST) -> str | None:
    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        return node.name
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        target = node.targets[0] if isinstance(node, ast.Assign) and node.targets else node.target if isinstance(node, ast.AnnAssign) else None
        return target.id if isinstance(target, ast.Name) else None
    return None


def _prediction_schemas(source: str, selected: set[str], *, batch_enabled: bool = False) -> str:
    tree = ast.parse(source)
    keep = _COMMON_NAMES | {name for kind in selected for name in _TYPE_NAMES[kind]}
    # Keep the private persisted execution snapshot and its DTO mapper in
    # every profile. They are required by shared prediction persistence even
    # though the public Prediction DTO intentionally exposes only
    # PredictionInputSnapshot.
    private_snapshot_support = {
        "ModifierStructureSnapshot",
        "PredictionExecutionInputSnapshot",
        "to_public_prediction_input_snapshot",
    }
    keep.update(private_snapshot_support)
    if "sfc" in selected:
        keep.update({"PredictionExecutionRequest", "build_prediction_execution_request"})
    if not batch_enabled:
        keep.discard("BatchAccepted")
    keep.update({"_prediction_discriminator"} | ({"_sfc_discriminator", "SfcPredictionCreate", "SfcPredictionRequest", "SFCInput"} if "sfc" in selected else set()))
    # Include private function dependencies referenced by retained public
    # declarations, transitively. This keeps validator behavior intact while
    # still removing helpers used only by unselected chromatography types.
    private_functions = {
        node.name: node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("_")
    }
    declarations = {
        name: node for node in tree.body
        if (name := _node_name(node)) is not None
    }
    pending = [declarations[name] for name in keep if name in declarations]
    required_helpers: set[str] = set()
    while pending:
        node = pending.pop()
        for item in ast.walk(node):
            if isinstance(item, ast.Name) and item.id in private_functions and item.id not in required_helpers:
                required_helpers.add(item.id)
                pending.append(private_functions[item.id])
    keep.update(required_helpers)
    chunks: list[str] = []
    private_snapshot_chunks: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            chunks.append(ast.get_source_segment(source, node) or "")
            continue
        name = _node_name(node)
        if name in {"_prediction_discriminator", "_sfc_discriminator"}:
            continue
        if name in keep:
            segment = ast.get_source_segment(source, node) or ""
            if name == "PredictionInputSnapshot":
                continue
            if name == "Prediction":
                continue
            if name in _PREDICTION_ALIASES:
                continue
            if name in private_snapshot_support:
                private_snapshot_chunks.append(segment)
                continue
            chunks.append(segment)
    selected_create = [f"{kind.title().replace('_', '')}PredictionCreate" for kind in ()]  # keeps the generator's type mapping explicit
    class_names = {name for kind in selected for name in _TYPE_NAMES[kind]}
    # The source declarations are assembled from sets above.  Sort every
    # generated union so Python's per-process hash randomization cannot alter
    # the package's OpenAPI bytes between two isolated builds.
    create_names = sorted(name for name in class_names if name.endswith("PredictionCreate"))
    result_names = sorted(name for name in class_names if name.endswith("PredictionResult"))
    if "tlc" in selected:
        result_names = sorted([*result_names, "TlcConditionComparisonResult", "TlcReverseResult"])
    input_names = sorted(name for name in class_names if name.endswith("Input") and name not in {"PredictionInputSnapshot"})
    if "hplc" in selected:
        input_names.append("HplcInputV10")
    tag_map = {
        "TlcPredictionCreate": "tlc", "CcPredictionCreate": "cc", "HplcPredictionCreate": "hplc",
        "ChiralLcPredictionCreate": "chiral_lc", "SfcSingleColumnPredictionCreate": "sfc_single_column",
        "SfcFullColumnPredictionCreate": "sfc_full_column", "GcPredictionCreate": "gc",
    }
    create_lines = [f'    Annotated[{name}, Tag("{tag_map[name]}")]' for name in create_names]
    # SFC tags are derived from tool in the original discriminator; keep the
    # same explicit classes while avoiding references to trimmed types.
    union_members = "\n    | ".join(create_lines).replace("    Annotated[", "Annotated[")
    result_union = "\n    | ".join(result_names) or "PublicErrorSummary"
    type_literals = ", ".join(repr(kind) for kind in sorted(selected))
    input_union = " | ".join(input_names) or "PublicErrorSummary"
    aliases = ["PredictionRequest = PredictionCreate"]
    if "sfc" not in selected:
        aliases.append("PredictionExecutionRequest = PredictionCreate")
    legacy_read_types = [(kind, name) for kind, name in (
        ("hplc", "HplcPredictionReadV10"), ("gc", "GcPredictionReadV10")
    ) if kind in selected]
    if "sfc" in selected:
        legacy_read_types.extend((
            ("sfc_single_column", "SfcSingleColumnPredictionReadV10"),
            ("sfc_full_column", "SfcFullColumnPredictionReadV10"),
        ))
    if legacy_read_types:
        stored_members = union_members + "".join(
            f'\n    | Annotated[{name}, Tag("{kind}_v10")]'
            for kind, name in legacy_read_types
        )
        legacy_discriminator = "".join(
            f'    if data.get("chromatography_type") == "{kind}" and data.get("schema_version") == "1.0":\n'
            f'        return "{kind}_v10"\n'
            for kind, _ in legacy_read_types if kind in {"hplc", "gc"}
        )
        if "sfc" in selected:
            legacy_discriminator += (
                '    if data.get("chromatography_type") == "sfc" and data.get("schema_version") == "1.0":\n'
                '        return f"sfc_{data.get(\'tool\')}_v10"\n'
            )
        aliases.append(
            'def _stored_prediction_discriminator(value: object) -> str | None:\n'
            '    data = value if isinstance(value, dict) else getattr(value, "__dict__", {})\n'
            + legacy_discriminator +
            '    return _prediction_discriminator(value)\n\n'
            f'PredictionStoredRead: TypeAlias = Annotated[{stored_members}, Discriminator(_stored_prediction_discriminator)]'
        )
    else:
        aliases.append("PredictionStoredRead = PredictionCreate")
    if "sfc" in selected:
        aliases.extend([
            "SfcPredictionCreate = Annotated[Annotated[SfcSingleColumnPredictionCreate, Tag('sfc_single_column')] | Annotated[SfcFullColumnPredictionCreate, Tag('sfc_full_column')], Discriminator(_sfc_discriminator)]",
            "SfcPredictionRequest = SfcPredictionCreate",
            "SFCInput = SfcSingleColumnInput | SfcFullColumnInput",
            "PredictionExecutionRequest = PredictionCreate | SfcSingleColumnExecutionV10 | SfcFullColumnExecutionV10",
        ])
    for alias_name, type_id, target in (("CCInput", "cc", "CcInput"), ("HPLCInput", "hplc", "HplcInput"), ("GCInput", "gc", "GcInput"), ("ChiralLCInput", "chiral_lc", "ChiralLcInput")):
        if type_id in selected:
            aliases.append(f"{alias_name} = {target}")
    exports = sorted((keep - private_snapshot_support - {"_prediction_discriminator", "_sfc_discriminator"}) | {"PredictionInputSnapshot", "Prediction", "PredictionCreate", "PredictionStoredRead", "PredictionRequest", "TypedPredictionResult", "PredictionResult"} | {alias.split(" = ", 1)[0] for alias in aliases if " = " in alias and not alias.startswith("def ")})
    discriminator_body = (
        '    if chromatography_type == "sfc":\n'
        '        return f"sfc_{data.get(\'tool\')}"\n'
        if "sfc" in selected
        else ""
    )
    sfc_discriminator_definition = (
        "\ndef _sfc_discriminator(value: object) -> str | None:\n"
        "    data = value if isinstance(value, dict) else getattr(value, \"__dict__\", {})\n"
        "    return f\"sfc_{data.get('tool')}\"\n"
        if "sfc" in selected
        else ""
    )
    generated = f'''

class PredictionInputSnapshot(PredictionStrictModel):
    structure_refs: list[StructureRefId] = Field(min_length=1, max_length=2)
    normalized_structures: list[Annotated[str, Field(min_length=1, max_length=20_000)]] = Field(min_length=1, max_length=2)
    input: {input_union}

def _prediction_discriminator(value: object) -> str | None:
    data = value if isinstance(value, dict) else getattr(value, "__dict__", {{}})
    chromatography_type = data.get("chromatography_type")
{discriminator_body}    
    return chromatography_type
{sfc_discriminator_definition}

PredictionCreate: TypeAlias = Annotated[
    {union_members},
    Discriminator(_prediction_discriminator),
]
{chr(10).join(aliases)}
TypedPredictionResult: TypeAlias = Annotated[{result_union}, Field(discriminator="task_type")]
PredictionResult = TypedPredictionResult

__all__ = {exports!r}

for _model_name in __all__:
    _model = globals().get(_model_name)
    if isinstance(_model, type) and issubclass(_model, BaseModel):
        _model.model_rebuild(_types_namespace=globals(), raise_errors=False)

class Prediction(PredictionStrictModel):
    id: Id
    project_id: Id
    prediction_record_id: Id
    job_id: Id
    chromatography_type: Literal[{type_literals}]
    tool: Annotated[str, Field(min_length=1, max_length=96)]
    schema_version: Annotated[str, Field(min_length=1, max_length=64)]
    status: JobStatus
    model: ModelSnapshot
    input_snapshot: PredictionInputSnapshot
    result: TypedPredictionResult | None = None
    warnings: list[Warning] = Field(default_factory=list)
    error: PublicErrorSummary | None = None
    source: Literal["web", "assistant", "service"]
    conversation_id: Id | None = None
    agent_run_id: Id | None = None
    created_at: datetime
    completed_at: datetime | None = None
    data_origin: Literal["production", "fixture"]
    revision: int = Field(ge=1)
    etag: Annotated[str, Field(min_length=1, max_length=128)]
    updated_at: datetime

Prediction.model_rebuild(_types_namespace=globals(), raise_errors=False)
'''
    return "\n\n".join(chunks) + generated + "\n\n" + "\n\n".join(private_snapshot_chunks) + "\n"


def _trim_prediction_adapters(
    source: str,
    selected: set[str],
    *,
    batch_enabled: bool = False,
    preserve_batch_contract: bool = False,
) -> str:
    selected_literal = "{" + ", ".join(repr(item) for item in sorted(selected)) + "}"
    source = re.sub(r"_SUPPORTED_TYPES\s*=\s*frozenset\([^\n]+\)", f"_SUPPORTED_TYPES = frozenset({selected_literal})", source, count=1)
    schema_match = re.search(r"from \.schemas import \(.*?\n\)", source, flags=re.S)
    if schema_match:
        block = schema_match.group(0)
        names = [
            part.strip()
            for part in block[block.find("(") + 1:block.rfind(")")].replace("\n", " ").split(",")
            if part.strip()
        ]
        names = [name for name in names if not name.startswith("Sfc") or "sfc" in selected]
        if "sfc" in selected:
            for name in (
                "SfcSingleColumnExecutionV10", "SfcFullColumnExecutionV10",
            ):
                if name not in names:
                    names.append(name)
        source = re.sub(
            r"from \.schemas import \(.*?\n\)",
            "from .schemas import (\n" + "\n".join(f"    {name}," for name in names) + "\n)",
            source,
            count=1,
            flags=re.S,
        )
    if not batch_enabled and not preserve_batch_contract:
        source = source.replace('Literal["single", "pair", "batch"]', 'Literal["single", "pair"]')
    if "sfc" not in selected:
        source = source.replace("    sfc_domain: SfcDomain | None = None\n", "")
        source = source.replace(
            '        if self.task_type != "sfc" and self.sfc_domain is not None:\n'
            '            raise ValueError("sfc_domain is only valid for SFC models")\n',
            "",
        )
        source = re.sub(r'^    "Sfc(?:AllColumnDomain|Domain|SingleColumnDomain)",\n', "", source, flags=re.M)
    return source


def _trim_prediction_service(source: str, selected: set[str]) -> str:
    selected_order = sorted(selected)
    names = [name for kind in selected_order for name in _TYPE_NAMES[kind] if name.endswith("PredictionCreate")]
    names += ["ModelSnapshot", "ModifierStructureSnapshot", "PredictionAccepted", "PredictionCreate", "PredictionInputSnapshot", "PredictionExecutionInputSnapshot"]
    block = "from .schemas import (\n" + "\n".join(f"    {name}," for name in dict.fromkeys(names)) + "\n)"
    source = re.sub(r"from \.schemas import \(.*?\n\)\n", block + "\n", source, count=1, flags=re.S)
    create = ", ".join(names for names in [name for kind in selected_order for name in _TYPE_NAMES[kind] if name.endswith("PredictionCreate")]) or "PredictionCreate"
    source = re.sub(r"_CREATE_TYPES\s*=\s*\(.*?\n\)", f"_CREATE_TYPES = ({create},)", source, count=1, flags=re.S)
    pair = [name for kind in selected_order for name in _TYPE_NAMES[kind] if name in {"CcPredictionCreate", "HplcPredictionCreate", "SfcSingleColumnPredictionCreate", "SfcFullColumnPredictionCreate"}]
    pair_value = f"({', '.join(pair)},)" if pair else "()"
    source = re.sub(r"_PAIR_TYPES\s*=\s*\(.*?\)", f"_PAIR_TYPES = {pair_value}", source, count=1, flags=re.S)
    if "chiral_lc" not in selected:
        source = re.sub(
            r"\n        elif isinstance\(request, ChiralLcPredictionCreate\):.*?(?=\n\nPredictionService =)",
            "",
            source,
            count=1,
            flags=re.S,
        )
    return _trim_versioned_prediction_execution(source, selected)


def _trim_versioned_prediction_execution(source: str, selected: set[str]) -> str:
    """Remove execution/read branches for absent versioned prediction DTOs."""
    excluded_names: set[str] = set()
    excluded_helpers: set[str] = set()
    if "hplc" not in selected:
        excluded_names.update({"HplcPredictionCreate", "HplcPredictionReadV10", "validate_hplc_execution_binding"})
        excluded_helpers.add("_validate_hplc_raw_identity")
    if "gc" not in selected:
        excluded_names.update({"GcPredictionCreate", "GcPredictionReadV10", "validate_gc_execution_binding"})
    if not excluded_names:
        return source
    tree = ast.parse(source)
    spans = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and any(
            isinstance(item, ast.Name) and item.id in excluded_names
            for item in ast.walk(node.test)
        ):
            spans.append((node.lineno, node.end_lineno))
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id in excluded_names:
            spans.append((node.lineno, node.end_lineno))
        elif isinstance(node, ast.FunctionDef) and node.name in excluded_helpers:
            spans.append((min([node.lineno] + [item.lineno for item in node.decorator_list]), node.end_lineno))
    removed = {line for first, last in spans for line in range(first, last + 1)}
    source = "".join(line for number, line in enumerate(source.splitlines(keepends=True), 1) if number not in removed)
    for name in excluded_names:
        source = re.sub(rf"\b{name},\s*", "", source)
    return source


def _trim_prediction_router(source: str, selected: set[str], *, batch_enabled: bool = False) -> str:
    refs = {
        "tlc": '("#/components/schemas/TlcPredictionCreate",)',
        "cc": '("#/components/schemas/CcPredictionCreate",)',
        "hplc": '("#/components/schemas/HplcPredictionCreate",)',
        "chiral_lc": '("#/components/schemas/ChiralLcPredictionCreate",)',
        "sfc": '("#/components/schemas/SfcSingleColumnPredictionCreate", "#/components/schemas/SfcFullColumnPredictionCreate",)',
        "gc": '("#/components/schemas/GcPredictionCreate",)',
    }
    entries = ",\n        ".join(f'"{kind}": {refs[kind]}' for kind in sorted(selected))
    replacement = "    profile_refs = {\n        " + entries + "\n    }"
    source = re.sub(r"    profile_refs = \{.*?\n    \}\n    profile_union", replacement + "\n    profile_union", source, count=1, flags=re.S)
    source = re.sub(
        r"def _replay_idempotency\(.*?\n    if not record\.response:",
        "def _replay_idempotency(record: IdempotencyRecord, model: type[object]) -> object:\n    if not record.response:",
        source,
        count=1,
        flags=re.S,
    )
    if "sfc" not in selected:
        source = source.replace(
            "from .adapters import ModelOption, SfcAllColumnDomain, SfcSingleColumnDomain, ToolCapability, TypeCapability\n",
            "from .adapters import ModelOption, ToolCapability, TypeCapability\n",
        )
        sfc_helpers_start = source.find("    def sfc_binding(request: Request, model_id: str, model_version: str):")
        sfc_helpers_end = source.find("    def tlc_service(request: Request):", sfc_helpers_start)
        if sfc_helpers_start >= 0 and sfc_helpers_end >= 0:
            source = source[:sfc_helpers_start] + source[sfc_helpers_end:]
        sfc_routes_start = source.find('    @router.get(\n        "/sfc/conditions/options"')
        generic_routes_start = source.find('    @router.get("/prediction/types"', sfc_routes_start)
        if sfc_routes_start >= 0 and generic_routes_start >= 0:
            source = source[:sfc_routes_start] + source[generic_routes_start:]
    if "tlc" not in selected:
        source = source.replace(
            "from chrom_platform.adapters.tlc.schema import (\n    TlcConditionComparisonCreate,\n    TlcReverseCreate,\n)\n\n",
            "",
        )
        tlc_start = source.find("    def tlc_service(request: Request):")
        sfc_start = source.find('    @router.get(\n        "/sfc/conditions/options"', tlc_start)
        if sfc_start < 0:
            sfc_start = source.find('    @router.get("/prediction/types"', tlc_start)
        if tlc_start >= 0 and sfc_start >= 0:
            source = source[:tlc_start] + source[sfc_start:]
        source = source.replace(
            "    SfcConditionsOptions, SfcCandidateScopeCreate, TlcCandidateSystem,\n    TlcConditionOptions, TlcRange, TlcReverseLimits, TlcSolventOption,\n",
            "    SfcConditionsOptions, SfcCandidateScopeCreate,\n",
        )
        source = re.sub(
            r"\n    if enabled_types is not None and \"tlc\" not in enabled_type_ids:\n        router\.routes\[:\] = \[route for route in router\.routes if not route\.path\.startswith\(\"/tlc/\"\)\]",
            "",
            source,
            count=1,
        )
    schema_match = re.search(r"from \.schemas import \(.*?\n\)", source, flags=re.S)
    if schema_match:
        block = schema_match.group(0)
        names = [part.strip() for part in block[block.find("(") + 1:block.rfind(")")].replace("\n", " ").split(",") if part.strip()]
        names = [name for name in names if not (name.startswith("Sfc") and "sfc" not in selected) and not (name.startswith("Tlc") and "tlc" not in selected)]
        source = source[:schema_match.start()] + "from .schemas import (\n" + "\n".join(f"    {name}," for name in names) + "\n)" + source[schema_match.end():]
    if not batch_enabled:
        source = _trim_optional_prediction_batch(source)
    return source


def _trim_optional_prediction_batch(source: str) -> str:
    """Remove batch-only DTOs and route branches from a non-batch profile."""

    tree = ast.parse(source)
    spans: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name in {"BatchToolCapability", "TlcBatchToolCapability", "NonTlcBatchToolCapability", "BatchAwareTypeCapability", "NonBatchToolCapability"}:
            spans.append((node.lineno, node.end_lineno or node.lineno))
        elif isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in {"TlcBatchCapabilityUnion", "BatchCapabilityUnion", "include_batch"} for target in node.targets):
            spans.append((node.lineno, node.end_lineno or node.lineno))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {"batch_selected", "batch_tool"}:
            spans.append((node.lineno, node.end_lineno or node.lineno))
        elif isinstance(node, ast.If):
            test = ast.dump(node.test, include_attributes=False)
            if "batch_selected" in test or isinstance(node.test, ast.Name) and node.test.id == "include_batch" or test == ast.dump(ast.parse('tool == "batch"', mode="eval").body, include_attributes=False):
                spans.append((node.lineno, node.end_lineno or node.lineno))
    merged: list[tuple[int, int]] = []
    for first, last in sorted(spans):
        if merged and first <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], last))
        else:
            merged.append((first, last))
    lines = source.splitlines(keepends=True)
    for first, last in reversed(merged):
        del lines[first - 1:last]
    source = "".join(lines)
    source = re.sub(
        r"from \.batch import \(.*?\n\)\nfrom \.batch_health import (?:\(.*?\n\)|[^\n]+)\n",
        "",
        source,
        count=1,
        flags=re.S,
    )
    source = re.sub(r"\n    batch_metadata_provider: Callable\[\[str\], list\[BatchTemplateDefinition\]\] \| None = None,", "", source, count=1)
    source = source.replace("SuccessEnvelope[BatchAwareTypeCapability]", "SuccessEnvelope[TypeCapability]")
    source = source.replace("SuccessEnvelope[BatchToolCapability | NonBatchToolCapability]", "SuccessEnvelope[ToolCapability]")
    source = source.replace("SuccessEnvelope[TlcBatchCapabilityUnion]", "SuccessEnvelope[ToolCapability]")
    source = source.replace("SuccessEnvelope[BatchCapabilityUnion]", "SuccessEnvelope[ToolCapability]")
    source = source.replace(' + (["batch"] if batch_selected(request, item.id) else [])', '')
    return source


def _trim_prediction_assembly(source: str, selected: set[str]) -> str:
    """Remove adapter imports and registry branches absent from a profile.

    ``assembly.prediction`` is shared by the app and worker entrypoints, but
    its development copy imports every optional adapter.  A release package
    contains only the adapters named by its manifest, so the assembly must
    have the same closed import graph.
    """

    optional_types = {"cc", "hplc", "gc"}
    optional_selected = optional_types & selected
    if not optional_selected:
        source = re.sub(
            r"\ndef _optional_asset_root\(.*?(?=\ndef _register_unverified_sfc)",
            "\n",
            source,
            count=1,
            flags=re.S,
        )
        source = re.sub(r"^\s*_register_optional_adapters\(registry, session_factory=session_factory\)\n", "", source, flags=re.M)
    else:
        optional_blocks = {
            "cc": r"    cc_root = _optional_asset_root\(.*?(?=    hplc_root =)",
            "hplc": r"    hplc_root = _optional_asset_root\(.*?(?=    gc_root =)",
            "gc": r"    gc_root = _optional_asset_root\(.*?(?=\ndef _register_unverified_sfc)",
        }
        for chromatography_type, pattern in optional_blocks.items():
            if chromatography_type not in optional_selected:
                source = re.sub(pattern, "", source, count=1, flags=re.S)

    if "sfc" not in selected:
        source = re.sub(
            r'\n            if capability.id == "sfc":.*?(?=\n            bindings.append\(binding\))',
            "", source, count=1, flags=re.S,
        )
        source = re.sub(r'    entry_fields = \(.*?\n    \)\n', "", source, count=1, flags=re.S)
        source = source.replace("    from dataclasses import asdict\n", "")
        lines = source.splitlines(keepends=True)
        excluded_constants = [node for node in ast.parse(source).body if isinstance(node, ast.Assign)
                              and any(isinstance(target, ast.Name) and target.id.startswith("SFC_") for target in node.targets)]
        for node in reversed(excluded_constants):
            del lines[node.lineno - 1:node.end_lineno]
        source = "".join(lines)
        source = re.sub(
            r"    def validate_enantiomer_pair\(.*?(?=\n    def validate_chiral_proposal)",
            "    def validate_enantiomer_pair(self, ctx: OperationContext, project_id: str, structures: list[StructureSnapshot], *, chromatography_type: str) -> None:\n        return None\n\n",
            source,
            count=1,
            flags=re.S,
        )
        source = re.sub(
            r"from chrom_platform\.adapters\.sfc\.adapter import (?:\([\s\S]*?\n\)|[^\n]*)\n",
            "", source, count=1,
        )
        source = re.sub(
            r"from chrom_platform\.adapters\.sfc\.runtime import \(.*?\n\)\n",
            "",
            source,
            count=1,
            flags=re.S,
        )
        source = re.sub(
            r"\ndef _sfc_scope_storage_path\(.*?(?=\ndef _sha256_file)",
            "\n",
            source,
            count=1,
            flags=re.S,
        )
        source = re.sub(
            r"\ndef _verification_result\(.*?(?=\ndef _register_verified_tlc|\ndef build_prediction_registry)",
            "\n",
            source,
            count=1,
            flags=re.S,
        )
        source = re.sub(
            r"\ndef _require_enantiomer_pair\(.*?(?=\ndef _register_verified_tlc|\ndef build_prediction_registry)",
            "\n",
            source,
            count=1,
            flags=re.S,
        )
        source = re.sub(
            r"\ndef _register_unverified_sfc\(.*?(?=\ndef build_prediction_registry)",
            "\n",
            source,
            count=1,
            flags=re.S,
        )
        source = source.replace("Prediction assembly for real, build-owned SFC assets.", "Prediction assembly for selected build-owned assets.")

    if "tlc" not in selected:
        source = re.sub(r"from chrom_platform\.adapters\.tlc\.adapter import .*?\n", "", source, count=1)
        source = re.sub(
            r"from chrom_platform\.adapters\.tlc\.manifest import \(.*?\n\)\n",
            "",
            source,
            count=1,
            flags=re.S,
        )
        source = re.sub(r"from chrom_platform\.adapters\.tlc\.runtime import .*?\n", "", source, count=1)
        source = re.sub(r"from chrom_platform\.adapters\.tlc\.service import .*?\n", "", source, count=1)
        source = re.sub(
            r"from chrom_platform\.modules\.prediction\.schemas import \(\n\s*TlcConditionComparisonCreate,\n\s*TlcConditionComparisonResult,\n\s*TlcPredictionCreate,\n\s*TlcPredictionResult,\n\s*TlcReverseCreate,\n\s*TlcReverseResult,\n\)\n",
            "",
            source,
            count=1,
        )
        source = re.sub(
            r"\ndef _register_verified_tlc\(.*?(?=\ndef _optional_asset_root|\ndef _register_optional_adapters|\ndef _register_unverified_sfc|\ndef build_prediction_registry)",
            "\n",
            source,
            count=1,
            flags=re.S,
        )
        source = re.sub(r"^\s*_register_verified_tlc\(registry, session_factory=session_factory\)\n", "", source, flags=re.M)
        source = source.replace("TLC", "selected adapter")

    if "sfc" not in selected:
        start = source.find("def build_prediction_registry(")
        end = source.find("\ndef build_prediction_worker_registry(", start)
        if start >= 0 and end >= 0:
            registration = "    registry = CapabilityRegistry()\n"
            if optional_selected:
                registration += "    _register_optional_adapters(registry, session_factory=session_factory)\n"
            if "tlc" in selected:
                registration += "    _register_verified_tlc(registry, session_factory=session_factory)\n"
            registration += "    return registry\n"
            source = source[:start] + (
                "def build_prediction_registry(*, session_factory: Callable[[], Session] | None = None) -> CapabilityRegistry:\n"
                + registration
                + source[end:]
            )
    return source


def _trim_prediction_worker_registry(source: str, selected: set[str]) -> str:
    """Keep only selected scientific load observers and metadata branches."""
    if "cc" not in selected:
        source = re.sub(r"^    cc_source = .*\n", "", source, count=1, flags=re.M)
        source = re.sub(
            r"        if filename == cc_source and event == \"call\".*?(?=        if filename == gc_source)",
            "", source, count=1, flags=re.S,
        )
    if "gc" not in selected:
        source = re.sub(r"^    gc_source = .*\n", "", source, count=1, flags=re.M)
        source = re.sub(
            r"        if filename == gc_source and event == \"call\".*?(?=        if filename == sfc_source)",
            "", source, count=1, flags=re.S,
        )
    if "sfc" not in selected:
        source = re.sub(r"^    sfc_source = .*\n", "", source, count=1, flags=re.M)
        source = re.sub(
            r"        if filename == sfc_source and event == \"return\".*?(?=        if filename == prediction_source)",
            "", source, count=1, flags=re.S,
        )
        source = re.sub(
            r"        if filename == prediction_source and event == \"return\".*?(?=        if previous_profile is not None:)",
            "", source, count=1, flags=re.S,
        )
        source = re.sub(r"^    prediction_source = .*\n", "", source, count=1, flags=re.M)
        source = re.sub(r"^    proofs: dict\[.*\n", "", source, count=1, flags=re.M)
        source = re.sub(r"\n    if proofs:.*?(?=\n    return registry)", "", source, count=1, flags=re.S)
        source = source.replace("    from dataclasses import asdict\n", "")
        source = re.sub(r"    entry_fields = \(.*?\n    \)\n", "", source, count=1, flags=re.S)
        source = re.sub(
            r"            if capability.id == \"sfc\":.*?(?=\n            bindings.append\(binding\))",
            "", source, count=1, flags=re.S,
        )
    if not (set(selected) & {"cc", "gc", "sfc"}):
        source = source.replace("import os\n", "").replace("import sys\n", "")
        source = source.replace("    from chrom_platform.core.model_load_observation import record_model_load\n", "")
        source = re.sub(
            r"def build_prediction_worker_registry\(.*?(?=\ndef _sha256_file)",
            "def build_prediction_worker_registry(*, session_factory: Callable[[], Session] | None = None) -> CapabilityRegistry:\n"
            "    from chrom_platform.assembly import prediction as original\n"
            "    return original.build_prediction_worker_registry(session_factory=session_factory)\n",
            source, count=1, flags=re.S,
        )
    return source


def _trim_prediction_api_registry(source: str, selected: set[str]) -> str:
    """Physically remove metadata registry branches for unselected types."""

    class ProfilePruner(ast.NodeTransformer):
        def __init__(self) -> None:
            self.function_stack: list[str] = []

        def _type_literal(self, node: ast.AST) -> tuple[str, ast.cmpop, set[str]] | None:
            if not isinstance(node, ast.Compare) or len(node.ops) != 1 or len(node.comparators) != 1:
                return None
            left = node.left
            type_name = left.id if isinstance(left, ast.Name) else None
            if type_name != "type_id":
                return None
            comparator = node.comparators[0]
            if isinstance(node.ops[0], (ast.In, ast.NotIn)) and isinstance(comparator, (ast.Set, ast.Tuple, ast.List)):
                values = {item.value for item in comparator.elts if isinstance(item, ast.Constant) and isinstance(item.value, str)}
                if len(values) == len(comparator.elts):
                    return ("set", node.ops[0], values)
                return None
            if isinstance(node.ops[0], (ast.Eq, ast.NotEq)) and isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                return (comparator.value, node.ops[0], {comparator.value})
            return None

        def _is_sfc_branch(self, node: ast.AST) -> bool:
            if isinstance(node, ast.BoolOp):
                return isinstance(node.op, ast.And) and any(self._is_sfc_branch(value) for value in node.values)
            return (
                isinstance(node, ast.Compare)
                and len(node.ops) == 1
                and isinstance(node.ops[0], ast.Eq)
                and isinstance(node.comparators[0], ast.Constant)
                and node.comparators[0].value == "sfc"
                and (
                    isinstance(node.left, ast.Name) and node.left.id in {"adapter_id", "type_id"}
                    or isinstance(node.left, ast.Attribute)
                    and isinstance(node.left.value, ast.Name)
                    and node.left.value.id == "capability"
                    and node.left.attr == "id"
                )
            )

        def _trim_dto_map(self, value: ast.expr) -> None:
            if not isinstance(value, ast.Dict):
                return
            keep = []
            for key, item in zip(value.keys, value.values):
                if isinstance(key, ast.Constant) and key.value in selected:
                    keep.append((key, item))
            value.keys = [key for key, _ in keep]
            value.values = [item for _, item in keep]

        def visit_Assign(self, node: ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == "_DTO_CLASS_NAMES" for target in node.targets):
                self._trim_dto_map(node.value)
            if "sfc" not in selected and self.function_stack and self.function_stack[-1] == "_parse_binding":
                if any(isinstance(target, ast.Name) and target.id in {"catalog", "proof", "entry", "domain", "parsed_catalog"} for target in node.targets):
                    return None
            return self.generic_visit(node)

        def visit_AnnAssign(self, node: ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "_DTO_CLASS_NAMES":
                self._trim_dto_map(node.value)
            return self.generic_visit(node)

        def visit_Call(self, node: ast.Call):
            if "sfc" not in selected and isinstance(node.func, ast.Name) and node.func.id == "WorkerAttestedBinding":
                removed = {"allowed_tool", "sfc_domain_data", "registry_entry", "catalog_data", "catalog_source_proof", "_catalog"}
                node.keywords = [keyword for keyword in node.keywords if keyword.arg not in removed]
            return self.generic_visit(node)

        def visit_If(self, node: ast.If):
            info = self._type_literal(node.test)
            if info is not None:
                kind, operator, kinds = info
                if kind == "set":
                    retained = kinds & selected
                    if isinstance(operator, ast.In):
                        if not retained:
                            return self._visit_body(node.orelse)
                        node.test.comparators[0].elts = [
                            item for item in node.test.comparators[0].elts
                            if isinstance(item, ast.Constant) and item.value in retained
                        ]
                    elif isinstance(operator, ast.NotIn):
                        node.test.comparators[0].elts = [
                            item for item in node.test.comparators[0].elts
                            if isinstance(item, ast.Constant) and item.value in retained
                        ]
                elif isinstance(operator, ast.Eq) and kind not in selected:
                    return self._visit_body(node.orelse)
                elif isinstance(operator, ast.NotEq) and kind not in selected:
                    return self._visit_body(node.body)

            if "sfc" not in selected:
                if self._is_sfc_branch(node.test):
                    return self._visit_body(node.orelse)
            return self.generic_visit(node)

        def _visit_body(self, statements: list[ast.stmt]):
            result: list[ast.stmt] = []
            for statement in statements:
                visited = self.visit(statement)
                if visited is None:
                    continue
                result.extend(visited if isinstance(visited, list) else [visited])
            return result

        def visit_ClassDef(self, node: ast.ClassDef):
            if node.name == "WorkerAttestedBinding" and "sfc" not in selected:
                sfc_fields = {
                    "allowed_tool", "sfc_domain_data", "registry_entry", "catalog_data",
                    "catalog_source_proof", "_catalog",
                }
                node.body = [
                    item for item in node.body
                    if not (
                        isinstance(item, ast.AnnAssign)
                        and isinstance(item.target, ast.Name)
                        and item.target.id in sfc_fields
                    ) and not (
                        isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and item.name in {"candidate_catalog", "sfc_domain"}
                    )
                ]
            if node.name == "_ApiAdapterFacade":
                removed = set()
                if "sfc" not in selected:
                    removed |= {"candidate_catalog", "catalog_for_scope"}
                if "cc" not in selected:
                    removed.add("condition_catalog")
                node.body = [
                    item for item in node.body
                    if not (isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name in removed)
                ]
            if node.name == "ApiPredictionRegistry" and "sfc" not in selected:
                node.body = [
                    item for item in node.body
                    if not (isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == "_load_sfc_catalog")
                ]
            return self.generic_visit(node)

        def visit_FunctionDef(self, node: ast.FunctionDef):
            if node.name == "_sha256_file" and "sfc" not in selected:
                return None
            self.function_stack.append(node.name)
            result = self.generic_visit(node)
            self.function_stack.pop()
            return result

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
            self.function_stack.append(node.name)
            result = self.generic_visit(node)
            self.function_stack.pop()
            return result

        def visit_Import(self, node: ast.Import):
            if "sfc" not in selected:
                node.names = [alias for alias in node.names if alias.name != "os"]
            return node if node.names else None

        def visit_ImportFrom(self, node: ast.ImportFrom):
            if "sfc" not in selected:
                if node.module == "pathlib":
                    node.names = [alias for alias in node.names if alias.name != "Path"]
                elif node.module == "types":
                    node.names = [alias for alias in node.names if alias.name != "SimpleNamespace"]
                elif node.module == "dataclasses":
                    node.names = [alias for alias in node.names if alias.name != "field"]
            return node if node.names else None

    tree = ast.parse(source)
    pruned = ProfilePruner().visit(tree)
    ast.fix_missing_locations(pruned)
    return ast.unparse(pruned) + "\n"


def _trim_app_source(
    source: str, selected: set[str], *, batch_enabled: bool, datasets_enabled: bool = False,
) -> str:
    profile_refs = {
        "tlc": '("#/components/schemas/TlcPredictionCreate",)',
        "cc": '("#/components/schemas/CcPredictionCreate",)',
        "hplc": '("#/components/schemas/HplcPredictionCreate",)',
        "chiral_lc": '("#/components/schemas/ChiralLcPredictionCreate",)',
        "sfc": '("#/components/schemas/SfcSingleColumnPredictionCreate", "#/components/schemas/SfcFullColumnPredictionCreate",)',
        "gc": '("#/components/schemas/GcPredictionCreate",)',
    }
    profile_block = "            profile_refs = {\n" + "\n".join(
        f'                "{kind}": {profile_refs[kind]},' for kind in sorted(selected)
    ) + "\n            }"
    source = re.sub(r"            profile_refs = \{.*?\n            \}", profile_block, source, count=1, flags=re.S)
    input_refs = {
        "tlc": '("TlcInput",)', "cc": '("CcInput",)', "hplc": '("HplcInput", "HplcInputV10")',
        "chiral_lc": '("ChiralLcInput",)', "sfc": '("SfcFullColumnInput", "SfcSingleColumnInput", "_SfcConditionInput",)',
        "gc": '("GcInput",)',
    }
    input_block = "        input_refs = {\n" + "\n".join(
        f'            "{kind}": {input_refs[kind]},' for kind in sorted(selected)
    ) + "\n        }"
    source = re.sub(r"        input_refs = \{.*?\n        \}", input_block, source, count=1, flags=re.S)
    result_refs = {
        "tlc": '(("tlc", "TlcPredictionResult"), ("tlc_condition_comparison", "TlcConditionComparisonResult"), ("tlc_reverse_solvent", "TlcReverseResult"))',
        "cc": '(("cc", "CcPredictionResult"),)', "hplc": '(("hplc", "HplcPredictionResult"),)',
        "chiral_lc": '(("chiral_lc", "ChiralLcPredictionResult"),)', "sfc": '(("sfc", "SfcPredictionResult"),)', "gc": '(("gc", "GcPredictionResult"),)',
    }
    result_block = "        result_refs = {\n" + "\n".join(
        f'            "{kind}": {result_refs[kind]},' for kind in sorted(selected)
    ) + "\n        }"
    source = re.sub(r"        result_refs = \{.*?\n        \}", result_block, source, count=1, flags=re.S)
    source = source.replace(
        "        # Prediction responses are shared by all builds, while their DTO\n"
        "        # unions must describe only the chromatography types in this\n"
        "        # manifest.  Keep the future chiral_lc models in source code; trim\n"
        "        # their response branches at the profile boundary.\n",
        "        # Prediction response unions are trimmed to this profile.\n",
    )
    source = re.sub(
        r"\n\s*# unions must describe only the chromatography types in this\n\s*# manifest\. Keep the future chiral_lc models in source code; trim\n\s*# their response branches at the profile boundary\.\n",
        "\n        # Prediction response unions are trimmed to this profile.\n",
        source,
        count=1,
    )
    source = re.sub(
        r"\n\s*# manifest\. Keep the future chiral_lc models in source code; trim\n\s*# their response branches at the profile boundary\.\n",
        "\n        # Prediction response unions are trimmed to this profile.\n",
        source,
        count=1,
    )
    legacy_start = source.find("        # sfc/cc are legacy M2 profile contracts.")
    legacy_end = source.find("        components = schema.setdefault", legacy_start)
    if legacy_start >= 0 and legacy_end >= 0:
        if selected == {"sfc"}:
            legacy = source[legacy_start:legacy_end].replace(
                "        # sfc/cc are legacy M2 profile contracts.  Their runtime records\n"
                "        # router accepts the newer cursor/project inference, but these\n"
                "        # profile documents must retain the frozen parameter surface.\n",
                "        # This profile retains its frozen record parameter surface.\n",
            ).replace(
                'if built_types in (frozenset({"sfc"}), frozenset({"cc"})):',
                'if built_types == frozenset({"sfc"}):',
            )
            source = source[:legacy_start] + legacy + source[legacy_end:]
        elif selected == {"cc"}:
            legacy = source[legacy_start:legacy_end].replace(
                "        # sfc/cc are legacy M2 profile contracts.  Their runtime records\n"
                "        # router accepts the newer cursor/project inference, but these\n"
                "        # profile documents must retain the frozen parameter surface.\n",
                "        # This profile retains its frozen record parameter surface.\n",
            ).replace(
                'if built_types in (frozenset({"sfc"}), frozenset({"cc"})):',
                'if built_types == frozenset({"cc"}):',
            )
            source = source[:legacy_start] + legacy + source[legacy_end:]
        else:
            source = source[:legacy_start] + source[legacy_end:]
    if not batch_enabled:
        source = re.sub(r"\n\s*batch_enabled=any\(\"batch\" in tools for tools in manifest\.prediction_tools\.values\(\)\),", "", source, count=1)
        source = re.sub(r"\n\s*enabled_batch_types=frozenset\(kind for kind, tools in manifest\.prediction_tools\.items\(\) if \"batch\" in tools\),", "", source, count=1)
    if not datasets_enabled:
        source = re.sub(r"\n\s*data_csv_window=data_csv_window,", "", source, count=1)
    return source


def _trim_manifest_source(source: str, manifest: BuildManifest) -> str:
    """Make the packaged validator know only this profile's vocabulary."""

    tail = source[source.index("def _default_manifest_path"):]
    set_literal = lambda values: f"set({sorted(values)!r})"
    modules = set_literal(manifest.modules)
    types = set_literal(manifest.chromatography_types)
    tools = "{" + ", ".join(
        f"{kind!r}: {set_literal(values)}"
        for kind, values in sorted(manifest.prediction_tools.items())
    ) + "}"
    utilities = set_literal(manifest.utilities)
    exports = set_literal(manifest.export_formats)
    return f'''from __future__ import annotations

import json
from pathlib import Path

from chrom_platform.core.dto import BuildManifest

_MODULES = {modules}
_TYPES = {types}
_TOOLS = {tools}
_UTILITIES = {utilities}
_EXPORTS = {exports}


def _unique(values: list[str], allowed: set[str], label: str) -> None:
    if len(set(values)) != len(values) or set(values) - allowed:
        raise ValueError(f"{{label}}: duplicate or unsupported value")


def validate_manifest(raw: object) -> BuildManifest:
    manifest = BuildManifest.model_validate(raw)
    _unique(manifest.modules, _MODULES, "modules")
    _unique(manifest.chromatography_types, _TYPES, "chromatography_types")
    _unique(manifest.utilities, _UTILITIES, "utilities")
    _unique(manifest.export_formats, _EXPORTS, "export_formats")
    if set(manifest.prediction_tools) != set(manifest.chromatography_types):
        raise ValueError("prediction_tools must exactly match selected adapters")
    for kind, selected_tools in manifest.prediction_tools.items():
        if kind not in _TOOLS or set(selected_tools) - _TOOLS[kind]:
            raise ValueError(f"prediction_tools.{{kind}}: unsupported tool")
        _unique(selected_tools, _TOOLS[kind], f"prediction_tools.{{kind}}")
        if not selected_tools:
            raise ValueError(f"prediction_tools.{{kind}}: at least one tool required")
    if "prediction" in manifest.modules and not manifest.chromatography_types:
        raise ValueError("prediction requires at least one adapter")
    if manifest.chromatography_types and "prediction" not in manifest.modules:
        raise ValueError("adapters require prediction module")
    if "utilities" not in manifest.modules and manifest.utilities:
        raise ValueError("optional utilities require utilities module")
    if "assistant" in manifest.modules:
        features = manifest.assistant_features or []
        _unique(features, {{"knowledge", "business", "trace"}}, "assistant_features")
        if not set(features) & {{"knowledge", "business"}}:
            raise ValueError("assistant requires an executable mode")
        if "knowledge" in features and "knowledge" not in manifest.modules:
            raise ValueError("knowledge mode requires knowledge module")
    elif manifest.assistant_features is not None:
        raise ValueError("assistant_features must be absent without assistant")
    return manifest


''' + tail


def _trim_artifact_csv_window(source: str) -> str:
    """Remove the reviewed CSV acceptance ledger from builds without batch."""
    class TrimWindow(ast.NodeTransformer):
        def visit_FunctionDef(self, node):
            if node.name in {"_scan_window_config", "_window_evidence_is_clean", "_reserve_csv_window"}:
                return None
            return self.generic_visit(node)

        def visit_If(self, node):
            if "scan_window" in ast.unparse(node.test):
                return None
            return self.generic_visit(node)

        def visit_Assign(self, node):
            if any("scan_window" in ast.unparse(target) for target in node.targets):
                return None
            return self.generic_visit(node)

        def visit_AnnAssign(self, node):
            if "scan_window" in ast.unparse(node.target):
                return None
            return self.generic_visit(node)

        def visit_arguments(self, node):
            kept = [(arg, default) for arg, default in zip(node.kwonlyargs, node.kw_defaults)
                    if arg.arg not in {"scan_window", "scan_window_id"}]
            node.kwonlyargs = [arg for arg, _ in kept]
            node.kw_defaults = [default for _, default in kept]
            return self.generic_visit(node)

        def visit_Call(self, node):
            node.keywords = [kw for kw in node.keywords if kw.arg not in {"scan_window", "scan_window_id"}]
            return self.generic_visit(node)

    tree = TrimWindow().visit(ast.parse(source))
    result = ast.unparse(ast.fix_missing_locations(tree)) + "\n"
    if "scan_window" in result or "artifact.csv_window" in result:
        raise ValueError("no-batch artifact worker retained CSV window code")
    return result


def _trim_worker_cli(source: str, selected: set[str], *, batch_enabled: bool) -> str:
    if batch_enabled:
        return source
    source = re.sub(
        r'(?ms)^        scan_window = None\n        if scanner is None and getattr\(plan, "batch_enabled", False\):.*?(?=^        if scanner is None:)',
        "", source, count=1,
    )
    source = source.replace("            scan_window=scan_window,\n", "")
    source = re.sub(r"\n    batch_enabled: bool = False", "", source, count=1)
    source = re.sub(r"\n    enabled_batch_types: frozenset\[str\] = frozenset\(\)", "", source, count=1)
    source = re.sub(r"\n        enabled_batch_types = frozenset\(kind for kind, tools in manifest\.prediction_tools\.items\(\) if \"batch\" in tools\)", "", source, count=1)
    source = re.sub(r"\n        batch_enabled = bool\(enabled_batch_types\)", "", source, count=1)
    source = re.sub(r"\n        batch_enabled = any\(\"batch\" in tools for tools in manifest\.prediction_tools\.values\(\)\)", "", source, count=1)
    source = re.sub(r"\n        if batch_enabled and \"prediction\" in selected:\n            allowed_kinds = allowed_kinds \| frozenset\(\{\"batch_prediction\"\}\)", "", source, count=1)
    source = re.sub(r"\n            batch_enabled,", "", source, count=1)
    source = re.sub(r"\n            enabled_batch_types,", "", source, count=1)
    source = re.sub(r'(?m)^\s*"batch_prediction": [^\n]*\n', "", source)
    source = re.sub(
        r"(?ms)^def _validate_batch_artifact_store\(.*?(?=^def build_production_handlers\()",
        "",
        source,
    )
    source = re.sub(
        r'(?m)^    store_requested = bool\(\{"records"\} & set\(plan\.modules\) or getattr\(plan, "batch_enabled", False\)\)$',
        '    store_requested = bool({"records"} & set(plan.modules))',
        source,
        count=1,
    )
    source = re.sub(
        r'(?ms)^    if getattr\(plan, "batch_enabled", False\):\n        _validate_batch_artifact_store\(deps\.artifact_store\)\n.*?(?=^    if async_session_factory is not None:)',
        "",
        source,
        count=1,
    )
    source = re.sub(
        r'(?ms)^            if getattr\(plan, "batch_enabled", False\):.*?(?=^        if "training" in plan\.worker_modules:)',
        "",
        source,
        count=1,
        flags=re.S,
    )
    # The optional batch worker also extends the transport payload with a
    # fencing token.  Keep the no-batch package's stable three-field worker
    # boundary and remove the batch-only routing predicate from its generated
    # CLI; the batch source remains available in batch-enabled profiles.
    source = source.replace(", allow_cancel_fencing: bool = False", "")
    source = re.sub(
        r'        if event_type == "job.cancel":\n'
        r'            if fields != base and not \(allow_cancel_fencing and fields == base \| \{"fencing_token"\}\):\n'
        r'                raise WorkerConfigurationError\("INVALID_WORKER_PAYLOAD"\)\n'
        r'        elif fields != base:',
        '        if fields != base:', source, count=1,
    )
    source = re.sub(
        r'\n        if event_type == "job.cancel" and "fencing_token" in payload:\n'
        r'            fencing_token = payload\["fencing_token"\]\n'
        r'            if isinstance\(fencing_token, bool\) or not isinstance\(fencing_token, int\) or fencing_token < 0:\n'
        r'                raise WorkerConfigurationError\("INVALID_WORKER_PAYLOAD"\)',
        '', source, count=1,
    )
    source = re.sub(
        r'            message = StableWorkerMessage\.from_payload\(\n'
        r'                payload, event_type=event_type,\n'
        r'                allow_cancel_fencing=\(str\(kind\) == "batch_prediction"\),\n'
        r'            \)',
        '            message = StableWorkerMessage.from_payload(payload, event_type=event_type)', source, count=1,
    )
    source = re.sub(
        r'            StableWorkerMessage\.from_payload\(\n'
        r'                claim\.payload, event_type=claim\.event_type,\n'
        r'                allow_cancel_fencing=\(claim\.event_type == "job\.cancel" and \(claim\.resource_ref or \{\}\)\.get\("type"\) in \{"batch_prediction", "batch_validation"\}\),\n'
        r'            \)',
        '            StableWorkerMessage.from_payload(claim.payload, event_type=claim.event_type)', source, count=1,
    )
    source = re.sub(
        r'        message = StableWorkerMessage\.from_payload\(\n'
        r'            claim\.payload, event_type=claim\.event_type,\n'
        r'            allow_cancel_fencing=\(claim\.event_type == "job\.cancel" and \(claim\.resource_ref or \{\}\)\.get\("type"\) in \{"batch_prediction", "batch_validation"\}\),\n'
        r'        \)',
        '        message = StableWorkerMessage.from_payload(claim.payload, event_type=claim.event_type)', source, count=1,
    )
    source = re.sub(r"(?m)^\s*allow_cancel_fencing=.*,$\n", "", source)
    if "sfc" not in selected:
        source = source.replace(
            "    # The local SFC acceptance worker is supported on Windows as well as the\n"
            "    # deployment Linux profile.  Dependency and connection validation below\n",
            "    # The local acceptance worker is supported on Windows as well as the\n"
            "    # deployment Linux profile.  Dependency and connection validation below\n",
        )
    forbidden = (
        '"batch_prediction"',
        '"batch_validation"',
        '"batch_export"',
        "batch_enabled",
        "_validate_batch_artifact_store",
        "allow_cancel_fencing",
        "BatchPredictionProductionWorker",
    )
    remaining = [marker for marker in forbidden if marker in source]
    if remaining:
        raise ValueError(f"no-batch worker trim left batch-only symbols: {remaining}")
    return source


def _trim_m2_batch_wiring(source: str, *, batch_enabled: bool) -> str:
    if batch_enabled:
        return source
    source = re.sub(r"\n    batch_prediction_service:.*", "", source)
    source = re.sub(r"\n    batch_enabled: bool = False", "", source)
    source = re.sub(r"\n    enabled_batch_types: frozenset\[str\] = frozenset\(\)", "", source)
    source = re.sub(r",\n    enabled_batch_types: set\[str\] \| frozenset\[str\] = frozenset\(\)", "", source)
    source = re.sub(r", enabled_batch_types: set\[str\] \| frozenset\[str\] = frozenset\(\)", "", source)
    source = re.sub(r", batch_prediction_service: (?:\"BatchPredictionService \| None\"|object \| None) = None", "", source)
    source = re.sub(r",\n    batch_enabled: bool = False", "", source)
    source = re.sub(r", batch_enabled: bool = False", "", source)
    source = re.sub(r"\n    elif batch_enabled:\n.*?(?=\n    prefixes:|\n    if \"records\" not in selected)", "", source, count=1, flags=re.S)
    source = re.sub(r"\n        batch_prediction_service=batch_prediction_service,", "", source)
    source = re.sub(r"\n        batch_enabled=assembly\.batch_enabled,", "", source)
    source = re.sub(r"\n        enabled_batch_types=assembly\.enabled_batch_types,", "", source)
    source = source.replace("batch_prediction_service=batch_prediction_service,", "")
    source = source.replace("batch_enabled=batch_enabled,", "")
    source = source.replace("batch_enabled=assembly.batch_enabled,", "")
    source = source.replace("enabled_batch_types=enabled_batch_types,", "")
    source = source.replace("enabled_batch_types=assembly.enabled_batch_types,", "")
    source = re.sub(r"\n\s*enabled_batch_types=frozenset\(enabled_batch_types\),", "", source)
    source = re.sub(r"^\s*batch_enabled=batch_enabled,\n", "", source, flags=re.M)
    source = re.sub(r"^\s*batch_enabled=assembly\.batch_enabled,\n", "", source, flags=re.M)
    source = re.sub(r", batch_enabled=batch_enabled", "", source)
    source = re.sub(r", batch_enabled=assembly\.batch_enabled", "", source)
    source = re.sub(r", enabled_batch_types=enabled_batch_types", "", source)
    source = re.sub(r", enabled_batch_types=assembly\.enabled_batch_types", "", source)
    source = re.sub(r"\nif TYPE_CHECKING:\n    from chrom_platform\.modules\.prediction\.batch_service import BatchPredictionService\n", "\n", source)
    source = source.replace(",,", ",")
    source = _remove_batch_m2_assembly_blocks(source)
    return source


def _remove_batch_m2_assembly_blocks(source: str) -> str:
    """Remove exact batch-only M2 statements without spanning records logic."""

    tree = ast.parse(source)
    spans: list[tuple[int, int]] = []
    expected_batch_resolver_test = ast.dump(
        ast.parse("batch_enabled and session_factory is not None and confirmation is not None", mode="eval").body,
        include_attributes=False,
    )
    for function in (
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in {"assemble_m2", "bind_m2"}
    ):
        for node in function.body:
            if function.name == "bind_m2":
                if isinstance(node, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id == "batch_prediction_service"
                    for target in node.targets
                ):
                    spans.append((node.lineno, node.end_lineno or node.lineno))
                elif (
                    isinstance(node, ast.If)
                    and isinstance(node.test, ast.Attribute)
                    and node.test.attr == "batch_enabled"
                    and isinstance(node.test.value, ast.Name)
                    and node.test.value.id == "assembly"
                ):
                    spans.append((node.lineno, node.end_lineno or node.lineno))
            elif (
                function.name == "assemble_m2"
                and isinstance(node, ast.If)
                and ast.dump(node.test, include_attributes=False) == expected_batch_resolver_test
            ):
                spans.append((node.lineno, node.end_lineno or node.lineno))
    create_routers = next((node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "create_m2_routers"), None)
    if create_routers is not None:
        spans.extend(
            (node.lineno, node.end_lineno or node.lineno)
            for node in create_routers.body
            if isinstance(node, ast.FunctionDef) and node.name == "_batch_metadata_provider"
        )

    if not spans:
        return source
    lines = source.splitlines(keepends=True)
    for first, last in sorted(spans, reverse=True):
        del lines[first - 1:last]
    source = "".join(lines)
    source = re.sub(r"\n        batch_metadata_provider=_batch_metadata_provider if batch_enabled else None,", "", source, count=1)
    return source


def _trim_prediction_production_worker(source: str, selected: set[str]) -> str:
    literal = "{" + ", ".join(repr(item) for item in sorted(selected)) + "}"
    source = re.sub(r"snapshot\.type_id not in \{[^\n]+\}", f"snapshot.type_id not in {literal}", source, count=1)
    return _trim_versioned_prediction_execution(source, selected)


def _trim_data_center_schemas(source: str, selected: set[str]) -> str:
    if not selected:
        return source
    order = ("tlc", "cc", "hplc", "gc", "sfc", "chiral_lc")
    literal = "Literal[" + ", ".join(repr(item) for item in order if item in selected) + "]"
    return re.sub(r"ChromatographyType = Literal\[[^\n]+\]", f"ChromatographyType = {literal}", source, count=1)


def _trim_task_service(source: str, *, batch_enabled: bool) -> str:
    if batch_enabled:
        return source
    source = source.replace('        "batch_prediction",\n', "")
    source = source.replace('        "batch.submit",\n', "")
    source = source.replace('        "batch.retry_failed",\n', "")
    return source


def _trim_task_router(source: str, *, task_sse_enabled: bool) -> str:
    """Keep the shared job router closed when a profile has no task stream."""

    if task_sse_enabled:
        return source
    source = source.replace("from fastapi.responses import StreamingResponse\n", "")
    source = re.sub(
        r"from \.sse import check_job_event_replay_window, resolve_job_event_cursor, stream_job_event_frames\n",
        "",
        source,
        count=1,
    )
    source, removed = re.subn(
        r'\n    @router\.get\(\n        "/jobs/\{job_id\}/events/stream".*?(?=\n    @router\.get\("/confirmations/\{confirmation_id\}")',
        "\n",
        source,
        count=1,
        flags=re.S,
    )
    if removed != 1:
        raise ValueError("task SSE route trim did not match exactly one route")
    return source


def _trim_tlc_batch_api(source: str, *, batch_enabled: bool) -> str:
    """Remove the legacy TLC batch convenience API from non-batch packages.

    The platform batch worker is a separate production path.  TLC's small
    service helper predates that path and is copied with the scientific
    adapter, so remove its batch-only symbols from trimmed packages while
    retaining the single-condition and reverse-solvent services.
    """

    if batch_enabled:
        return source
    tree = ast.parse(source)
    chunks: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            chunks.append(ast.get_source_segment(source, node) or "")
            continue
        if isinstance(node, ast.ClassDef) and node.name in {"TlcBatchRow", "TlcBatchRowResult"}:
            continue
        if isinstance(node, ast.ClassDef) and node.name == "TlcPredictionService":
            node.body = [
                item for item in node.body
                if not (isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == "predict_batch")
            ]
        if isinstance(node, ast.Assign):
            target_names = {
                target.id for target in node.targets if isinstance(target, ast.Name)
            }
            if target_names == {"__all__"}:
                values = node.value
                if isinstance(values, (ast.List, ast.Tuple)):
                    values.elts = [
                        item for item in values.elts
                        if not (isinstance(item, ast.Constant) and item.value in {"TlcBatchRow", "TlcBatchRowResult"})
                    ]
        chunks.append(ast.unparse(node))
    return "\n\n".join(chunks) + "\n"


def _trim_tlc_init(source: str, *, batch_enabled: bool) -> str:
    if batch_enabled:
        return source
    source = re.sub(r"\n    TlcBatchRow,\n    TlcBatchRowResult,", "", source)
    source = re.sub(r"\n    \"TlcBatchRow\",\n    \"TlcBatchRowResult\",", "", source)
    return source


def _trim_artifact_schemas(
    source: str,
    *,
    batch_enabled: bool,
    records_enabled: bool = False,
    datasets_enabled: bool = False,
    preserve_batch_contract: bool = False,
) -> str:
    if not batch_enabled and not preserve_batch_contract:
        source = source.replace('    "batch_input",\n', "")
    if not records_enabled and not datasets_enabled:
        source = source.replace('    "export_report",\n', "")
    return source


def _trim_assistant_batch(source: str, *, batch_enabled: bool) -> str:
    if batch_enabled:
        return source
    return "\n".join(
        line for line in source.splitlines()
        if not any(
            marker in line
            for marker in (
                '"create_batch_draft"', '"update_batch_draft"', '"validate_batch"',
                '"submit_batch"', '"get_batch"',
                'ToolSpec("create_batch_draft"', 'ToolSpec("update_batch_draft"',
                'ToolSpec("validate_batch"', 'ToolSpec("submit_batch"', 'ToolSpec("get_batch"',
            )
        )
    ) + "\n"


def _filter_top_level(source: str, keep: set[str], *, schema_import: str = "", ports_import: str = "") -> str:
    """Keep selected top-level declarations while retaining ordinary imports."""

    tree = ast.parse(source)
    chunks: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module in {"schemas", "ports"}:
                continue
            chunks.append(ast.get_source_segment(source, node) or "")
            continue
        name = _node_name(node)
        if name in keep:
            chunks.append(_declaration_source_with_decorators(source, node))
    imports = "\n".join(item for item in (schema_import, ports_import) if item)
    return "\n\n".join(chunks[:1] + ([imports] if imports else []) + chunks[1:]) + "\n"


def _declaration_source_with_decorators(source: str, node: ast.AST) -> str:
    """Return a top-level declaration including decorator lines before its AST span."""

    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        decorators = getattr(node, "decorator_list", ())
        first_line = min([node.lineno, *(decorator.lineno for decorator in decorators)])
        lines = source.splitlines(keepends=True)
        return "".join(lines[first_line - 1:(node.end_lineno or node.lineno)])
    return ast.get_source_segment(source, node) or ""


def _records_router_source() -> str:
    return '''"""Prediction-record routes included by records-only builds."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import APIRouter, Header, Query, Request, Response, status

from chrom_platform.core.cursor import CursorCodec, CursorError
from chrom_platform.core.dto import ActorContext, ListEnvelope, PageInfo, SuccessEnvelope
from chrom_platform.core.errors import APIError
from chrom_platform.core.idempotency import BeginResult, IdempotencyRecord, IdempotencyScope

from .errors import DataCenterError
from .schemas import ArchiveRequest, DataStrictModel, ExportAccepted, ExportCreate, ExportView, PredictionDraftView, PredictionRecord
from .service import ExportService, PredictionRecordService


def _actor(request: Request) -> ActorContext:
    return request.app.state.actor_context


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "service-request")


class _EmptyPayload(DataStrictModel):
    pass


_IDEMPOTENCY_OPENAPI = {"in": "header", "name": "Idempotency-Key", "required": True, "schema": {"type": "string", "minLength": 1, "maxLength": 128}}


def _project(actor: ActorContext, project_id: str) -> None:
    if not actor.can_access_project(project_id):
        raise APIError(404, "RESOURCE_NOT_FOUND", "资源不存在")


def _resource_project(request: Request, supplied: str | None, record_id: str, repository: object | None, *, kind: str = "prediction") -> str:
    actor = _actor(request)
    if supplied is not None:
        _project(actor, supplied)
        return supplied
    if len(actor.project_ids) == 1:
        return actor.project_ids[0]
    finder = getattr(repository, f"get_{kind}_any", None) if repository is not None else None
    value = finder(actor.project_ids, record_id) if callable(finder) else None
    if value is not None:
        return value.project_id
    raise APIError(400, "VALIDATION_FAILED", "多项目访问时必须提供 project_id")


def _record_repository(request: Request, explicit: object | None, service: object) -> object | None:
    value = explicit or getattr(request.app.state, "prediction_record_repository", None)
    return value or getattr(service, "_records", None)


def _header(request: Request, name: str, *, status_code: int, code: str, max_length: int) -> str:
    value = request.headers.get(name)
    if value is None:
        raise APIError(status_code, code, f"缺少 {name}")
    if value == "" or len(value) > max_length:
        raise APIError(422, "VALIDATION_FAILED", f"{name} 长度无效")
    return value


def _payload_hash(route: str, payload: Any) -> str:
    raw = json.dumps({"route": route, "payload": payload}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _reserve(request: Request, store: object | None, *, actor_id: str, project_id: str, route: str, key: str, payload: Any):
    store = store or getattr(request.state, "idempotency_store", None) or getattr(request.app.state, "idempotency_store", None)
    if store is None:
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "幂等记录存储未配置", retryable=True)
    digest = _payload_hash(route, payload)
    scope = IdempotencyScope(actor_id=actor_id, project_id=project_id, method="POST", route_identity=route, key=key)
    try:
        existing = store.get(scope)
        if existing is not None:
            if existing.payload_hash != digest:
                raise APIError(409, "IDEMPOTENCY_KEY_REUSED", "幂等键已用于其他请求")
            if existing.status == "IN_PROGRESS":
                raise APIError(409, "OPERATION_IN_PROGRESS", "相同幂等请求正在处理中", retryable=True)
            if existing.status == "COMPLETED":
                return store, scope, digest, existing
        reservation = store.begin(scope, digest)
    except APIError:
        raise
    except Exception as exc:
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "幂等存储暂不可用", retryable=True) from exc
    if not isinstance(reservation, BeginResult):
        raise APIError(500, "STATE_CONFLICT", "幂等存储返回无效结果")
    if not reservation.acquired:
        if reservation.record.payload_hash != digest:
            raise APIError(409, "IDEMPOTENCY_KEY_REUSED", "幂等键已用于其他请求")
        if reservation.record.status == "IN_PROGRESS":
            raise APIError(409, "OPERATION_IN_PROGRESS", "相同幂等请求正在处理中", retryable=True)
        if reservation.record.status == "FAILED":
            raise APIError(503, "OPERATION_RETRY_REQUIRED", "上一次操作失败，可稍后重试", retryable=True)
        return store, scope, digest, reservation.record
    return store, scope, digest, None


def _finish(store: object, scope: IdempotencyScope, digest: str, result: Any, status_code: int) -> None:
    try:
        store.complete(scope, digest, status_code, result.model_dump(mode="json"))
    except Exception as exc:
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "幂等记录存储暂不可用", retryable=True) from exc


def _fail(store: object, scope: IdempotencyScope, digest: str, code: str) -> None:
    try:
        store.fail(scope, digest, code)
    except Exception as exc:
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "幂等失败记录暂不可用", retryable=True) from exc


def _replay(record: IdempotencyRecord, model: Any, request: Request) -> Any:
    if record.response is None:
        raise APIError(409, "STATE_CONFLICT", "幂等记录缺少响应")
    result = model.model_validate_json(json.dumps(record.response, ensure_ascii=False))
    if hasattr(result, "request_id"):
        result.request_id = _request_id(request)
    return result


def _after_cursor(cursor: str | None, scope: str) -> str | None:
    if not cursor:
        return None
    try:
        return CursorCodec().decode(cursor, scope=scope)
    except CursorError:
        raise APIError(400, "VALIDATION_FAILED", "cursor 无效或已失效") from None


def _page(values: list[Any], *, limit: int, scope: str) -> tuple[list[Any], PageInfo]:
    window = values[:limit]
    has_more = len(values) > limit
    next_cursor = CursorCodec().encode(after_id=str(window[-1].id), scope=scope) if has_more and window else None
    return window, PageInfo(next_cursor=next_cursor, limit=limit, has_more=has_more)


def _service(request: Request, explicit: object | None) -> PredictionRecordService:
    value = explicit or getattr(request.state, "m2_assembly", None)
    value = getattr(value, "prediction_record_service", value)
    if value is None:
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "预测记录服务未配置", retryable=True)
    return value


def _export_service(request: Request, explicit: object | None) -> ExportService:
    value = explicit or getattr(request.state, "m2_assembly", None)
    value = getattr(value, "export_service", value)
    if value is None:
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", "导出服务未配置", retryable=True)
    return value


def create_router(*, prediction_record_service: PredictionRecordService | None = None, export_service: ExportService | None = None, prediction_repository: object | None = None, idempotency_store: object | None = None, **_: object) -> APIRouter:
    router = APIRouter(prefix="", tags=["data-center"])

    @router.get("/prediction-records", response_model=ListEnvelope[PredictionRecord], operation_id="list_prediction_records")
    def list_prediction_records(request: Request, project_id: str = Query(..., min_length=1, max_length=96), chromatography_type: str | None = Query(None), status_filter: str | None = Query(None, alias="status"), limit: int = Query(50, ge=1, le=200), cursor: str | None = Query(None, min_length=1, max_length=512)):
        actor = _actor(request)
        if not actor.can("prediction:read"):
            raise APIError(403, "PERMISSION_DENIED", "当前主体无权读取预测记录")
        _project(actor, project_id)
        service = _service(request, prediction_record_service)
        scope = f"prediction-records:{project_id}:{chromatography_type or ''}:{status_filter or ''}"
        try:
            values = list(service.list(project_id, chromatography_type=chromatography_type, status=status_filter, limit=limit + 1, after_id=_after_cursor(cursor, scope)))
        except DataCenterError as exc:
            raise APIError(503, "DEPENDENCY_UNAVAILABLE", str(exc), retryable=True) from exc
        values, page = _page(values, limit=limit, scope=scope)
        return ListEnvelope(data=values, page=page, warnings=[], request_id=_request_id(request))

    @router.get("/prediction-records/{record_id}", response_model=SuccessEnvelope[PredictionRecord], operation_id="get_prediction_record")
    def get_prediction_record(record_id: str, request: Request, response: Response, project_id: str | None = Query(None, min_length=1, max_length=96)):
        actor = _actor(request)
        if not actor.can("prediction:read"):
            raise APIError(403, "PERMISSION_DENIED", "当前主体无权读取预测记录")
        service = _service(request, prediction_record_service)
        project = _resource_project(request, project_id, record_id, _record_repository(request, prediction_repository, service))
        try:
            value = service.get(project, record_id)
        except DataCenterError as exc:
            raise APIError(404, "RESOURCE_NOT_FOUND", str(exc)) from exc
        response.headers["ETag"] = value.etag
        return SuccessEnvelope(data=value, warnings=[], request_id=_request_id(request))

    @router.get("/prediction-drafts/{draft_id}", response_model=SuccessEnvelope[PredictionDraftView], operation_id="get_prediction_draft")
    def get_prediction_draft(draft_id: str, request: Request, response: Response, project_id: str | None = Query(None, min_length=1, max_length=96)):
        actor = _actor(request)
        if not actor.can("prediction:read"):
            raise APIError(403, "PERMISSION_DENIED", "当前主体无权读取预测草稿")
        service = _service(request, prediction_record_service)
        project = _resource_project(request, project_id, draft_id, _record_repository(request, prediction_repository, service), kind="replay_draft")
        try:
            value = service.get_draft(project_id=project, draft_id=draft_id)
        except DataCenterError as exc:
            _raise(exc)
        response.headers["ETag"] = value.etag
        return SuccessEnvelope(data=value, warnings=[], request_id=_request_id(request))

    @router.post("/prediction-records/{record_id}/replay-draft", response_model=SuccessEnvelope[PredictionDraftView], status_code=status.HTTP_201_CREATED, operation_id="replay_prediction_record", openapi_extra={"parameters": [_IDEMPOTENCY_OPENAPI]})
    def replay_prediction_record(record_id: str, payload: _EmptyPayload, request: Request, response: Response, project_id: str | None = Query(None, min_length=1, max_length=96), idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
        key = _header(request, "Idempotency-Key", status_code=400, code="IDEMPOTENCY_KEY_REQUIRED", max_length=128)
        actor = _actor(request)
        if not actor.can("prediction:read"):
            raise APIError(403, "PERMISSION_DENIED", "当前主体无权读取预测记录")
        if not actor.can("prediction:create"):
            raise APIError(403, "PERMISSION_DENIED", "当前主体无权创建预测草稿")
        service = _service(request, prediction_record_service)
        project = _resource_project(request, project_id, record_id, _record_repository(request, prediction_repository, service))
        route = "/api/v1/prediction-records/{id}/replay-draft"
        store, scope, digest, replay = _reserve(request, idempotency_store, actor_id=actor.actor_id, project_id=project, route=route, key=key, payload={"record_id": record_id})
        if replay is not None:
            response.headers["Idempotency-Replayed"] = "true"
            return _replay(replay, SuccessEnvelope[PredictionDraftView], request)
        try:
            value = service.replay_draft(project_id=project, record_id=record_id, actor_id=actor.actor_id)
        except DataCenterError as exc:
            _fail(store, scope, digest, exc.code)
            raise APIError(exc.status_code, exc.code, exc.message, details=exc.details) from exc
        result = SuccessEnvelope(data=value, warnings=[], request_id=_request_id(request))
        _finish(store, scope, digest, result, 201)
        return result

    @router.post("/prediction-records/{record_id}/archive", response_model=SuccessEnvelope[PredictionRecord], operation_id="archive_prediction_record", openapi_extra={"parameters": [_IDEMPOTENCY_OPENAPI]})
    def archive_prediction_record(record_id: str, payload: ArchiveRequest, request: Request, response: Response, project_id: str | None = Query(None, min_length=1, max_length=96), idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
        key = _header(request, "Idempotency-Key", status_code=400, code="IDEMPOTENCY_KEY_REQUIRED", max_length=128)
        actor = _actor(request)
        if not actor.can("prediction:read"):
            raise APIError(403, "PERMISSION_DENIED", "当前主体无权读取预测记录")
        if not actor.can("prediction:archive"):
            raise APIError(403, "PERMISSION_DENIED", "当前主体无权归档预测记录")
        service = _service(request, prediction_record_service)
        project = _resource_project(request, project_id, record_id, _record_repository(request, prediction_repository, service))
        route = "/api/v1/prediction-records/{id}/archive"
        store, scope, digest, replay = _reserve(request, idempotency_store, actor_id=actor.actor_id, project_id=project, route=route, key=key, payload={"record_id": record_id, **payload.model_dump(mode="json")})
        if replay is not None:
            result = _replay(replay, SuccessEnvelope[PredictionRecord], request)
            response.headers["Idempotency-Replayed"] = "true"
            response.headers["ETag"] = result.data.etag
            return result
        try:
            value = service.archive_record(project_id=project, record_id=record_id, request=payload, actor=actor)
        except DataCenterError as exc:
            _fail(store, scope, digest, exc.code)
            raise APIError(exc.status_code, exc.code, exc.message, details=exc.details) from exc
        except Exception as exc:
            from chrom_platform.task_core.service import TaskError
            if not isinstance(exc, TaskError):
                raise
            ledger_code = exc.code if exc.code in {"DEPENDENCY_UNAVAILABLE", "VALIDATION_FAILED", "STATE_CONFLICT", "OPERATION_TIMEOUT", "CONFIRMATION_REQUIRED", "DATA_CONFLICT", "DATA_VALIDATION_FAILED", "RESOURCE_NOT_FOUND", "DATA_CAPABILITY_UNAVAILABLE"} else "CONFIRMATION_REQUIRED"
            _fail(store, scope, digest, ledger_code)
            raise APIError(exc.status_code, exc.code, exc.message, retryable=exc.retryable) from exc
        response.headers["ETag"] = value.etag
        result = SuccessEnvelope(data=value, warnings=[], request_id=_request_id(request))
        _finish(store, scope, digest, result, 200)
        return result

    @router.post("/exports", response_model=SuccessEnvelope[ExportAccepted], status_code=status.HTTP_202_ACCEPTED, operation_id="create_prediction_record_export", openapi_extra={"parameters": [{"in": "header", "name": "Idempotency-Key", "required": True, "schema": {"type": "string", "minLength": 1, "maxLength": 128}}], "responses": {"202": {"description": "异步受理", "headers": {"Location": {"required": True, "schema": {"type": "string", "format": "uri-reference"}}}}}})
    def create_export(payload: ExportCreate, request: Request, response: Response, idempotency_key: str | None = Header(None, alias="Idempotency-Key", include_in_schema=False)):
        actor = _actor(request)
        _project(actor, payload.project_id)
        if not actor.can("prediction:read"):
            raise APIError(403, "PERMISSION_DENIED", "当前主体无权导出预测记录")
        key = request.headers.get("Idempotency-Key")
        if not key or len(key) > 128:
            raise APIError(400, "IDEMPOTENCY_KEY_REQUIRED", "缺少或无效的 Idempotency-Key")
        store = idempotency_store or getattr(request.state, "idempotency_store", None) or getattr(request.app.state, "idempotency_store", None)
        if store is None:
            raise APIError(503, "DEPENDENCY_UNAVAILABLE", "幂等记录存储未配置", retryable=True)
        digest = hashlib.sha256(json.dumps({"route": "/api/v1/exports", "payload": payload.model_dump(mode="json")}, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
        scope = IdempotencyScope(actor_id=actor.actor_id, project_id=payload.project_id, method="POST", route_identity="/api/v1/exports", key=key)
        existing = store.get(scope)
        if existing is not None:
            if existing.payload_hash != digest:
                raise APIError(409, "IDEMPOTENCY_KEY_REUSED", "幂等键已用于其他请求")
            if existing.status == "COMPLETED" and existing.response:
                result = SuccessEnvelope[ExportAccepted].model_validate(existing.response)
                response.headers["Location"] = f"/api/v1/exports/{result.data.id}"
                response.headers["Idempotency-Replayed"] = "true"
                return result
            raise APIError(409, "OPERATION_IN_PROGRESS", "相同幂等请求正在处理中", retryable=True)
        reservation = store.begin(scope, digest)
        if not isinstance(reservation, BeginResult) or not reservation.acquired:
            raise APIError(409, "OPERATION_IN_PROGRESS", "相同幂等请求正在处理中", retryable=True)
        try:
            value = _export_service(request, export_service).create(payload, actor=actor, request_id=_request_id(request))
            result = SuccessEnvelope(data=value, warnings=[], request_id=_request_id(request))
            response.headers["Location"] = f"/api/v1/exports/{value.id}"
            store.complete(scope, digest, 202, result.model_dump(mode="json"))
            return result
        except DataCenterError as exc:
            store.fail(scope, digest, exc.code)
            raise APIError(exc.status_code, exc.code, exc.message, details=exc.details) from exc

    @router.get("/exports/{id}", response_model=SuccessEnvelope[ExportView], operation_id="get_prediction_record_export")
    def get_export(id: str, request: Request):
        try:
            value = _export_service(request, export_service).get(id, actor=_actor(request))
        except DataCenterError as exc:
            raise APIError(exc.status_code, exc.code, exc.message, details=exc.details) from exc
        return SuccessEnvelope(data=value, warnings=[], request_id=_request_id(request))

    return router


__all__ = ["create_router"]
'''


def _trim_records_data_center(package: Path, *, batch_enabled: bool = False) -> None:
    data = package / "modules" / "data_center"
    schema_source = (data / "schemas.py").read_text(encoding="utf-8")
    schema_keep = {"DataStrictModel", "PublicId", "ChromatographyType", "PredictionRecord", "PredictionDraftExactModelSelection", "PredictionDraftStructureSnapshot", "PredictionDraftSourceModelSnapshot", "PredictionDraftView", "ArchiveRequest", "ConfirmationConsumeRequest", "ExportCreate", "ExportAccepted", "ExportView"}
    (data / "schemas.py").write_text(_filter_top_level(schema_source, schema_keep), encoding="utf-8")
    ports_source = (data / "ports.py").read_text(encoding="utf-8")
    ports_keep = {"PredictionRecordPort", "PredictionReplaySourceData", "PredictionReplaySource", "PredictionRecordDraft", "PredictionRecordStagingPort", "PredictionArchivePort", "default_prediction_record_staging_port", "ExportPort"}
    ports_import = "from .schemas import PredictionDraftView, PredictionRecord, ConfirmationConsumeRequest, ExportCreate, ExportAccepted, ExportView"
    (data / "ports.py").write_text(_filter_top_level(ports_source, ports_keep, schema_import=ports_import), encoding="utf-8")
    persistence_source = (data / "persistence.py").read_text(encoding="utf-8")
    if not batch_enabled:
        lines = persistence_source.splitlines(keepends=True)
        methods = [
            node for node in ast.walk(ast.parse(persistence_source))
            if isinstance(node, ast.FunctionDef)
            and node.name in {"_batch_row_record_id", "write_batch_row_record"}
        ]
        for node in sorted(methods, key=lambda item: item.lineno, reverse=True):
            first = min([node.lineno, *(item.lineno for item in node.decorator_list)])
            del lines[first - 1:node.end_lineno]
        persistence_source = "".join(lines).replace("from copy import deepcopy\n", "")
    persistence_keep = {"DataCenterBase", "PredictionRecordRow", "PredictionDraftRow", "ExportRow", "_PROJECT_ID", "_ARTIFACT_PROJECT_ID", "_ARTIFACT_ID", "_JOB_ID", "_now", "_aware", "_id", "_SHA256", "_confirmation_hash", "_confirmation_binding_hash", "_public_prediction_input_snapshot", "_prediction_dto", "_prediction_draft_dto", "SQLAlchemyPredictionRecordRepository", "SQLAlchemyPredictionRecordWriter"}
    persistence_import = "from .schemas import ArchiveRequest, ConfirmationConsumeRequest, PredictionDraftView, PredictionRecord, ExportCreate, ExportView\nfrom .ports import PredictionRecordDraft, PredictionRecordStagingPort, PredictionReplaySourceData"
    (data / "persistence.py").write_text(_filter_top_level(persistence_source, persistence_keep, schema_import=persistence_import), encoding="utf-8")
    service_source = (data / "service.py").read_text(encoding="utf-8")
    service_keep = {"_now", "_canonical", "_hash", "PredictionRecordService", "ExportService"}
    service_import = "from .schemas import ArchiveRequest, ConfirmationConsumeRequest, PredictionDraftView, PredictionRecord, ExportCreate, ExportAccepted, ExportView\nfrom .ports import PredictionArchivePort, PredictionRecordPort, PredictionReplaySource, ExportPort"
    (data / "service.py").write_text(_filter_top_level(service_source, service_keep, schema_import=service_import), encoding="utf-8")
    schema_path = data / "schemas.py"
    schema_path.write_text(schema_path.read_text(encoding="utf-8").replace('Literal["data_import.commit", "resource.archive"]', 'Literal["resource.archive"]'), encoding="utf-8")
    (data / "router.py").write_text(_records_router_source(), encoding="utf-8")
    (data / "testing.py").unlink(missing_ok=True)
    (data / "__init__.py").write_text(
        "from .errors import *\nfrom .ports import *\nfrom .schemas import *\nfrom .service import *\nfrom .persistence import *\n",
        encoding="utf-8",
    )


def _trim_records_assembly(source: str) -> str:
    return '''"""M2 assembly for prediction plus prediction-records only."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from fastapi import APIRouter
from sqlalchemy import MetaData
from sqlalchemy.orm import Session

from chrom_platform.infrastructure.metadata import m2_metadata
from chrom_platform.modules.data_center.persistence import SQLAlchemyPredictionRecordRepository, SQLAlchemyPredictionRecordWriter
from chrom_platform.modules.data_center.exports import SQLAlchemyExportRepository
from chrom_platform.modules.data_center.router import create_router as create_data_center_router
from chrom_platform.modules.data_center.service import ExportService, PredictionRecordService
from chrom_platform.modules.prediction.persistence import SQLAlchemyPredictionPersistence
from chrom_platform.modules.prediction.router import create_router as create_prediction_router
from chrom_platform.modules.prediction.service import PredictionCreateService


@dataclass(frozen=True)
class M2Assembly:
    session_factory: Callable[[], Session] | None = None
    auth: object | None = None
    access: object | None = None
    structure: object | None = None
    capability_registry: object | None = None
    model_port: object | None = None
    artifact: object | None = None
    confirmation: object | None = None
    idempotency_store: object | None = None
    modules: frozenset[str] = frozenset()
    chromatography_types: frozenset[str] = frozenset()
    session: Session | None = None
    prediction_persistence: SQLAlchemyPredictionPersistence | None = None
    prediction_service: PredictionCreateService | None = None
    prediction_record_repository: SQLAlchemyPredictionRecordRepository | None = None
    prediction_record_service: PredictionRecordService | None = None
    export_service: ExportService | None = None
    batch_prediction_service: object | None = None
    batch_enabled: bool = False
    routers: tuple[APIRouter, APIRouter] = ()

    @property
    def metadata(self) -> MetaData:
        return m2_metadata()


def create_m2_routers(*, prediction_service: PredictionCreateService | None = None, batch_prediction_service: object | None = None, prediction_read_service: object | None = None, prediction_registry: object | None = None, prediction_record_service: PredictionRecordService | None = None, export_service: ExportService | None = None, prediction_repository: object | None = None, idempotency_store: object | None = None, modules: set[str] | frozenset[str] | None = None, chromatography_types: set[str] | frozenset[str] | None = None, batch_enabled: bool = False) -> tuple[APIRouter, APIRouter]:
    selected = set(modules) if modules is not None else {"prediction", "records"}
    prediction = create_prediction_router(prediction_service=prediction_service, read_service=prediction_read_service, registry=prediction_registry, enabled_types=chromatography_types)
    data_center = create_data_center_router(prediction_record_service=prediction_record_service, export_service=export_service, prediction_repository=prediction_repository, idempotency_store=idempotency_store)
    if "prediction" not in selected:
        prediction.routes[:] = []
    elif batch_enabled:
        from chrom_platform.modules.prediction.batch_router import create_router as create_batch_router
        from chrom_platform.modules.prediction.batch_service import default_batch_template_registry

        batch = create_batch_router(service=batch_prediction_service, template_registry=default_batch_template_registry())
        prediction.routes.extend(batch.routes)
    if "records" not in selected:
        data_center.routes[:] = []
    return prediction, data_center


def assemble_m2_metadata() -> MetaData:
    return m2_metadata()


def _prediction_record_archive_target(record):
    import hashlib
    import json
    from chrom_platform.core.dto import ResourceRef
    from chrom_platform.task_core.service import TargetSnapshot

    config_hash = hashlib.sha256(json.dumps({"record_id": record.id, "etag": record.etag}, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
    return TargetSnapshot(project_id=record.project_id, resource_ref=ResourceRef(type="prediction_record", id=record.id), revision=record.revision, config_hash=config_hash, etag=record.etag, policy_version="deployment-default-v1", summary=[{"label": "预测记录", "value": record.id}, {"label": "状态", "value": record.status}], can_confirm=record.status != "ARCHIVED", blockers=[] if record.status != "ARCHIVED" else [{"code": "ALREADY_ARCHIVED", "message": "预测记录已归档"}])


def assemble_m2(session_factory: Callable[[], Session] | None = None, *, auth: object | None = None, access: object | None = None, structure: object | None = None, capability_registry: object | None = None, model_port: object | None = None, artifact: object | None = None, confirmation: object | None = None, idempotency_store: object | None = None, modules: set[str] | frozenset[str] | None = None, chromatography_types: set[str] | frozenset[str] | None = None, batch_enabled: bool = False) -> M2Assembly:
    routers = create_m2_routers(prediction_registry=capability_registry, idempotency_store=idempotency_store, modules=modules, chromatography_types=chromatography_types, batch_enabled=batch_enabled)
    selected = set(modules) if modules is not None else {"prediction", "records"}
    if "records" in selected and session_factory is not None and confirmation is not None:
        from chrom_platform.task_core.confirmation_target_resolver import register_confirmation_target_resolver
        from chrom_platform.task_core.service import TaskError

        def resolve_prediction_record_confirmation(ctx, resource_ref, action):
            if action != "resource.archive" or resource_ref.type != "prediction_record":
                raise TaskError(400, "VALIDATION_FAILED", "预测记录仅支持归档确认")
            if not ctx.actor.can("prediction:read") or not ctx.actor.can("prediction:archive"):
                raise TaskError(403, "PERMISSION_DENIED", "当前主体无权确认预测记录归档")
            if not ctx.actor.project_ids:
                raise TaskError(404, "RESOURCE_NOT_FOUND", "预测记录不存在或当前主体不可见")
            with session_factory() as lookup_session:
                record = SQLAlchemyPredictionRecordRepository(lookup_session).get_prediction_any(ctx.actor.project_ids, resource_ref.id)
                if record is None:
                    raise TaskError(404, "RESOURCE_NOT_FOUND", "预测记录不存在或当前主体不可见")
                return _prediction_record_archive_target(record)

        register_confirmation_target_resolver(confirmation, "prediction_record", resolve_prediction_record_confirmation)
    return M2Assembly(session_factory=session_factory, auth=auth, access=access, structure=structure, capability_registry=capability_registry, model_port=model_port, artifact=artifact, confirmation=confirmation, idempotency_store=idempotency_store, routers=routers, modules=frozenset(modules or {"prediction", "records"}), chromatography_types=frozenset(chromatography_types or ()), batch_enabled=batch_enabled)


def bind_m2(assembly: M2Assembly, session: Session, *, idempotency_store: object | None = None) -> M2Assembly:
    record_repository = SQLAlchemyPredictionRecordRepository(session)
    prediction_persistence = SQLAlchemyPredictionPersistence(session, record_writer=SQLAlchemyPredictionRecordWriter())
    prediction_service = PredictionCreateService(access=assembly.access, structures=assembly.structure, capabilities=assembly.capability_registry, models=assembly.model_port or assembly.capability_registry, idempotency=prediction_persistence, atomic=prediction_persistence)
    batch_prediction_service = None
    if assembly.batch_enabled:
        from chrom_platform.modules.prediction.batch_service import BatchPredictionService, default_batch_template_registry

        batch_prediction_service = BatchPredictionService(session, artifact=assembly.artifact, confirmation=assembly.confirmation, capabilities=assembly.capability_registry, prediction_create_service=prediction_service, template_registry=default_batch_template_registry(), idempotency=idempotency_store if idempotency_store is not None else assembly.idempotency_store)
    from chrom_platform.task_core.service import OperationContext, TaskError

    def consume_prediction_record_archive(confirmation_id, actor, record, caller_session):
        if assembly.confirmation is None:
            raise TaskError(503, "POLICY_UNAVAILABLE", "Confirmation 服务未配置", retryable=True)
        if record.status == "ARCHIVED":
            raise TaskError(409, "CONFIRMATION_INVALID", "预测记录已归档")
        assembly.confirmation.consume_for_mutation(OperationContext(actor=actor), confirmation_id, target=_prediction_record_archive_target(record), action="resource.archive", result_ref={"record_id": record.id, "status": "ARCHIVED", "revision": record.revision + 1}, session=caller_session)

    record_service = PredictionRecordService(record_repository, archive=record_repository, replay_source=prediction_persistence, archive_confirmation=consume_prediction_record_archive)
    export_service = ExportService(SQLAlchemyExportRepository(session))
    routers = create_m2_routers(prediction_service=prediction_service, batch_prediction_service=batch_prediction_service, prediction_read_service=prediction_persistence, prediction_registry=assembly.capability_registry, prediction_record_service=record_service, export_service=export_service, prediction_repository=record_repository, idempotency_store=idempotency_store if idempotency_store is not None else assembly.idempotency_store, modules=assembly.modules, chromatography_types=assembly.chromatography_types, batch_enabled=assembly.batch_enabled)
    return M2Assembly(session_factory=assembly.session_factory, auth=assembly.auth, access=assembly.access, structure=assembly.structure, capability_registry=assembly.capability_registry, model_port=assembly.model_port, artifact=assembly.artifact, confirmation=assembly.confirmation, idempotency_store=idempotency_store if idempotency_store is not None else assembly.idempotency_store, session=session, modules=assembly.modules, chromatography_types=assembly.chromatography_types, prediction_persistence=prediction_persistence, prediction_service=prediction_service, batch_prediction_service=batch_prediction_service, batch_enabled=assembly.batch_enabled, prediction_record_repository=record_repository, prediction_record_service=record_service, export_service=export_service, routers=routers)


__all__ = ["M2Assembly", "assemble_m2", "assemble_m2_metadata", "bind_m2", "create_m2_routers"]
'''


def _trim_records_migration(source: str) -> str:
    """Keep only prediction-record and prediction DDL for records profiles."""

    tree = ast.parse(source)
    upgrade = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "upgrade")
    downgrade = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "downgrade")

    def call_name(statement: ast.stmt) -> tuple[str, list[ast.expr], list[ast.keyword]] | None:
        if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
            return None
        call = statement.value
        if not isinstance(call.func, ast.Attribute) or not isinstance(call.func.value, ast.Name) or call.func.value.id != "op":
            return None
        return call.func.attr, call.args, call.keywords

    def string_value(value: ast.expr | None) -> str | None:
        return value.value if isinstance(value, ast.Constant) and isinstance(value.value, str) else None

    def selected(stmt: ast.stmt) -> bool:
        parsed = call_name(stmt)
        if parsed is None:
            return False
        name, args, keywords = parsed
        first = string_value(args[0]) if args else None
        table = string_value(args[1]) if len(args) > 1 else None
        table_kw = next((string_value(item.value) for item in keywords if item.arg == "table_name"), None)
        if name in {"create_table", "drop_table"}:
            return first in {"prediction", "prediction_record"}
        if name == "create_index":
            return table in {"prediction", "prediction_record"}
        if name == "drop_index":
            return table_kw in {"prediction", "prediction_record"}
        if name in {"create_foreign_key", "drop_constraint"}:
            return any("prediction" in (string_value(item) or "") for item in args[:3])
        return False

    preamble = source[: source.index("def upgrade()")]
    upgrade_body = "\n".join(f"    {ast.unparse(statement)}" for statement in upgrade.body if selected(statement))
    downgrade_body = "\n".join(f"    {ast.unparse(statement)}" for statement in downgrade.body if selected(statement))
    return preamble + f"def upgrade() -> None:\n{upgrade_body}\n\ndef downgrade() -> None:\n{downgrade_body}\n"


def _copy_package_tree(source_root: Path, target_root: Path, relative_files: Iterable[str]) -> None:
    """Copy an explicit file allowlist, never the development package tree."""

    for relative in relative_files:
        source = source_root / relative
        if not source.is_file():
            raise FileNotFoundError(f"profile source file missing: {source}")
        destination = target_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _load_gc_profile_source_integrity():
    """Load the build-only verifier by path; it is never copied into runtime."""
    repository_root = Path(__file__).resolve().parents[4]
    helper_path = repository_root / "builds" / "model_package_source_integrity.py"
    spec = importlib.util.spec_from_file_location("_chrom_build_model_package_source_integrity", helper_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("MODEL_PACKAGE_BUILD_SOURCE_CHECK_UNAVAILABLE")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)

    return helper


def _load_gc_derived_receipt_helper():
    """Load the build-only derived receipt helper; never copy it to runtime."""
    repository_root = Path(__file__).resolve().parents[4]
    helper_path = repository_root / "builds" / "model_package_derived_receipt.py"
    spec = importlib.util.spec_from_file_location("_chrom_build_model_package_derived_receipt", helper_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("MODEL_PACKAGE_DERIVED_RECEIPT_HELPER_UNAVAILABLE")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper, helper_path


def _verify_gc_profile_sources(source_root: Path, target_root: Path, *, model_center_selected: bool) -> None:
    """Verify the source and selected copied closure with the build-only helper."""

    helper = _load_gc_profile_source_integrity()
    helper.verify_gc_profile_copy(
        source_root,
        target_root,
        model_center_selected=model_center_selected,
    )


def _copy_tree(source_root: Path, target_root: Path, manifest: BuildManifest) -> None:
    modules = set(manifest.modules)
    batch_enabled = any("batch" in tools for tools in manifest.prediction_tools.values())
    task_sse_enabled = bool(modules & {"prediction", "records", "datasets", "knowledge", "training", "model_center", "assistant"})
    m2_selected = modules & {"prediction", "records", "datasets"}
    worker_modules = modules & {"prediction", "knowledge", "training", "model_center", "assistant"}
    data_import_worker_selected = "datasets" in modules
    # These are the common runtime modules required by every app profile.
    relative_files: set[str] = {
        "chrom_platform/__init__.py",
        "chrom_platform/app.py",
        "chrom_platform/generate_openapi.py",
    }
    for directory in ("artifact_core", "core", "infrastructure", "structure_core", "task_core"):
        for path in (source_root / "chrom_platform" / directory).rglob("*.py"):
            if directory == "artifact_core" and path.name == "batch_csv_window.py" and not batch_enabled:
                continue
            if directory == "artifact_core" and path.name == "data_csv_window.py" and (
                manifest.build_id != "data-center-invalid-window-20261026" or modules != {"datasets"}
            ):
                continue
            if directory == "artifact_core" and path.name == "dataset_csv_elevated_runner.py" and (
                manifest.build_id != "data-center-invalid-window-20261026" or modules != {"datasets"}
            ):
                continue
            if directory == "task_core" and path.name == "sse.py" and not task_sse_enabled:
                continue
            if directory == "artifact_core" and not (worker_modules or data_import_worker_selected) and path.name in {
                "validation_worker.py", "scanner_defender.py",
            }:
                continue
            relative_files.add(str(path.relative_to(source_root)).replace("\\", "/"))
    # Keep only the static manifest loader and selected assemblies.  The
    # profile generator itself is build tooling and must not enter a runtime
    # package where it could expose discarded source paths.
    relative_files.update({
        "chrom_platform/assembly/__init__.py",
        "chrom_platform/assembly/manifest.py",
        "chrom_platform/assembly/m1.py",
    })
    # The application imports the prediction storage port and registry from
    # this assembly module whenever the prediction module is selected. Keep
    # it in the runtime closure so an assembled package does not fall back to
    # the source checkout for that import.
    if "prediction" in modules:
        relative_files.add("chrom_platform/assembly/prediction.py")
        relative_files.update({
            "chrom_platform/assembly/prediction_structure.py",
            "chrom_platform/assembly/prediction_api.py",
            "chrom_platform/assembly/prediction_metadata.py",
        })
        if "gc" in manifest.chromatography_types:
            relative_files.add("chrom_platform/assembly/gc_candidate_policy.py")
    selected_module_dirs = {"structure_core"}
    if "prediction" in modules:
        selected_module_dirs.add("prediction")
    if m2_selected:
        selected_module_dirs.add("data_center")
    selected_module_dirs.update(
        name for name in ("knowledge", "training", "model_center", "assistant") if name in modules
    )
    # Worker profiles carry the fixed entrypoint together with their selected
    # static handlers and the M3 composition that owns their DB ports.
    # Structure-only profiles stay physically worker-free.
    if worker_modules or data_import_worker_selected:
        relative_files.add("chrom_platform/worker_cli.py")
        relative_files.add("chrom_platform/assembly/handler_registry.py")
        for module in sorted(worker_modules):
            relative_files.add(f"chrom_platform/modules/{module}/worker.py")
            if module in {"prediction", "training", "assistant"}:
                relative_files.add(f"chrom_platform/modules/{module}/production_worker.py")
        if "prediction" in worker_modules:
            relative_files.add("chrom_platform/assembly/prediction_worker.py")
            relative_files.add("chrom_platform/assembly/prediction_worker_registry.py")
        if "training" in worker_modules:
            relative_files.add("chrom_platform/assembly/training_worker.py")
        if "assistant" in worker_modules:
            relative_files.add("chrom_platform/assembly/assistant_worker.py")
    for directory in selected_module_dirs:
        relative_files.update(
            str(path.relative_to(source_root)).replace("\\", "/")
            for path in (source_root / "chrom_platform" / "modules" / directory).rglob("*.py")
            if not (directory == "data_center" and path.name == "tlc_author_domain.py" and "datasets" not in modules)
        )
    if m2_selected:
        relative_files.add("chrom_platform/assembly/m2.py")
    if modules & {"knowledge", "training", "model_center"}:
        relative_files.add("chrom_platform/assembly/m3.py")
    if "assistant" in modules:
        relative_files.add("chrom_platform/assembly/m4.py")
    if "prediction" in modules:
        adapter_root = source_root / "chrom_platform" / "adapters"
        for kind in manifest.chromatography_types:
            selected_dir = adapter_root / kind
            relative_files.update(
                str(path.relative_to(source_root)).replace("\\", "/")
                for path in selected_dir.rglob("*.py")
            )
        selected_resources = {
            "gc": {"gc-reference-preprocessing-v2.json"},
            "sfc": {
                "sfc-full-reference.json",
                "sfc-rs12-full-column-reference-coupled-20261020-v2.json",
                "sfc-single-reference-2p5-warning-v3.json",
                "sfc-single-reference.json",
            },
            "tlc": {"tlc.smoke.json"},
        }
        for kind in manifest.chromatography_types:
            relative_files.update(
                f"chrom_platform/resources/{name}"
                for name in selected_resources.get(kind, set())
            )
    if batch_enabled:
        relative_files.add("chrom_platform/resources/batch-scan-manifest.json")
    # GC source bytes must pass the build-owned inventory before any profile
    # files are copied.  The build-only verifier is loaded only for GC.
    gc_source_integrity = None
    if "gc" in manifest.chromatography_types:
        gc_source_integrity = _load_gc_profile_source_integrity()
        gc_source_integrity.verify_source_tree(source_root)

    _copy_package_tree(source_root, target_root, sorted(relative_files))
    # Check exact copied bytes before the profile's intentional AST trimming.
    # The build-only verifier is loaded only for selected GC profiles.
    if gc_source_integrity is not None:
        _verify_gc_profile_sources(
            source_root,
            target_root,
            model_center_selected="model_center" in modules,
        )
    package = target_root / "chrom_platform"
    artifact_worker = package / "artifact_core" / "validation_worker.py"
    if artifact_worker.is_file() and not batch_enabled:
        artifact_worker.write_text(
            _trim_artifact_csv_window(artifact_worker.read_text(encoding="utf-8")), encoding="utf-8",
        )
    (package / "assembly" / "manifest.py").write_text(
        _trim_manifest_source(
            (package / "assembly" / "manifest.py").read_text(encoding="utf-8"),
            manifest,
        ),
        encoding="utf-8",
    )
    if worker_modules or data_import_worker_selected:
        worker_cli = package / "worker_cli.py"
        worker_cli.write_text(
            _trim_worker_cli(worker_cli.read_text(encoding="utf-8"), set(manifest.chromatography_types), batch_enabled=batch_enabled),
            encoding="utf-8",
        )
    if "assistant" in modules:
        assistant_service = package / "modules" / "assistant" / "service.py"
        assistant_tools = package / "modules" / "assistant" / "tools" / "__init__.py"
        assistant_service.write_text(
            _trim_assistant_batch(assistant_service.read_text(encoding="utf-8"), batch_enabled=batch_enabled),
            encoding="utf-8",
        )
        assistant_tools.write_text(
            _trim_assistant_batch(assistant_tools.read_text(encoding="utf-8"), batch_enabled=batch_enabled),
            encoding="utf-8",
        )
    artifact_schemas = package / "artifact_core" / "schemas.py"
    artifact_schemas.write_text(
        _trim_artifact_schemas(
            artifact_schemas.read_text(encoding="utf-8"),
            batch_enabled=batch_enabled,
            records_enabled="records" in modules,
            datasets_enabled="datasets" in modules,
            preserve_batch_contract=manifest.build_id == "universal-v1",
        ),
        encoding="utf-8",
    )
    task_service = package / "task_core" / "service.py"
    task_service.write_text(
        _trim_task_service(task_service.read_text(encoding="utf-8"), batch_enabled=batch_enabled),
        encoding="utf-8",
    )
    task_router = package / "task_core" / "router.py"
    task_router.write_text(
        _trim_task_router(task_router.read_text(encoding="utf-8"), task_sse_enabled=task_sse_enabled),
        encoding="utf-8",
    )
    if m2_selected:
        data_schemas = package / "modules" / "data_center" / "schemas.py"
        if data_schemas.is_file():
            data_types = set(manifest.chromatography_types)
            if manifest.build_id == "universal-v1":
                # The approved universal contract exposes chiral_lc in shared
                # record/data-domain DTOs even when its prediction adapter is
                # not selected. Preserve that data compatibility for both
                # batch and non-batch universal packages.
                data_types.add("chiral_lc")
            data_schemas.write_text(
                _trim_data_center_schemas(
                    data_schemas.read_text(encoding="utf-8"),
                    data_types,
                ),
                encoding="utf-8",
            )
    if "prediction" in modules:
        api_registry = package / "modules" / "prediction" / "api_registry.py"
        if api_registry.is_file():
            api_registry.write_text(
                _trim_prediction_api_registry(
                    api_registry.read_text(encoding="utf-8"),
                    set(manifest.chromatography_types),
                ),
                encoding="utf-8",
            )
        selected_types = set(manifest.chromatography_types)
        if selected_types != {"tlc", "cc", "hplc", "gc", "sfc"}:
            prediction_assembly = package / "assembly" / "prediction.py"
            prediction_assembly.write_bytes(
                _trim_prediction_assembly(
                    prediction_assembly.read_text(encoding="utf-8"), selected_types,
                ).encode("utf-8")
            )
            worker_registry = package / "assembly" / "prediction_worker_registry.py"
            if worker_registry.is_file():
                worker_registry.write_bytes(
                    _trim_prediction_worker_registry(
                        worker_registry.read_text(encoding="utf-8"), selected_types,
                    ).encode("utf-8")
                )
        if "tlc" in manifest.chromatography_types and not batch_enabled:
            tlc_service = package / "adapters" / "tlc" / "service.py"
            if tlc_service.is_file():
                tlc_service.write_text(
                    _trim_tlc_batch_api(tlc_service.read_text(encoding="utf-8"), batch_enabled=batch_enabled),
                    encoding="utf-8",
                )
            tlc_init = package / "adapters" / "tlc" / "__init__.py"
            if tlc_init.is_file():
                tlc_init.write_text(
                    _trim_tlc_init(tlc_init.read_text(encoding="utf-8"), batch_enabled=batch_enabled),
                    encoding="utf-8",
                )
    if not m2_selected:
        app = package / "app.py"
        app_source = app.read_text(encoding="utf-8")
        app_source = re.sub(
            r"    # M2 stores factories only\..*?    app\.state\.m2_assembly = m2\n",
            "    m2 = None\n    app.state.m2_assembly = None\n",
            app_source,
            count=1,
            flags=re.S,
        )
        app_source = re.sub(
            r"    if m2 is not None:\n        prediction_router, data_center_router = m2\.routers\n        if \"prediction\" in manifest\.modules:\n            app\.include_router\(prediction_router, prefix=\"/api/v1\"\)\n        if \{\"records\", \"datasets\"\} & set\(manifest\.modules\):\n            app\.include_router\(data_center_router, prefix=\"/api/v1\"\)\n\n",
            "",
            app_source,
            count=1,
        )
        # The common request middleware also binds M3/M4 when those modules
        # are selected; keep it intact for assistant-only profiles.
        app_source = re.sub(
            r"        prediction_operation = schema\.get\(\"paths\", \{\}\)\.get\(\"/api/v1/predictions\", \{\}\)\.get\(\"post\"\)\n"
            r"        if prediction_operation is not None and manifest\.chromatography_types:.*?"
            r"        # Prediction responses are shared by all builds, while their DTO",
            "",
            app_source,
            count=1,
            flags=re.S,
        )
        app.write_text(app_source, encoding="utf-8")
        metadata = package / "infrastructure/metadata.py"
        metadata_source = metadata.read_text(encoding="utf-8")
        metadata_source = metadata_source.split("def m2_metadata()", 1)[0]
        metadata_source = metadata_source.replace("from sqlalchemy import ForeignKeyConstraint, MetaData", "from sqlalchemy import MetaData")
        metadata_source = metadata_source.replace('    "m2_metadata",\n', "")
        metadata.write_text(metadata_source, encoding="utf-8")
    elif "prediction" in modules:
        if "prediction" not in modules:
            shutil.rmtree(package / "modules/prediction", ignore_errors=True)
            shutil.rmtree(package / "adapters", ignore_errors=True)
        else:
            prediction = package / "modules/prediction"
            schemas = prediction / "schemas.py"
            schemas.write_text(_prediction_schemas(schemas.read_text(encoding="utf-8"), set(manifest.chromatography_types), batch_enabled=batch_enabled), encoding="utf-8")
            (prediction / "adapters.py").write_text(
                _trim_prediction_adapters(
                    (prediction / "adapters.py").read_text(encoding="utf-8"),
                    set(manifest.chromatography_types),
                    batch_enabled=batch_enabled,
                    preserve_batch_contract=manifest.build_id == "universal-v1",
                ),
                encoding="utf-8",
            )
            (prediction / "service.py").write_text(_trim_prediction_service((prediction / "service.py").read_text(encoding="utf-8"), set(manifest.chromatography_types)), encoding="utf-8")
            (prediction / "router.py").write_text(_trim_prediction_router((prediction / "router.py").read_text(encoding="utf-8"), set(manifest.chromatography_types), batch_enabled=batch_enabled), encoding="utf-8")
            for filename in ("worker.py", "persistence.py"):
                shared_source = prediction / filename
                if shared_source.is_file():
                    shared_source.write_text(_trim_versioned_prediction_execution(shared_source.read_text(encoding="utf-8"), set(manifest.chromatography_types)), encoding="utf-8")
            production_worker = prediction / "production_worker.py"
            if production_worker.is_file():
                production_worker.write_text(
                    _trim_prediction_production_worker(
                        production_worker.read_text(encoding="utf-8"),
                        set(manifest.chromatography_types),
                    ),
                    encoding="utf-8",
                )
            init = prediction / "__init__.py"
            if not batch_enabled:
                init.write_text("\n".join(line for line in init.read_text(encoding="utf-8").splitlines() if ".batch import" not in line) + "\n", encoding="utf-8")
            # The allowlist above already copied only selected adapters.
    if "records" in modules and "datasets" not in modules:
        _trim_records_data_center(package, batch_enabled=batch_enabled)
        m2_source = package / "assembly" / "m2.py"
        m2_source.write_text(_trim_records_assembly(m2_source.read_text(encoding="utf-8")), encoding="utf-8")
    if m2_selected:
        m2_source = package / "assembly" / "m2.py"
        m2_source.write_text(
            _trim_m2_batch_wiring(m2_source.read_text(encoding="utf-8"), batch_enabled=batch_enabled),
            encoding="utf-8",
        )
    if "prediction" not in modules:
        shutil.rmtree(package / "adapters", ignore_errors=True)

    if "prediction" in modules:
        # Batch is not a registered route/worker in the assembled API.  It is
        # deliberately omitted so its unselected DTO imports cannot leak.
        if not batch_enabled:
            for name in ("batch.py", "batch_health.py", "batch_router.py", "batch_service.py", "batch_worker.py"):
                (package / "modules" / "prediction" / name).unlink(missing_ok=True)
        init = package / "modules" / "prediction" / "__init__.py"
        if init.is_file() and not batch_enabled:
            init.write_text("\n".join(line for line in init.read_text(encoding="utf-8").splitlines() if ".batch import" not in line and "_BATCH_EXPORTS" not in line) + "\n", encoding="utf-8")
        if not batch_enabled:
            metadata = package / "infrastructure" / "metadata.py"
            metadata.write_text(
                metadata.read_text(encoding="utf-8").replace(
                    "    # Importing the batch models registers their tables on the shared M1 Base.\n"
                    "    from chrom_platform.modules.prediction import batch_service as _batch_service  # noqa: F401\n",
                    "",
                ),
                encoding="utf-8",
            )
    if "records" not in modules and "datasets" not in modules and m2_selected:
        # Prediction persistence still needs the canonical record table; route
        # and import services are retained only as internal assembly imports.
        pass
    app = package / "app.py"
    app.write_text(
        _trim_app_source(
            app.read_text(encoding="utf-8"),
            set(manifest.chromatography_types),
            batch_enabled=batch_enabled,
            datasets_enabled="datasets" in modules,
        ),
        encoding="utf-8",
    )


def _migration_metadata(path: Path) -> tuple[str, tuple[str, ...]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id in {"revision", "down_revision"}:
            values[node.targets[0].id] = ast.literal_eval(node.value)
    revision = values.get("revision")
    down = values.get("down_revision")
    if not isinstance(revision, str) or not revision:
        raise ValueError(f"migration revision missing: {path.name}")
    if down is None:
        return revision, ()
    if isinstance(down, str):
        return revision, (down,)
    if isinstance(down, (tuple, list)) and all(isinstance(item, str) and item for item in down):
        return revision, tuple(down)
    raise ValueError(f"migration down_revision invalid: {path.name}")


def _profile_migrations(versions_source: Path, modules: set[str], *, batch_enabled: bool = False) -> set[str]:
    """Select a module's migration roots, then close over the real graph."""

    rows: dict[str, tuple[str, tuple[str, ...]]] = {}
    for path in sorted(versions_source.glob("*.py")):
        if path.name == "__init__.py":
            continue
        revision, down = _migration_metadata(path)
        if revision in rows:
            raise ValueError(f"duplicate migration revision: {revision}")
        rows[revision] = (path.name, down)
    selected: set[str] = set()
    m2 = modules & {"prediction", "records", "datasets"}
    for revision, (filename, _) in rows.items():
        stem = Path(filename).stem.lower()
        if stem.startswith("0001_") or (m2 and (stem.startswith("0002_m2_") or stem == "0007_worker_outbox_lease")):
            selected.add(revision)
        if "records" in modules and stem.startswith("0012_m2_record_export"):
            selected.add(revision)
        if "records" in modules and stem.startswith("0015_m2_prediction_replay_draft"):
            selected.add(revision)
        if batch_enabled and stem.startswith("0014_batch_resolved_snapshot_export"):
            selected.add(revision)
        if batch_enabled and stem.startswith("0019_batch_export_status_repair"):
            selected.add(revision)
        if any(module in stem for module in modules & {"knowledge", "training", "model_center", "assistant"}):
            selected.add(revision)
    if not selected:
        raise ValueError("profile has no migration root")
    changed = True
    while changed:
        changed = False
        for revision in tuple(selected):
            for parent in rows[revision][1]:
                if parent not in rows:
                    raise ValueError(f"migration {revision} depends on missing revision {parent}")
                if parent not in selected:
                    selected.add(parent)
                    changed = True
        # A merge is needed when every branch it joins is already selected;
        # this also discovers future merge revisions without naming a head.
        for revision, (_, down) in rows.items():
            if len(down) > 1 and revision not in selected and set(down) <= selected:
                selected.add(revision)
                changed = True
    return {rows[revision][0] for revision in selected}


def assemble_profile(
    manifest_path: str | Path,
    output_root: str | Path,
    *,
    source_root: str | Path | None = None,
    candidate_receipt_path: str | Path | None = None,
) -> Path:
    manifest = load_manifest(manifest_path)
    selected = set(manifest.modules)
    backend_root = Path(source_root) if source_root is not None else Path(__file__).resolve().parents[3]
    source_package = backend_root / "src"
    output = Path(output_root) / manifest.build_id
    gc_selected = "gc" in manifest.chromatography_types
    receipt_helper = None
    receipt_helper_path = None
    receipt_destination = None
    if gc_selected:
        receipt_helper, receipt_helper_path = _load_gc_derived_receipt_helper()
        requested_receipt = (
            Path(candidate_receipt_path)
            if candidate_receipt_path is not None
            else Path(output_root) / f"{manifest.build_id}.candidate-receipt.json"
        )
        receipt_destination = receipt_helper.validate_receipt_destination(
            requested_receipt,
            package_root=output,
        )
        if output.exists():
            raise FileExistsError("MODEL_PACKAGE_CANDIDATE_OUTPUT_ALREADY_EXISTS")
    if output.exists():
        shutil.rmtree(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    _copy_tree(source_package, output, manifest)
    if gc_selected:
        assert receipt_helper is not None
        output_schema = output / "chrom_platform/modules/prediction/schemas.py"
        source_pin = source_package / "chrom_platform/adapters/gc/package_source_pins.py"
        output_pin = output / "chrom_platform/adapters/gc/package_source_pins.py"
        receipt_helper.derive_output_schema_pin(source_pin, output_pin, output_schema)

    alembic_source = backend_root / "alembic"
    alembic_target = output / "alembic"
    shutil.copytree(
        alembic_source,
        alembic_target,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "versions"),
    )
    versions_source = alembic_source / "versions"
    versions_target = alembic_target / "versions"
    versions_target.mkdir(parents=True, exist_ok=True)
    batch_enabled = any("batch" in tools for tools in manifest.prediction_tools.values())
    keep_migrations = _profile_migrations(versions_source, set(manifest.modules), batch_enabled=batch_enabled)
    for filename in sorted(keep_migrations):
        source = versions_source / filename
        if not source.is_file():
            raise FileNotFoundError(f"profile migration source file missing: {source}")
        shutil.copy2(source, versions_target / filename)
    if not (set(manifest.modules) & {"prediction", "records", "datasets"}):
        (output / "alembic/versions/0002_m2_prediction_data.py").unlink(missing_ok=True)
    elif "records" in set(manifest.modules) and "datasets" not in set(manifest.modules):
        migration = output / "alembic/versions/0002_m2_prediction_data.py"
        migration.write_text(_trim_records_migration(migration.read_text(encoding="utf-8")), encoding="utf-8")
    (output / "build-manifest.json").write_text(json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    worker_modules = {"prediction", "knowledge", "training", "model_center", "assistant"}
    worker_runtime_modules = worker_modules | {"datasets"}
    dependencies = ["fastapi==0.136.3", "pydantic==2.13.4", "sqlalchemy==2.0.50", "alembic==1.18.5", "redis==8.0.1", "rdkit==2026.3.3"]
    if worker_runtime_modules & selected:
        dependencies.extend(("asyncmy==0.2.14", "dramatiq[redis]==2.2.1"))
    scientific_dependencies = {
        "tlc": ["numpy==1.26.4", "torch==2.6.0+cu124", "joblib==1.5.3", "scikit-learn==1.6.1", "lightgbm==4.7.0", "xgboost==2.1.4"],
        "cc": ["numpy==1.26.4", "torch==2.6.0+cu124", "torch-geometric==2.8.0", "mordred==1.2.0"],
        "hplc": ["numpy==1.26.4", "torch==2.6.0+cu124", "torch-geometric==2.8.0", "mordred==1.2.0"],
        "sfc": ["numpy==1.26.4", "pandas==2.3.3", "joblib==1.5.3", "mordred==1.2.0", "scikit-learn==1.6.1"],
        "gc": ["numpy==1.26.4", "pandas==2.3.3", "openpyxl==3.1.5", "torch==2.6.0+cu124", "torch-geometric==2.8.0", "mordred==1.2.0", "joblib==1.5.3", "scikit-learn==1.6.1", "xgboost==2.1.4", "matplotlib==3.11.1"],
    }
    for kind in manifest.chromatography_types:
        dependencies.extend(item for item in scientific_dependencies[kind] if item not in dependencies)
    if "assistant" in selected:
        dependencies.extend((
            "pydantic-ai-slim==2.56.0",
            "pydantic-graph==2.56.0",
            "openai==3.31.0",
            "httpx==0.28.1",
            "httpcore==1.0.9",
            "tiktoken==0.13.0",
        ))
    (output / "requirements-profile.txt").write_text("\n".join(dependencies) + "\n", encoding="utf-8")
    files = []
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.name not in {"SOURCE_MANIFEST.json"}:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            files.append({"path": path.relative_to(output).as_posix(), "sha256": digest})
    registry = {
        "build_id": manifest.build_id,
        "modules": manifest.modules,
        "chromatography_types": manifest.chromatography_types,
        "files": files,
        "migrations": [item["path"] for item in files if "alembic/versions/" in item["path"]],
        "workers": [item["path"] for item in files if item["path"].endswith("worker.py")],
        "worker_entrypoint": "chrom_platform/worker_cli.py" if worker_runtime_modules & set(manifest.modules) else None,
        "dependencies": dependencies,
    }
    if gc_selected:
        assert receipt_helper is not None and receipt_helper_path is not None
        normalized_manifest = manifest.model_dump(mode="json")
        source_inventory = receipt_helper_path.with_name("model_package_source_inventory.json")
        source_integrity_helper = receipt_helper_path.with_name("model_package_source_integrity.py")
        registry.update({
            "normalized_build_manifest": normalized_manifest,
            "build_manifest_sha256": hashlib.sha256(
                json.dumps(normalized_manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "profile_trim_parameters": receipt_helper.profile_trim_parameters(normalized_manifest),
            "profile_transform": {
                "version": receipt_helper.TRANSFORM_VERSION,
                "profile_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "derived_receipt_helper_sha256": hashlib.sha256(receipt_helper_path.read_bytes()).hexdigest(),
                "source_integrity_helper_sha256": hashlib.sha256(source_integrity_helper.read_bytes()).hexdigest(),
                "source_inventory_sha256": hashlib.sha256(source_inventory.read_bytes()).hexdigest(),
            },
        })
    (output / "SOURCE_MANIFEST.json").write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if gc_selected:
        assert receipt_helper is not None and receipt_helper_path is not None and receipt_destination is not None
        receipt = receipt_helper.create_candidate_receipt(
            manifest=manifest.model_dump(mode="json"),
            source_root=source_package,
            package_root=output,
            source_manifest_path=output / "SOURCE_MANIFEST.json",
            profile_source_path=Path(__file__),
            helper_path=receipt_helper_path,
        )
        receipt_helper.write_candidate_receipt(
            receipt_destination,
            receipt,
            package_root=output,
        )
    return output


__all__ = ["assemble_profile"]
