"""Small TLC utilities that always execute the verified model runtime.

The service is intentionally independent from HTTP and queue assembly.  A
caller supplies normalized SMILES and a :class:`TlcRuntime`; no interpolation
or fallback value is produced when the artifact cannot execute.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from collections.abc import Mapping, Sequence
from typing import Any

from .runtime import ALLOWED_SYSTEMS, TlcRuntime, validate_mobile_phase


SYSTEM_IDS = {
    "hexane/ethyl_acetate": frozenset(("hexane", "ethyl_acetate")),
    "dichloromethane/methanol": frozenset(("dichloromethane", "methanol")),
    "hexane/diethyl_ether": frozenset(("hexane", "diethyl_ether")),
}


@dataclass(frozen=True)
class TlcBatchRow:
    smiles: str
    mobile_phase: Mapping[str, float]


@dataclass(frozen=True)
class TlcBatchRowResult:
    row_index: int
    predicted_rf: float | None
    component_predictions: Mapping[str, float] | None
    error: str | None = None


@dataclass(frozen=True)
class TlcConditionResult:
    condition_index: int
    mobile_phase: Mapping[str, float]
    predicted_rf: float
    component_predictions: Mapping[str, float]


@dataclass(frozen=True)
class TlcReverseCandidate:
    system_id: str
    mobile_phase: Mapping[str, float]
    predicted_rf: float
    source: str = "model"


@dataclass(frozen=True)
class TlcReverseResult:
    candidates: tuple[TlcReverseCandidate, ...]
    evaluated: int
    failed: int
    target_reachable: bool
    warnings: tuple[str, ...]


def _require_runtime(runtime: TlcRuntime) -> TlcRuntime:
    if not isinstance(runtime, TlcRuntime):
        raise TypeError("TLC service requires the trusted TlcRuntime implementation")
    if runtime.status.status != "READY":
        raise RuntimeError(runtime.status.reason or "TLC model runtime is unavailable")
    return runtime


def _validate_smiles(smiles: str) -> str:
    if not isinstance(smiles, str) or not smiles.strip():
        raise ValueError("smiles must be a non-empty string")
    return smiles


class TlcPredictionService:
    """Batch, comparison, and reverse-solvent operations over one runtime."""

    def __init__(self, runtime: TlcRuntime) -> None:
        self.runtime = _require_runtime(runtime)

    def predict_one(self, *, smiles: str, mobile_phase: Mapping[str, float]) -> dict[str, Any]:
        return self.runtime.predict(
            smiles=_validate_smiles(smiles),
            mobile_phase=validate_mobile_phase(mobile_phase),
        )

    def predict_batch(self, rows: Sequence[TlcBatchRow], *, max_rows: int = 200) -> tuple[TlcBatchRowResult, ...]:
        if len(rows) > max_rows:
            raise ValueError(f"TLC batch supports at most {max_rows} rows")
        results: list[TlcBatchRowResult] = []
        for index, row in enumerate(rows):
            try:
                raw = self.predict_one(smiles=row.smiles, mobile_phase=row.mobile_phase)
                results.append(TlcBatchRowResult(index, raw["rf"], raw["component_predictions"]))
            except Exception as exc:  # batch rows are explicitly allowed to fail independently
                results.append(TlcBatchRowResult(index, None, None, str(exc)))
        return tuple(results)

    def compare_conditions(
        self,
        *,
        smiles: str,
        conditions: Sequence[Mapping[str, float]],
        max_conditions: int = 200,
    ) -> tuple[TlcConditionResult, ...]:
        if not 1 <= len(conditions) <= max_conditions:
            raise ValueError(f"TLC condition comparison requires 1-{max_conditions} conditions")
        return tuple(
            TlcConditionResult(
                index,
                validate_mobile_phase(condition),
                (raw := self.predict_one(smiles=smiles, mobile_phase=condition))["rf"],
                raw["component_predictions"],
            )
            for index, condition in enumerate(conditions)
        )

    def reverse_solvent(
        self,
        *,
        smiles: str,
        target_rf: tuple[float, float],
        candidate_system_ids: Sequence[str],
        ratio_step: float,
        max_candidates: int = 200,
    ) -> TlcReverseResult:
        minimum, maximum = target_rf
        if not (0.0 <= minimum <= maximum <= 1.0):
            raise ValueError("target_rf must satisfy 0 <= min <= max <= 1")
        if not math.isfinite(ratio_step) or not 0.0 < ratio_step <= 1.0:
            raise ValueError("ratio_step must be finite and in (0, 1]")
        if not 1 <= max_candidates <= 200:
            raise ValueError("max_candidates must be in 1..200")
        systems: list[tuple[str, frozenset[str]]] = []
        for system_id in candidate_system_ids:
            if system_id not in SYSTEM_IDS:
                raise ValueError(f"unsupported TLC solvent system: {system_id}")
            system = SYSTEM_IDS[system_id]
            if system not in ALLOWED_SYSTEMS:
                raise ValueError(f"unsupported TLC solvent system: {system_id}")
            systems.append((system_id, system))
        if not systems:
            raise ValueError("at least one solvent system is required")

        candidates: list[TlcReverseCandidate] = []
        evaluated = failed = 0
        for system_id, system in systems:
            ordered = sorted(system)
            steps = max(1, math.ceil(1.0 / ratio_step))
            # Both components must remain active; zero/one would be a unary
            # phase and is outside the author's binary training contract.
            for step in range(1, steps):
                if evaluated >= max_candidates:
                    break
                ratio = min(1.0, step * ratio_step)
                phase = {ordered[0]: ratio, ordered[1]: 1.0 - ratio}
                try:
                    raw = self.predict_one(smiles=smiles, mobile_phase=phase)
                except Exception:
                    failed += 1
                    evaluated += 1
                    continue
                evaluated += 1
                if minimum <= raw["rf"] <= maximum:
                    candidates.append(TlcReverseCandidate(system_id, phase, raw["rf"]))
            if evaluated >= max_candidates:
                break
        warnings: list[str] = []
        if failed:
            warnings.append("PARTIAL_FAILURE: one or more real model evaluations failed")
        if not candidates:
            warnings.append("TARGET_UNREACHABLE: no directly predicted solvent point reaches target Rf")
        return TlcReverseResult(tuple(candidates), evaluated, failed, bool(candidates), tuple(warnings))


__all__ = [
    "SYSTEM_IDS",
    "TlcBatchRow",
    "TlcBatchRowResult",
    "TlcConditionResult",
    "TlcPredictionService",
    "TlcReverseCandidate",
    "TlcReverseResult",
]
