"""SFC feature construction and controlled model loading.

Scientific feature functions are adapted from the reviewed SFC Predictor
v0.1/v1.1 ``inference.py`` (see ``backend/REUSE_MANIFEST.json``).  HTTP,
SQLite, configuration imports, and source-side defaults are intentionally
absent here.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from threading import Lock
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from chrom_platform.core.errors import InputValidationError


ANALYTE_BASES = [
    "mw_smiles", "tpsa_smiles", "nrotbs_smiles", "hba_smiles", "hbd_smiles",
    "logp_smiles", "rpsa_smiles", "rasa_smiles", "mdec_smiles", "mats_smiles",
]
MODIFIER_FEATURE_COLUMNS = [
    "mw_modi", "tpsa_modi", "nrotbs_modi", "hba_modi", "hbd_modi",
    "logp_modi", "rpsa_modi", "rasa_modi", "mdec_modi", "mats_modi",
]
REQUIRED_FEATURE_COUNT = 66
MAX_CANDIDATES = 100_000
OBSERVED_DISTINCT_ROWS = "observed_distinct_rows"
COUPLED_DISCRETE_CONDITIONS = "coupled_discrete_conditions"
SFC_EXPANSION_POLICY_VERSION = "1"
TRUSTED_COUPLED_CANDIDATE_SOURCE_SHA256 = (
    "4a76421324aca6865d5dbd7df1e57d5df5362d231d7e3ee3dd34cf4db48c9ab1"
)
_SCOPE_TO_TOOL = {"all_column": "full_column", "single_column": "single_column"}
_CATEGORICAL_FEATURES = frozenset({"column", "stationary_phase", "mode"})
ALLOWED_NAN_FEATURES = (
    "mdec_modi",
    "mdec_smiles_pair_value",
    "mdec_smiles_mean",
    "mdec_smiles_median",
    "mdec_smiles_abs_diff",
    "mdec_smiles_range",
)
_ALLOWED_NAN_FEATURES = frozenset(ALLOWED_NAN_FEATURES)

# Golden inputs remain pinned for startup regression verification. They do not
# define the full-column candidate service domain.
FULL_COLUMN_GOLDEN_CONDITIONS = {
    "column": "AD",
    "stationary_phase": "amylose tris (3,5-dimethylphenylcarbamate)",
    "mode": "coated",
    "length_mm": 150.0,
    "inner_diameter_mm": 4.6,
    "particle_size_um": 5.0,
    "column_temperature_c": 35.0,
    "flow_rate_ml_min": 2.5,
}
FULL_COLUMN_GOLDEN_MODIFIER_SMILES = "CO"
FULL_COLUMN_GOLDEN_MODIFIER_TOTAL_FRACTION = 0.2
TRUSTED_FULL_COLUMN_CANDIDATE_SOURCE_SHA256 = (
    "266a6d00df80166c3faf64be9dc21748d9a39a9f7106a24b8741fdd628eb9c1d"
)


class SfcAssetUnavailable(RuntimeError):
    """A model cannot be executed because trusted evidence is incomplete."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class SfcRequestValidationError(InputValidationError):
    """A caller supplied SFC scope/condition is outside the pinned contract."""


class SfcCandidateScopeError(KeyError):
    """A catalog scope is absent or does not match its stored binding."""


class SfcCandidateScopeSelectionError(ValueError):
    """A validated axis selection has no matches or exceeds its candidate limit."""

    def __init__(
        self, *, reason: str, matched_count: int, max_candidates: int,
        expansion_mode: str = OBSERVED_DISTINCT_ROWS,
        observed_count: int | None = None,
        unobserved_count: int | None = None,
    ) -> None:
        self.reason = reason
        self.matched_count = matched_count
        self.max_candidates = max_candidates
        self.expansion_mode = expansion_mode
        self.observed_count = observed_count
        self.unobserved_count = unobserved_count
        message = "candidate scope is empty" if reason == "empty" else "candidate scope exceeds the limit"
        super().__init__(message)


@dataclass(frozen=True)
class SfcCandidate:
    candidate_id: str
    column: str
    stationary_phase: str
    mode: str
    length_mm: float
    inner_diameter_mm: float
    particle_size_um: float
    column_temperature: float
    flow_rate: float
    observed_in_source: bool = True

    def as_features(self) -> dict[str, Any]:
        return {
            "column": self.column,
            "stationary_phase": self.stationary_phase,
            "mode": self.mode,
            "length_mm": self.length_mm,
            "inner_diameter_mm": self.inner_diameter_mm,
            "particle_size_um": self.particle_size_um,
            "column_temperature": self.column_temperature,
            "flow_rate": self.flow_rate,
        }

    def as_conditions(self) -> dict[str, Any]:
        return {
            "column": self.column,
            "stationary_phase": self.stationary_phase,
            "mode": self.mode,
            "length_mm": self.length_mm,
            "inner_diameter_mm": self.inner_diameter_mm,
            "particle_size_um": self.particle_size_um,
            "column_temperature_c": self.column_temperature,
            "flow_rate_ml_min": self.flow_rate,
        }


@dataclass(frozen=True)
class SfcModelDomain:
    """Server-resolved model domain used to filter read-only catalog options."""

    model_id: str
    model_version: str
    scope: str
    allowed_columns: tuple[str, ...] = ()
    stationary_phase: str | None = None
    mode: str | None = None

    @classmethod
    def from_registry_entry(cls, entry: "_ModelEntry") -> "SfcModelDomain":
        return cls(
            model_id=entry.model_id,
            model_version=entry.version,
            scope=str(entry.scope),
            allowed_columns=entry.allowed_columns,
            stationary_phase=entry.stationary_phase,
            mode=entry.mode,
        )


class SfcCandidateCatalog:
    """Server-owned candidates plus immutable project/model-bound scopes."""

    DEFAULT_SCOPE_ID = "sfc_conditions_default"
    _COLUMNS = (
        "column_2", "stationary_phase", "mode", "length_mm",
        "inner_diameter_mm", "particle_size_um", "column_temperature", "flow_rate",
    )

    def __init__(
        self,
        sqlite_path: str | Path,
        *,
        max_candidates: int = MAX_CANDIDATES,
        scope_storage_path: str | Path | None = None,
    ) -> None:
        self.path = Path(sqlite_path).resolve()
        self.scope_storage_path = Path(scope_storage_path or f"{self.path}.scopes.sqlite3").resolve()
        self.max_candidates = max_candidates
        if not self.path.is_file():
            raise SfcAssetUnavailable("CANDIDATE_DATABASE_MISSING")
        self._candidates = self._load()
        self._scopes: dict[str, tuple[SfcCandidate, ...]] = {self.DEFAULT_SCOPE_ID: self._candidates}
        self._scope_limits: dict[str, int] = {self.DEFAULT_SCOPE_ID: self.max_candidates}
        self._scope_policies: dict[str, dict[str, Any] | None] = {self.DEFAULT_SCOPE_ID: None}
        self._model_scope: dict[str, Any] | None = None

    @property
    def supported_expansion_modes(self) -> list[str]:
        modes = [OBSERVED_DISTINCT_ROWS]
        if self._trusted_coupled_source_sha256() is not None:
            modes.append(COUPLED_DISCRETE_CONDITIONS)
        return modes

    def _trusted_coupled_source_sha256(self) -> str | None:
        try:
            digest = _sha256(self.path)
        except OSError:
            return None
        return digest if digest == TRUSTED_COUPLED_CANDIDATE_SOURCE_SHA256 else None

    def _policy(self, expansion_mode: str) -> dict[str, str]:
        if expansion_mode != COUPLED_DISCRETE_CONDITIONS:
            raise ValueError("SFC candidate expansion mode is unsupported")
        digest = _sha256(self.path)
        if digest != TRUSTED_COUPLED_CANDIDATE_SOURCE_SHA256:
            raise ValueError("coupled discrete candidate source is not verified")
        return {
            "expansion_mode": expansion_mode,
            "policy_version": SFC_EXPANSION_POLICY_VERSION,
            "candidate_source_sha256": digest,
        }

    def bind_model_scope(
        self,
        *,
        model_id: str,
        model_version: str,
        scope: str,
        allowed_columns: Sequence[str] = (),
        stationary_phase: str | None = None,
        mode: str | None = None,
    ) -> None:
        """Bind candidate creation to the model domain exposed by this adapter."""
        if scope not in _SCOPE_TO_TOOL:
            raise SfcAssetUnavailable("MODEL_SCOPE_UNSUPPORTED")
        binding = {
            "model_id": str(model_id),
            "model_version": str(model_version),
            "scope": scope,
            "allowed_columns": tuple(str(value) for value in allowed_columns),
            "stationary_phase": stationary_phase,
            "mode": mode,
        }
        if self._model_scope is not None and self._model_scope != binding:
            raise SfcAssetUnavailable("CANDIDATE_SCOPE_MODEL_BINDING_MISMATCH")
        self._model_scope = binding
        if scope == "all_column":
            self._scopes[self.DEFAULT_SCOPE_ID] = self._visible_candidates(self._candidates)

    def verify_source_hash(self, expected_sha256: str = TRUSTED_FULL_COLUMN_CANDIDATE_SOURCE_SHA256) -> None:
        """Verify the immutable source used by the pinned full-column golden."""
        if _sha256(self.path) != expected_sha256.lower():
            raise SfcAssetUnavailable("CANDIDATE_DATABASE_HASH_MISMATCH")

    def _check_scope_binding(self, *, model_id: str, model_version: str) -> None:
        if self._model_scope is None:
            return
        if (str(model_id), str(model_version)) != (
            self._model_scope["model_id"], self._model_scope["model_version"]
        ):
            raise ValueError("candidate scope model binding is invalid")

    @staticmethod
    def _distinct_observed_candidates(candidates: Sequence[SfcCandidate]) -> tuple[SfcCandidate, ...]:
        # The source has formatting variants that normalize to the same tuple.
        # Keep the first sorted source ID; do not change shared/single counts.
        unique: dict[tuple[tuple[str, Any], ...], SfcCandidate] = {}
        for candidate in candidates:
            unique.setdefault(tuple(candidate.as_conditions().items()), candidate)
        return tuple(unique.values())

    def _visible_candidates(self, candidates: Sequence[SfcCandidate]) -> tuple[SfcCandidate, ...]:
        if self._model_scope is None or self._model_scope["scope"] != "all_column":
            return tuple(candidates)
        return self._distinct_observed_candidates(candidates)

    def _scope_connection(self) -> sqlite3.Connection:
        try:
            self.scope_storage_path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(str(self.scope_storage_path), timeout=30.0)
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sfc_candidate_scope (
                    scope_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    model_version TEXT NOT NULL,
                    axes_json TEXT NOT NULL,
                    candidates_json TEXT NOT NULL,
                    max_candidates INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            return connection
        except (OSError, sqlite3.Error) as exc:
            raise SfcAssetUnavailable("CANDIDATE_SCOPE_STORE_UNAVAILABLE") from exc

    @staticmethod
    def _scope_document(raw: str) -> tuple[tuple[SfcCandidate, ...], dict[str, str] | None]:
        try:
            values = json.loads(raw)
            policy = None
            if isinstance(values, Mapping):
                if (
                    set(values) != {"format_version", "policy", "candidates"}
                    or type(values["format_version"]) is not int
                    or values["format_version"] != 2
                ):
                    raise ValueError
                policy_value = values["policy"]
                if not isinstance(policy_value, Mapping) or set(policy_value) != {
                    "expansion_mode", "policy_version", "candidate_source_sha256"
                }:
                    raise ValueError
                if (
                    policy_value["expansion_mode"] != COUPLED_DISCRETE_CONDITIONS
                    or policy_value["policy_version"] != SFC_EXPANSION_POLICY_VERSION
                    or not isinstance(policy_value["candidate_source_sha256"], str)
                    or len(policy_value["candidate_source_sha256"]) != 64
                    or any(char not in "0123456789abcdef" for char in policy_value["candidate_source_sha256"])
                ):
                    raise ValueError
                policy = dict(policy_value)
                values = values["candidates"]
            if not isinstance(values, list) or not values or len(values) > MAX_CANDIDATES:
                raise ValueError
            candidates = []
            for index, item in enumerate(values):
                if not isinstance(item, Mapping):
                    raise ValueError
                if policy is not None:
                    expected = {
                        "candidate_id", "column", "stationary_phase", "mode", "length_mm",
                        "inner_diameter_mm", "particle_size_um", "column_temperature", "flow_rate",
                        "observed_in_source",
                    }
                    if (
                        set(item) != expected
                        or not isinstance(item.get("candidate_id"), str)
                        or not item["candidate_id"].strip()
                        or any(not isinstance(item.get(key), str) for key in ("column", "stationary_phase", "mode"))
                        or not isinstance(item.get("observed_in_source"), bool)
                        or any(type(item.get(key)) not in (int, float) for key in (
                            "length_mm", "inner_diameter_mm", "particle_size_um",
                            "column_temperature", "flow_rate",
                        ))
                    ):
                        raise ValueError
                candidates.append(SfcRuntime._candidate(item, index))
            return tuple(candidates), policy
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SfcAssetUnavailable("CANDIDATE_SCOPE_STORE_INVALID") from exc

    @classmethod
    def _scope_candidates(cls, raw: str) -> tuple[SfcCandidate, ...]:
        return cls._scope_document(raw)[0]

    def _stored_scope(
        self,
        scope_id: str,
        *,
        project_id: str | None = None,
        model_id: str | None = None,
        model_version: str | None = None,
    ) -> tuple[SfcCandidate, ...] | None:
        if scope_id == self.DEFAULT_SCOPE_ID:
            return self._scopes[scope_id]
        if any(value is None or not str(value).strip() for value in (project_id, model_id, model_version)):
            raise SfcCandidateScopeError(scope_id)
        try:
            with self._scope_connection() as connection:
                row = connection.execute(
                    "SELECT project_id, model_id, model_version, axes_json, candidates_json, max_candidates FROM sfc_candidate_scope WHERE scope_id = ?",
                    (scope_id,),
                ).fetchone()
        except SfcAssetUnavailable:
            raise
        except sqlite3.Error as exc:
            raise SfcAssetUnavailable("CANDIDATE_SCOPE_STORE_INVALID") from exc
        if row is None:
            return None
        if (row[0], row[1], row[2]) != (str(project_id), str(model_id), str(model_version)):
            raise SfcCandidateScopeError(scope_id)
        candidates, policy = self._scope_document(row[4])
        limit = row[5]
        if not isinstance(limit, int) or limit < len(candidates) or limit > MAX_CANDIDATES:
            raise SfcAssetUnavailable("CANDIDATE_SCOPE_STORE_INVALID")
        if policy is not None:
            try:
                axes = json.loads(row[3])
                if not isinstance(axes, Mapping):
                    raise ValueError
                normalized, selected = self._select_scope_candidates(
                    axes, project_id=str(project_id), model_id=str(model_id), model_version=str(model_version),
                    max_candidates=limit, expansion_mode=policy["expansion_mode"],
                )
                current_policy = self._policy(policy["expansion_mode"])
                if policy != current_policy or self._scope_id(
                    project_id=str(project_id), model_id=str(model_id), model_version=str(model_version),
                    axes=normalized, policy=policy,
                ) != scope_id:
                    raise ValueError
                regenerated = tuple(
                    SfcCandidate(
                        candidate_id=f"{scope_id}-{index + 1:04d}",
                        **candidate.as_features(),
                        observed_in_source=candidate.observed_in_source,
                    )
                    for index, candidate in enumerate(selected)
                )
                if candidates != regenerated:
                    raise ValueError
            except (KeyError, TypeError, ValueError, SfcCandidateScopeSelectionError) as exc:
                raise SfcAssetUnavailable("CANDIDATE_SCOPE_STORE_INVALID") from exc
        if self._model_scope is not None and self._model_scope["scope"] == "all_column":
            self._check_scope_binding(model_id=str(model_id), model_version=str(model_version))
            ids = [candidate.candidate_id for candidate in candidates]
            conditions = [tuple(candidate.as_conditions().items()) for candidate in candidates]
            observed = {
                tuple(candidate.as_conditions().items())
                for candidate in self._visible_candidates(self._candidates)
            }
            if (
                any(not candidate_id.strip() for candidate_id in ids)
                or len(ids) != len(set(ids))
                or len(conditions) != len(set(conditions))
                or (policy is None and any(condition not in observed for condition in conditions))
            ):
                raise SfcAssetUnavailable("CANDIDATE_SCOPE_STORE_INVALID")
        self._scope_limits[scope_id] = limit
        self._scope_policies[scope_id] = policy
        return candidates

    def _scope_limit(self, scope_id: str) -> int:
        limit = self._scope_limits.get(scope_id)
        if limit is None:
            raise SfcCandidateScopeError(scope_id)
        return limit

    def scope_binding(self, scope_id: str) -> dict[str, str] | None:
        """Read a stored scope's immutable binding without creating its table."""
        if scope_id == self.DEFAULT_SCOPE_ID:
            return None
        if not self.scope_storage_path.is_file():
            return None
        uri = f"file:{self.scope_storage_path.as_posix()}?mode=ro"
        try:
            with sqlite3.connect(uri, uri=True) as connection:
                row = connection.execute(
                    "SELECT project_id, model_id, model_version FROM sfc_candidate_scope WHERE scope_id = ?",
                    (scope_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise SfcAssetUnavailable("CANDIDATE_SCOPE_STORE_INVALID") from exc
        if row is None:
            return None
        return {"project_id": row[0], "model_id": row[1], "model_version": row[2]}

    def _load(self) -> tuple[SfcCandidate, ...]:
        query = """
            SELECT DISTINCT column_2, stationary_phase, mode, length_mm,
                   inner_diameter_mm, particle_size_um, column_temperature, flow_rate
            FROM result_with_advanced_descriptors
            WHERE column_2 IS NOT NULL AND stationary_phase IS NOT NULL AND mode IS NOT NULL
              AND length_mm IS NOT NULL AND inner_diameter_mm IS NOT NULL
              AND particle_size_um IS NOT NULL AND column_temperature IS NOT NULL
              AND flow_rate IS NOT NULL
            ORDER BY column_2, stationary_phase, mode, length_mm,
                     inner_diameter_mm, particle_size_um, column_temperature, flow_rate
        """
        try:
            with sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True) as connection:
                rows = connection.execute(query).fetchall()
        except (OSError, sqlite3.Error) as exc:
            raise SfcAssetUnavailable("CANDIDATE_DATABASE_INVALID") from exc
        if not rows or len(rows) > self.max_candidates:
            raise SfcAssetUnavailable("CANDIDATE_SCOPE_LIMIT_INVALID")
        candidates = []
        for index, row in enumerate(rows):
            numeric = [float(value) for value in row[3:]]
            if any(not math.isfinite(value) for value in numeric):
                raise SfcAssetUnavailable("CANDIDATE_DATABASE_NONFINITE")
            candidates.append(SfcCandidate(
                candidate_id=f"sfc-candidate-{index + 1:04d}",
                column=str(row[0]).strip(), stationary_phase=str(row[1]).strip(), mode=str(row[2]).strip(),
                length_mm=numeric[0], inner_diameter_mm=numeric[1], particle_size_um=numeric[2],
                column_temperature=numeric[3], flow_rate=numeric[4],
            ))
        return tuple(candidates)

    def resolve(
        self,
        scope_id: str,
        *,
        project_id: str | None = None,
        model_id: str | None = None,
        model_version: str | None = None,
    ) -> tuple[SfcCandidate, ...]:
        candidates = self._scopes.get(scope_id)
        if candidates is None:
            candidates = self._stored_scope(
                scope_id, project_id=project_id, model_id=model_id, model_version=model_version
            )
            if candidates is None:
                raise SfcCandidateScopeError(scope_id)
            self._scopes[scope_id] = candidates
        elif scope_id != self.DEFAULT_SCOPE_ID:
            stored = self._stored_scope(
                scope_id, project_id=project_id, model_id=model_id, model_version=model_version
            )
            if stored is None:
                raise SfcCandidateScopeError(scope_id)
            candidates = stored
        return self._visible_candidates(candidates)

    def resolve_with_policy(
        self,
        scope_id: str,
        *,
        project_id: str | None = None,
        model_id: str | None = None,
        model_version: str | None = None,
    ) -> dict[str, Any]:
        candidates = self.resolve(
            scope_id, project_id=project_id, model_id=model_id, model_version=model_version
        )
        policy = self._scope_policies.get(scope_id)
        observed_count = sum(candidate.observed_in_source for candidate in candidates)
        return {
            "candidates": candidates,
            "scope_id": scope_id,
            "policy": dict(policy) if policy is not None else None,
            "candidate_count": len(candidates),
            "observed_count": observed_count if policy is not None else None,
            "unobserved_count": len(candidates) - observed_count if policy is not None else None,
        }

    def create_scope(
        self,
        axes: Mapping[str, Sequence[Any]],
        *,
        project_id: str,
        model_id: str,
        model_version: str,
        max_candidates: int = MAX_CANDIDATES,
        expansion_mode: str = OBSERVED_DISTINCT_ROWS,
    ) -> dict[str, Any]:
        """Expand a validated axis selection once and retain its immutable IDs."""
        normalized, selected = self._select_scope_candidates(
            axes,
            project_id=project_id,
            model_id=model_id,
            model_version=model_version,
            max_candidates=max_candidates,
            expansion_mode=expansion_mode,
        )
        policy = self._policy(expansion_mode) if expansion_mode == COUPLED_DISCRETE_CONDITIONS else None
        scope_id = (
            self._scope_id(
                project_id=project_id, model_id=model_id, model_version=model_version,
                axes=normalized, policy=policy,
            )
            if policy is not None else self._legacy_scope_id(
                project_id=project_id, model_id=model_id, model_version=model_version, axes=normalized,
            )
        )
        scoped = tuple(
            SfcCandidate(
                candidate_id=f"{scope_id}-{index + 1:04d}",
                **candidate.as_features(),
                observed_in_source=candidate.observed_in_source,
            )
            for index, candidate in enumerate(selected)
        )
        if scope_id in self._scopes and self._scopes[scope_id] != scoped:
            raise SfcAssetUnavailable("CANDIDATE_SCOPE_ID_COLLISION")
        try:
            with self._scope_connection() as connection:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO sfc_candidate_scope
                    (scope_id, project_id, model_id, model_version, axes_json, candidates_json, max_candidates, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    """,
                    (
                        scope_id, project_id, model_id, model_version,
                        json.dumps(normalized, sort_keys=True, separators=(",", ":")),
                        json.dumps(
                            ({
                                "format_version": 2,
                                "policy": policy,
                                "candidates": [
                                    {"candidate_id": item.candidate_id, **item.as_features(),
                                     "observed_in_source": item.observed_in_source}
                                    for item in scoped
                                ],
                            } if policy is not None else [
                                {"candidate_id": item.candidate_id, **item.as_features()}
                                for item in scoped
                            ]),
                            sort_keys=True, separators=(",", ":"),
                        ),
                        max_candidates,
                    ),
                )
                connection.commit()
        except SfcAssetUnavailable:
            raise
        except sqlite3.Error as exc:
            raise SfcAssetUnavailable("CANDIDATE_SCOPE_STORE_UNAVAILABLE") from exc
        self._scopes.setdefault(scope_id, scoped)
        self._scope_limits[scope_id] = max_candidates
        self._scope_policies[scope_id] = policy
        return self.describe(scope_id, project_id=project_id, model_id=model_id, model_version=model_version)

    @staticmethod
    def _scope_id(
        *, project_id: str, model_id: str, model_version: str,
        axes: Mapping[str, Any], policy: Mapping[str, Any],
    ) -> str:
        digest = hashlib.sha256(json.dumps({
            "project_id": project_id,
            "model_id": model_id,
            "model_version": model_version,
            "axes": axes,
            "policy": policy,
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]
        return f"sfc-scope-{digest}"

    @staticmethod
    def _legacy_scope_id(
        *, project_id: str, model_id: str, model_version: str, axes: Mapping[str, Any]
    ) -> str:
        digest = hashlib.sha256(json.dumps({
            "project_id": project_id,
            "model_id": model_id,
            "model_version": model_version,
            "axes": axes,
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]
        return f"sfc-scope-{digest}"

    def preview_scope(
        self,
        axes: Mapping[str, Sequence[Any]],
        *,
        project_id: str,
        model_id: str,
        model_version: str,
        max_candidates: int = MAX_CANDIDATES,
        expansion_mode: str = OBSERVED_DISTINCT_ROWS,
    ) -> dict[str, Any]:
        """Count a scope selection without persisting or materializing a scope."""
        try:
            _normalized, selected = self._select_scope_candidates(
                axes,
                project_id=project_id,
                model_id=model_id,
                model_version=model_version,
                max_candidates=max_candidates,
                expansion_mode=expansion_mode,
            )
        except SfcCandidateScopeSelectionError as exc:
            return {
                "project_id": project_id,
                "model_id": model_id,
                "model_version": model_version,
                "candidate_count": exc.matched_count,
                "max_candidates": exc.max_candidates,
                "expansion_mode": exc.expansion_mode,
                "policy": self._policy(expansion_mode) if expansion_mode == COUPLED_DISCRETE_CONDITIONS else None,
                "observed_count": exc.observed_count,
                "unobserved_count": exc.unobserved_count,
                "status": "empty" if exc.reason == "empty" else "over_limit",
            }
        observed_count = sum(candidate.observed_in_source for candidate in selected)
        return {
            "project_id": project_id,
            "model_id": model_id,
            "model_version": model_version,
            "candidate_count": len(selected),
            "max_candidates": max_candidates,
            "expansion_mode": expansion_mode,
            "policy": self._policy(expansion_mode) if expansion_mode == COUPLED_DISCRETE_CONDITIONS else None,
            "observed_count": observed_count,
            "unobserved_count": len(selected) - observed_count,
            "status": "ready",
        }

    def _select_scope_candidates(
        self,
        axes: Mapping[str, Sequence[Any]],
        *,
        project_id: str,
        model_id: str,
        model_version: str,
        max_candidates: int,
        expansion_mode: str = OBSERVED_DISTINCT_ROWS,
    ) -> tuple[dict[str, list[str | float]], list[SfcCandidate]]:
        """Validate and filter observed rows without creating or storing a scope."""
        if any(not isinstance(value, str) or not value.strip() for value in (project_id, model_id, model_version)):
            raise ValueError("candidate scope binding is invalid")
        self._check_scope_binding(model_id=model_id, model_version=model_version)
        if max_candidates < 1 or max_candidates > MAX_CANDIDATES:
            raise ValueError("candidate scope limit is invalid")
        if expansion_mode not in {OBSERVED_DISTINCT_ROWS, COUPLED_DISCRETE_CONDITIONS}:
            raise ValueError("SFC candidate expansion mode is unsupported")
        if expansion_mode == COUPLED_DISCRETE_CONDITIONS:
            self._policy(expansion_mode)
        allowed = {"column", "stationary_phase", "mode", "length_mm", "inner_diameter_mm", "particle_size_um", "column_temperature_c", "flow_rate_ml_min"}
        if not axes or set(axes) - allowed or any(not values for values in axes.values()):
            raise ValueError("candidate scope axes are invalid")
        normalized: dict[str, list[str | float]] = {}
        for key, values in axes.items():
            normalized_values: set[str | float] = set()
            for value in values:
                if isinstance(value, str):
                    if not value.strip():
                        raise ValueError("candidate scope axes are invalid")
                    normalized_values.add(value)
                else:
                    number = float(value)
                    if not math.isfinite(number):
                        raise ValueError("candidate scope axes must be finite")
                    normalized_values.add(number)
            normalized[key] = sorted(normalized_values, key=str)
        source_candidates = (
            self._visible_candidates(self._candidates)
            if self._model_scope is not None and self._model_scope["scope"] == "all_column"
            else self._candidates
        )
        if expansion_mode == COUPLED_DISCRETE_CONDITIONS:
            hardware_axes = set(allowed) - {"column_temperature_c", "flow_rate_ml_min"}
            selected = [
                candidate for candidate in source_candidates
                if all(
                    candidate.as_conditions().get(key) in values
                    for key, values in normalized.items() if key in hardware_axes
                )
            ]
        else:
            selected = []
            for candidate in source_candidates:
                conditions = candidate.as_conditions()
                if all(conditions.get(key) in values for key, values in normalized.items()):
                    selected.append(candidate)
        if self._model_scope is not None and self._model_scope["scope"] != "all_column":
            allowed_columns = set(self._model_scope["allowed_columns"])
            if allowed_columns and any(item.column not in allowed_columns for item in selected):
                raise ValueError("SFC candidate scope exceeds the bound model column domain")
            if self._model_scope["stationary_phase"] and any(
                item.stationary_phase != self._model_scope["stationary_phase"] for item in selected
            ):
                raise ValueError("SFC candidate scope exceeds the bound stationary-phase domain")
            if self._model_scope["mode"] and any(item.mode != self._model_scope["mode"] for item in selected):
                raise ValueError("SFC candidate scope exceeds the bound mode domain")
        if expansion_mode == COUPLED_DISCRETE_CONDITIONS:
            selected, matched_count, observed_count = self._expand_coupled_discrete_conditions(
                selected, axes=normalized, max_candidates=max_candidates
            )
            if matched_count == 0:
                raise SfcCandidateScopeSelectionError(
                    reason="empty", matched_count=0, max_candidates=max_candidates,
                    expansion_mode=expansion_mode, observed_count=0, unobserved_count=0,
                )
            if selected is None:
                raise SfcCandidateScopeSelectionError(
                    reason="over_limit", matched_count=matched_count, max_candidates=max_candidates,
                    expansion_mode=expansion_mode, observed_count=observed_count,
                    unobserved_count=matched_count - observed_count,
                )
            return normalized, selected
        if not selected:
            raise SfcCandidateScopeSelectionError(
                reason="empty", matched_count=0, max_candidates=max_candidates,
                expansion_mode=expansion_mode, observed_count=0, unobserved_count=0,
            )
        if len(selected) > max_candidates:
            observed_count = sum(candidate.observed_in_source for candidate in selected)
            raise SfcCandidateScopeSelectionError(
                reason="over_limit", matched_count=len(selected), max_candidates=max_candidates,
                expansion_mode=expansion_mode, observed_count=observed_count,
                unobserved_count=len(selected) - observed_count,
            )
        return normalized, selected

    @staticmethod
    def _expand_coupled_discrete_conditions(
        candidates: Sequence[SfcCandidate], *, axes: Mapping[str, Sequence[Any]], max_candidates: int
    ) -> tuple[list[SfcCandidate] | None, int, int]:
        """Cross selected source temperature/flow values within each matching hardware group."""
        hardware_keys = (
            "column", "stationary_phase", "mode", "length_mm",
            "inner_diameter_mm", "particle_size_um",
        )
        groups: dict[tuple[Any, ...], tuple[SfcCandidate, list[SfcCandidate]]] = {}
        for candidate in candidates:
            conditions = candidate.as_conditions()
            hardware = tuple(conditions[key] for key in hardware_keys)
            if hardware not in groups:
                groups[hardware] = (candidate, [])
            groups[hardware][1].append(candidate)
        selected_temperatures = set(axes.get("column_temperature_c", ()))
        selected_flows = set(axes.get("flow_rate_ml_min", ()))
        grouped_values: list[tuple[SfcCandidate, set[float], set[float], set[tuple[Any, ...]]]] = []
        matched_count = 0
        observed_tuples: set[tuple[Any, ...]] = set()
        for base, source_rows in groups.values():
            source_temperatures = {candidate.column_temperature for candidate in source_rows}
            source_flows = {candidate.flow_rate for candidate in source_rows}
            temperatures = source_temperatures & selected_temperatures if selected_temperatures else source_temperatures
            flows = source_flows & selected_flows if selected_flows else source_flows
            if not temperatures or not flows:
                continue
            source_conditions = {tuple(candidate.as_conditions().values()) for candidate in source_rows}
            grouped_values.append((base, temperatures, flows, source_conditions))
            matched_count += len(temperatures) * len(flows)
            observed_tuples.update(
                condition for condition in source_conditions
                if condition[6] in temperatures and condition[7] in flows
            )
        observed_count = len(observed_tuples)
        if matched_count == 0:
            return [], 0, 0
        if matched_count > max_candidates:
            return None, matched_count, observed_count
        expanded: list[SfcCandidate] = []
        for base, temperatures, flows, source_conditions in grouped_values:
            for temperature in sorted(temperatures):
                for flow in sorted(flows):
                    candidate = SfcCandidate(
                        candidate_id=base.candidate_id,
                        column=base.column,
                        stationary_phase=base.stationary_phase,
                        mode=base.mode,
                        length_mm=base.length_mm,
                        inner_diameter_mm=base.inner_diameter_mm,
                        particle_size_um=base.particle_size_um,
                        column_temperature=temperature,
                        flow_rate=flow,
                        observed_in_source=(
                            base.column, base.stationary_phase, base.mode,
                            base.length_mm, base.inner_diameter_mm, base.particle_size_um,
                            temperature, flow,
                        ) in source_conditions,
                    )
                    expanded.append(candidate)
        return sorted(expanded, key=lambda item: tuple(item.as_conditions().values())), matched_count, observed_count

    def describe(
        self,
        scope_id: str = DEFAULT_SCOPE_ID,
        *,
        project_id: str | None = None,
        model_id: str | None = None,
        model_version: str | None = None,
    ) -> dict[str, Any]:
        candidates = self.resolve(
            scope_id, project_id=project_id, model_id=model_id, model_version=model_version
        )
        candidates = self._visible_candidates(candidates)
        if not candidates:
            raise KeyError(scope_id)
        policy = self._scope_policies.get(scope_id)
        observed_count = sum(candidate.observed_in_source for candidate in candidates)
        value = {
            "id": scope_id,
            "candidate_count": len(candidates),
            "max_candidates": self._scope_limit(scope_id),
            "expansion": (
                policy["expansion_mode"] if policy is not None
                else "server_cartesian_or_historical_distinct_rows"
            ),
            "expansion_mode": policy["expansion_mode"] if policy is not None else OBSERVED_DISTINCT_ROWS,
            "policy": policy,
            "observed_count": observed_count,
            "unobserved_count": len(candidates) - observed_count,
            "conditions": [
                {**candidate.as_conditions(), "observed_in_source": candidate.observed_in_source if policy is not None else None}
                for candidate in candidates[:500]
            ],
            "truncated": len(candidates) > 500,
        }
        if scope_id != self.DEFAULT_SCOPE_ID:
            value.update({"project_id": project_id, "model_id": model_id, "model_version": model_version})
        return value

    def options(
        self,
        scope_id: str = DEFAULT_SCOPE_ID,
        *,
        project_id: str | None = None,
        model_id: str | None = None,
        model_version: str | None = None,
    ) -> dict[str, Any]:
        """Return server-owned axis options without exposing a client cartesian product."""
        resolved = self.resolve_with_policy(
            scope_id, project_id=project_id, model_id=model_id, model_version=model_version
        )
        candidates = resolved["candidates"]
        candidates = self._visible_candidates(candidates)
        labels = {
            "column": ("Column", None),
            "stationary_phase": ("Stationary phase", None),
            "mode": ("Mode", None),
            "length_mm": ("Length", "mm"),
            "inner_diameter_mm": ("Inner diameter", "mm"),
            "particle_size_um": ("Particle size", "um"),
            "column_temperature_c": ("Column temperature", "C"),
            "flow_rate_ml_min": ("Flow rate", "mL/min"),
        }
        values: dict[str, set[Any]] = {key: set() for key in labels}
        for candidate in candidates:
            for key, value in candidate.as_conditions().items():
                values[key].add(value)
        fields = [
            {
                "key": key,
                "label": labels[key][0],
                "unit": labels[key][1],
                "values": sorted(values[key], key=lambda item: (str(type(item)), item)),
            }
            for key in labels
        ]
        policy = resolved["policy"]
        scope_provenance = None
        if policy is not None and policy.get("expansion_mode") == COUPLED_DISCRETE_CONDITIONS:
            scope_provenance = {
                "scope_id": resolved["scope_id"],
                "policy": policy,
                "candidate_count": resolved["candidate_count"],
                "observed_count": resolved["observed_count"],
                "unobserved_count": resolved["unobserved_count"],
            }
        return {
            "scope_id": scope_id,
            "candidate_count": len(candidates),
            "max_candidates": self._scope_limit(scope_id),
            "supported_expansion_modes": self.supported_expansion_modes,
            "scope_provenance": scope_provenance,
            "fields": fields,
        }

    def options_for_model(self, domain: SfcModelDomain) -> dict[str, Any]:
        """Aggregate observed axes for one trusted model without creating a scope."""
        if not isinstance(domain, SfcModelDomain):
            raise TypeError("SFC options require a server-resolved model domain")
        if not domain.model_id.strip() or not domain.model_version.strip():
            raise ValueError("SFC model binding is invalid")
        if domain.scope == "single_column":
            if (
                not domain.allowed_columns
                or not domain.stationary_phase
                or not domain.mode
                or any(not column.strip() for column in domain.allowed_columns)
            ):
                raise SfcAssetUnavailable("MODEL_METADATA_INVALID")
            candidates = tuple(
                candidate for candidate in self._candidates
                if candidate.column in domain.allowed_columns
                and candidate.stationary_phase == domain.stationary_phase
                and candidate.mode == domain.mode
            )
        elif domain.scope == "all_column":
            candidates = self._distinct_observed_candidates(self._candidates)
        else:
            raise SfcAssetUnavailable("MODEL_SCOPE_UNSUPPORTED")
        if not candidates:
            raise SfcAssetUnavailable("MODEL_CANDIDATE_DOMAIN_EMPTY")

        labels = {
            "column": ("Column", None),
            "stationary_phase": ("Stationary phase", None),
            "mode": ("Mode", None),
            "length_mm": ("Length", "mm"),
            "inner_diameter_mm": ("Inner diameter", "mm"),
            "particle_size_um": ("Particle size", "um"),
            "column_temperature_c": ("Column temperature", "C"),
            "flow_rate_ml_min": ("Flow rate", "mL/min"),
        }
        values: dict[str, set[Any]] = {key: set() for key in labels}
        for candidate in candidates:
            for key, value in candidate.as_conditions().items():
                values[key].add(value)
        fields = [
            {
                "key": key,
                "label": labels[key][0],
                "unit": labels[key][1],
                "values": sorted(values[key], key=lambda item: (str(type(item)), item)),
            }
            for key in labels
        ]
        return {
            "scope_id": None,
            "scope_provenance": None,
            "model_id": domain.model_id,
            "model_version": domain.model_version,
            "candidate_count": len(candidates),
            "max_candidates": self.max_candidates,
            "supported_expansion_modes": self.supported_expansion_modes,
            "fields": fields,
        }


@dataclass(frozen=True)
class _ModelEntry:
    model_id: str
    version: str
    artifact_sha256: str
    feature_names: tuple[str, ...]
    target: str | None
    artifact_path: str | None
    scope: str | None
    stationary_phase: str | None
    mode: str | None
    allowed_columns: tuple[str, ...]
    single_column_group: str | None
    single_column_slug: str | None
    threshold: str | None
    score_semantics: str


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


class SfcModelRegistry:
    """Read-only registry metadata supplied by a trusted build assembly."""

    def __init__(self, *, registry_path: str | Path, model_root: str | Path, expected_registry_sha256: str) -> None:
        self.registry_path = Path(registry_path).resolve()
        self.model_root = Path(model_root).resolve()
        expected_registry_sha256 = expected_registry_sha256.lower()
        if len(expected_registry_sha256) != 64 or any(c not in "0123456789abcdef" for c in expected_registry_sha256):
            raise SfcAssetUnavailable("MODEL_REGISTRY_HASH_INVALID")
        if not self.registry_path.is_file():
            raise SfcAssetUnavailable("MODEL_REGISTRY_MISSING")
        if not self.model_root.is_dir():
            raise SfcAssetUnavailable("MODEL_ROOT_MISSING")
        if not _inside(self.registry_path, self.model_root):
            raise SfcAssetUnavailable("MODEL_REGISTRY_OUTSIDE_ROOT")
        if _sha256(self.registry_path) != expected_registry_sha256:
            raise SfcAssetUnavailable("MODEL_REGISTRY_HASH_MISMATCH")
        try:
            payload = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SfcAssetUnavailable("MODEL_REGISTRY_INVALID") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
            raise SfcAssetUnavailable("MODEL_REGISTRY_INVALID")
        self._payload = payload
        self._entries: dict[str, _ModelEntry] = {}
        for raw in payload["models"]:
            if not isinstance(raw, dict):
                raise SfcAssetUnavailable("MODEL_REGISTRY_INVALID")
            model_id = raw.get("id")
            digest = raw.get("model_sha256") or raw.get("artifact_sha256")
            names = raw.get("feature_names")
            scope = raw.get("scope")
            if not isinstance(model_id, str) or not model_id.strip() or model_id in self._entries:
                raise SfcAssetUnavailable("MODEL_REGISTRY_ENTRY_INVALID")
            if not isinstance(digest, str) or not isinstance(names, list) or not isinstance(scope, str):
                raise SfcAssetUnavailable("MODEL_REGISTRY_ENTRY_INVALID")
            if scope not in _SCOPE_TO_TOOL:
                raise SfcAssetUnavailable("MODEL_SCOPE_UNSUPPORTED")
            if len(names) != REQUIRED_FEATURE_COUNT or len(set(names)) != len(names) or not all(isinstance(name, str) and name for name in names):
                raise SfcAssetUnavailable("MODEL_FEATURE_SCHEMA_INVALID")
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest.lower()):
                raise SfcAssetUnavailable("MODEL_HASH_INVALID")
            artifact_path = self._artifact_path(raw.get("artifact_uri") or raw.get("artifact_path"), model_id)
            metadata_path = artifact_path.parent / "metadata.json"
            if not metadata_path.is_file():
                raise SfcAssetUnavailable("MODEL_METADATA_MISSING")
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise SfcAssetUnavailable("MODEL_METADATA_INVALID") from exc
            if not isinstance(metadata, dict):
                raise SfcAssetUnavailable("MODEL_METADATA_INVALID")
            if (
                metadata.get("id") != model_id
                or metadata.get("scope") != scope
                or metadata.get("available") is not True
                or str(metadata.get("model_sha256", "")).lower() != digest.lower()
                or metadata.get("feature_names") != names
            ):
                raise SfcAssetUnavailable("MODEL_METADATA_MISMATCH")
            allowed_columns = metadata.get("allowed_columns", [])
            if scope == "single_column" and (
                not isinstance(allowed_columns, list)
                or not allowed_columns
                or not all(isinstance(column, str) and column.strip() for column in allowed_columns)
                or not isinstance(metadata.get("stationary_phase"), str)
                or not isinstance(metadata.get("mode"), str)
                or not isinstance(metadata.get("single_column_group"), str)
                or not metadata.get("single_column_group", "").strip()
                or not isinstance(metadata.get("single_column_slug"), str)
                or not metadata.get("single_column_slug", "").strip()
            ):
                raise SfcAssetUnavailable("MODEL_METADATA_INVALID")
            if scope == "all_column":
                allowed_columns = []
            self._entries[model_id] = _ModelEntry(
                model_id=model_id,
                version=str(raw.get("version", raw.get("model_version", "1"))),
                artifact_sha256=digest.lower(),
                feature_names=tuple(str(item) for item in names),
                target=str(metadata.get("target")) if metadata.get("target") is not None else None,
                artifact_path=raw.get("artifact_uri") or raw.get("artifact_path"),
                scope=scope,
                stationary_phase=metadata.get("stationary_phase"),
                mode=metadata.get("mode"),
                allowed_columns=tuple(allowed_columns),
                single_column_group=metadata.get("single_column_group"),
                single_column_slug=metadata.get("single_column_slug"),
                threshold=str(metadata.get("threshold")) if metadata.get("threshold") is not None else None,
                score_semantics="positive_class_probability",
            )
        champions = payload.get("champions", {})
        if not isinstance(champions, dict):
            raise SfcAssetUnavailable("MODEL_REGISTRY_INVALID")
        self._champions = dict(champions)

    def _artifact_path(self, raw: str | None, model_id: str) -> Path:
        path = Path(raw) if raw else self.model_root / model_id / "model.joblib"
        if raw and not path.is_absolute():
            path = self.model_root / path
        path = path.resolve()
        if not _inside(path, self.model_root) or not path.is_file():
            raise SfcAssetUnavailable("MODEL_ARTIFACT_UNAVAILABLE")
        return path

    @property
    def entries(self) -> tuple[_ModelEntry, ...]:
        return tuple(self._entries.values())

    def entry(self, model_id: str, *, version: str | None = None) -> _ModelEntry:
        entry = self._entries.get(model_id)
        if entry is None or (version is not None and entry.version != version):
            raise SfcAssetUnavailable("MODEL_REGISTRY_ENTRY_MISSING")
        return entry

    def resolve_champion(self, scope_id: str) -> _ModelEntry:
        model_id = self._champions.get(scope_id)
        if model_id is None:
            raise SfcAssetUnavailable("MODEL_CHAMPION_UNAVAILABLE")
        return self.entry(str(model_id))

    def artifact_for(self, entry: _ModelEntry) -> Path:
        return self._artifact_path(entry.artifact_path, entry.model_id)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_sfc_model(registry: SfcModelRegistry, model_id: str, *, model_version: str | None = None) -> tuple[Any, _ModelEntry]:
    """Verify the registered artifact before the only permitted joblib load."""
    entry = registry.entry(model_id, version=model_version)
    artifact = registry.artifact_for(entry)
    if _sha256(artifact) != entry.artifact_sha256:
        raise SfcAssetUnavailable("MODEL_ARTIFACT_HASH_MISMATCH")
    try:
        import joblib
        model = joblib.load(artifact)
    except Exception as exc:
        raise SfcAssetUnavailable("MODEL_ARTIFACT_LOAD_FAILED") from exc
    feature_names = tuple(str(item) for item in getattr(model, "feature_names_in_", ()))
    if feature_names != entry.feature_names or getattr(model, "n_features_in_", None) != REQUIRED_FEATURE_COUNT:
        raise SfcAssetUnavailable("MODEL_FEATURE_SCHEMA_MISMATCH")
    if not callable(getattr(model, "predict_proba", None)):
        raise SfcAssetUnavailable("MODEL_API_UNSUPPORTED")
    return model, entry


# Adapted scientific functions; names and equations follow reviewed inference.py.
def safe_float(value: Any) -> float:
    try:
        number = float(value)
        # Preserve +/-Inf so the runtime can reject it before model inference.
        # Only conversion failures become NaN (which is separately policy-checked).
        return number
    except Exception:
        return float("nan")


def canonicalize(smiles: str) -> str:
    try:
        from rdkit import Chem
    except ImportError as exc:
        raise SfcAssetUnavailable("RDKit_UNAVAILABLE") from exc
    mol = Chem.MolFromSmiles(str(smiles).strip())
    if mol is None:
        raise ValueError(f"无法解析 SMILES: {smiles}")
    return Chem.MolToSmiles(mol, isomericSmiles=True)


def _mordred_value(result: Any, name: str) -> float:
    try:
        return safe_float(result[name])
    except Exception:
        return float("nan")


@lru_cache(maxsize=4096)
def molecular_descriptors(smiles: str) -> dict[str, float]:
    try:
        import numpy as np
        from mordred import Calculator, descriptors
        from rdkit import Chem
        from rdkit.Chem import Crippen, Descriptors, Lipinski, rdMolDescriptors
    except ImportError as exc:
        raise SfcAssetUnavailable("SCIENTIFIC_DEPENDENCIES_UNAVAILABLE") from exc
    if str(smiles).strip().lower() in {"", "none", "nan"}:
        return {"mw": 0.0, "tpsa": 0.0, "nrotbs": 0.0, "hba": 0.0, "hbd": 0.0,
                "logp": 0.0, "rpsa": 0.0, "rasa": 1.0, "mdec": 0.0, "mats": 0.0}
    mol = Chem.MolFromSmiles(canonicalize(smiles))
    result = Calculator(descriptors, ignore_3D=True)(mol)
    tpsa = safe_float(rdMolDescriptors.CalcTPSA(mol))
    asa = safe_float(rdMolDescriptors.CalcLabuteASA(mol))
    rpsa = math.inf if math.isinf(tpsa) or math.isinf(asa) else (
        tpsa / asa if np.isfinite(asa) and asa > 0 else np.nan
    )
    return {"mw": safe_float(Descriptors.MolWt(mol)), "tpsa": tpsa,
            "nrotbs": safe_float(Lipinski.NumRotatableBonds(mol)),
            "hba": safe_float(Lipinski.NumHAcceptors(mol)), "hbd": safe_float(Lipinski.NumHDonors(mol)),
            "logp": safe_float(Crippen.MolLogP(mol)), "rpsa": rpsa,
            "rasa": 1.0 - rpsa if np.isfinite(rpsa) else np.nan,
            "mdec": _mordred_value(result, "MDEC-11"), "mats": _mordred_value(result, "MATS1c")}


def pair_features(smiles_1: str, smiles_2: str) -> dict[str, float]:
    import numpy as np
    first, second = molecular_descriptors(smiles_1), molecular_descriptors(smiles_2)
    output: dict[str, float] = {}
    for base in ANALYTE_BASES:
        key = base.replace("_smiles", "")
        values = [first[key], second[key]]
        if any(math.isinf(value) for value in values):
            for suffix in ("pair_value", "mean", "median", "abs_diff", "range"):
                output[f"{base}_{suffix}"] = math.inf
            continue
        valid = [value for value in values if np.isfinite(value)]
        median = float(np.median(valid)) if valid else np.nan
        value_range = float(np.max(valid) - np.min(valid)) if valid else np.nan
        output[f"{base}_pair_value"] = median if value_range <= 1e-8 else np.nan
        output[f"{base}_mean"] = float(np.mean(valid)) if valid else np.nan
        output[f"{base}_median"] = median
        output[f"{base}_abs_diff"] = float(abs(values[0] - values[1])) if all(np.isfinite(v) for v in values) else np.nan
        output[f"{base}_range"] = value_range
    return output


def modifier_features(modifiers: Sequence[tuple[str, float]], modifier_total_fraction: float) -> dict[str, float]:
    """Build weighted modifier descriptors without changing caller fractions."""
    import numpy as np
    if not modifiers:
        output = dict(zip(MODIFIER_FEATURE_COLUMNS, [0.0] * len(MODIFIER_FEATURE_COLUMNS)))
        output["rasa_modi"] = 1.0
        output["modifier_ratio"] = float(modifier_total_fraction)
        return output
    total = sum(float(fraction) for _, fraction in modifiers)
    if total <= 0 or not math.isclose(total, 1.0, abs_tol=1e-6):
        raise ValueError("modifier fractions must sum to 1")
    weighted = dict(zip(MODIFIER_FEATURE_COLUMNS, [0.0] * len(MODIFIER_FEATURE_COLUMNS)))
    keys = dict(zip(MODIFIER_FEATURE_COLUMNS, ["mw", "tpsa", "nrotbs", "hba", "hbd", "logp", "rpsa", "rasa", "mdec", "mats"]))
    for smiles, fraction in modifiers:
        values = molecular_descriptors(smiles)
        for column, key in keys.items():
            weighted[column] += float(values[key]) * float(fraction)
    weighted["modifier_ratio"] = float(modifier_total_fraction)
    return weighted


def candidate_frame(candidates: Sequence[SfcCandidate], smiles_1: str, smiles_2: str, modifiers: Sequence[tuple[str, float]], modifier_total_fraction: float):
    try:
        import pandas as pd
    except ImportError as exc:
        raise SfcAssetUnavailable("PANDAS_UNAVAILABLE") from exc
    if not candidates:
        raise ValueError("candidate scope is empty")
    if len(candidates) > MAX_CANDIDATES:
        raise ValueError(f"candidate count exceeds {MAX_CANDIDATES}")
    modifier = modifier_features(modifiers, modifier_total_fraction)
    pair = pair_features(smiles_1, smiles_2)
    rows = []
    for candidate in candidates:
        row = candidate.as_features()
        row.update(modifier)
        row.update(pair)
        rows.append(row)
    return pd.DataFrame(rows)


class SfcRuntime:
    def __init__(self, *, registry: SfcModelRegistry, bound_model_id: str, bound_model_version: str, bound_artifact_sha256: str, business_scope_id: str, structure_resolver: Mapping[str, str] | Callable[[str], str], candidate_resolver: Mapping[str, Sequence[SfcCandidate | Mapping[str, Any]]] | Callable[[str], Sequence[SfcCandidate | Mapping[str, Any]]]):
        if not isinstance(business_scope_id, str) or not business_scope_id.strip():
            raise ValueError("business_scope_id is required")
        self.registry = registry
        self.bound_model_id = bound_model_id
        self.bound_model_version = bound_model_version
        self.bound_artifact_sha256 = bound_artifact_sha256.lower()
        self.business_scope_id = business_scope_id
        entry = registry.entry(bound_model_id, version=bound_model_version)
        self.allowed_tool = _SCOPE_TO_TOOL.get(str(entry.scope))
        if self.allowed_tool is None:
            raise SfcAssetUnavailable("MODEL_SCOPE_UNSUPPORTED")
        if entry.artifact_sha256 != self.bound_artifact_sha256:
            raise SfcAssetUnavailable("MODEL_BINDING_HASH_MISMATCH")
        self.structure_resolver = structure_resolver
        self.candidate_resolver = candidate_resolver
        self._loaded_model: Any | None = None
        self._loaded_entry: _ModelEntry | None = None
        self._load_lock = Lock()

    def _load_bound_model(self) -> tuple[Any, _ModelEntry]:
        """Load this exact runtime binding once, including concurrent first use."""
        if self._loaded_model is not None and self._loaded_entry is not None:
            return self._loaded_model, self._loaded_entry
        with self._load_lock:
            if self._loaded_model is None or self._loaded_entry is None:
                model, entry = load_sfc_model(
                    self.registry, self.bound_model_id, model_version=self.bound_model_version
                )
                if entry.artifact_sha256 != self.bound_artifact_sha256:
                    raise SfcAssetUnavailable("MODEL_BINDING_HASH_MISMATCH")
                self._loaded_model, self._loaded_entry = model, entry
            return self._loaded_model, self._loaded_entry

    def _resolve(self, resolver: Any, key: str) -> Any:
        try:
            value = resolver(key) if callable(resolver) else resolver[key]
        except (KeyError, TypeError) as exc:
            raise ValueError(f"trusted resolver has no value for {key}") from exc
        if value is None:
            raise ValueError(f"trusted resolver has no value for {key}")
        return value

    def _resolve_candidates(self, request: Any) -> Any:
        resolver = self.candidate_resolver
        key = request.input.candidate_scope_id
        metadata = {
            "project_id": getattr(request, "project_id", None),
            "model_id": self.bound_model_id,
            "model_version": self.bound_model_version,
        }
        if not callable(resolver):
            try:
                return resolver[key]
            except (KeyError, TypeError) as exc:
                raise SfcRequestValidationError(
                    "SFC candidate scope is unknown or not bound to this request"
                ) from exc
        # Select the supported resolver shape before invoking it.  A TypeError
        # raised by the trusted dependency must not be reclassified as 422.
        import inspect
        try:
            parameters = inspect.signature(resolver).parameters
        except (TypeError, ValueError):
            parameters = {}
        accepts_metadata = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            or (name in metadata and parameter.kind is not inspect.Parameter.POSITIONAL_ONLY)
            for name, parameter in parameters.items()
        )
        if accepts_metadata:
            try:
                return resolver(key, **metadata)
            except SfcCandidateScopeError as exc:
                raise SfcRequestValidationError(
                    "SFC candidate scope is unknown or not bound to this request"
                ) from exc
        try:
            return resolver(key)
        except SfcCandidateScopeError as exc:
            raise SfcRequestValidationError(
                "SFC candidate scope is unknown or not bound to this request"
            ) from exc

    def _resolve_scope(self, request: Any) -> tuple[Sequence[Any], dict[str, Any] | None]:
        resolver = self.candidate_resolver
        catalog = getattr(resolver, "__self__", None)
        if isinstance(catalog, SfcCandidateCatalog):
            try:
                resolved = catalog.resolve_with_policy(
                    request.input.candidate_scope_id,
                    project_id=getattr(request, "project_id", None),
                    model_id=self.bound_model_id,
                    model_version=self.bound_model_version,
                )
                return resolved["candidates"], resolved
            except SfcCandidateScopeError as exc:
                raise SfcRequestValidationError(
                    "SFC candidate scope is unknown or not bound to this request"
                ) from exc
        return self._resolve_candidates(request), None

    @staticmethod
    def _candidate(value: SfcCandidate | Mapping[str, Any], index: int) -> SfcCandidate:
        if isinstance(value, SfcCandidate):
            if any(not math.isfinite(float(item)) for item in (
                value.length_mm, value.inner_diameter_mm, value.particle_size_um,
                value.column_temperature, value.flow_rate,
            )):
                raise ValueError("candidate contains a non-finite condition")
            return value
        if not isinstance(value, Mapping):
            raise ValueError("candidate resolver returned an invalid candidate")
        required = ("column", "stationary_phase", "mode", "length_mm", "inner_diameter_mm", "particle_size_um", "column_temperature", "flow_rate")
        if any(key not in value for key in required):
            raise ValueError("candidate resolver omitted a required condition")
        numeric = {key: float(value[key]) for key in required[3:]}
        if any(not math.isfinite(number) for number in numeric.values()):
            raise ValueError("candidate contains a non-finite condition")
        return SfcCandidate(
            candidate_id=str(value.get("candidate_id", f"candidate-{index + 1}")),
            column=str(value["column"]), stationary_phase=str(value["stationary_phase"]), mode=str(value["mode"]),
            **numeric,
            observed_in_source=value.get("observed_in_source", True),
        )

    def _validate_domain(
        self,
        request: Any,
        *,
        entry: _ModelEntry,
        candidates: Sequence[SfcCandidate],
        modifiers: Sequence[tuple[str, float]],
        invalid_error: type[Exception] = SfcAssetUnavailable,
    ) -> None:
        total_fraction = float(request.input.modifier_total_fraction)
        if not math.isfinite(total_fraction):
            raise invalid_error("SFC_INPUT_NONFINITE")
        if any(not math.isfinite(float(fraction)) for _, fraction in modifiers):
            raise invalid_error("SFC_INPUT_NONFINITE")

    def preflight(
        self,
        request: Any,
        *,
        authorized_modifier_structures: Mapping[str, str] | None = None,
    ) -> None:
        """Check persisted scope and pinned inputs before atomic creation."""
        if authorized_modifier_structures is None:
            raise SfcAssetUnavailable("SFC_MODIFIER_SNAPSHOT_MISSING")
        entry = self.registry.entry(self.bound_model_id, version=self.bound_model_version)
        raw_candidates = self._resolve_candidates(request)
        candidates = [self._candidate(item, index) for index, item in enumerate(raw_candidates)]
        if self.allowed_tool == "single_column":
            if request.input.column_id not in entry.allowed_columns:
                raise SfcRequestValidationError("SFC column is outside the bound model domain")
            candidates = [
                item for item in candidates
                if item.column == request.input.column_id
                and item.stationary_phase == entry.stationary_phase
                and item.mode == entry.mode
            ]
        if not candidates:
            raise SfcRequestValidationError("SFC candidate scope is empty for this model")
        modifiers: list[tuple[str, float]] = []
        for item in request.input.modifier_components:
            try:
                smiles = authorized_modifier_structures[item.structure_ref]
            except KeyError as exc:
                raise SfcAssetUnavailable("SFC_MODIFIER_SNAPSHOT_MISSING") from exc
            if smiles is None:
                raise SfcAssetUnavailable("SFC_MODIFIER_SNAPSHOT_MISSING")
            modifiers.append((str(smiles), float(item.fraction)))
        self._validate_domain(
            request,
            entry=entry,
            candidates=candidates,
            modifiers=modifiers,
            invalid_error=SfcRequestValidationError,
        )

    def predict(
        self,
        request: Any,
        *,
        normalized_structures: Sequence[str] | None = None,
        normalized_modifier_structures: Mapping[str, str] | None = None,
        require_snapshots: bool = False,
        return_scope_provenance: bool = False,
    ) -> (
        tuple[list[dict[str, Any]], list[str], _ModelEntry]
        | tuple[list[dict[str, Any]], list[str], _ModelEntry, dict[str, Any] | None, int]
    ):
        selection = request.model_selection
        if request.tool != self.allowed_tool:
            raise SfcAssetUnavailable("MODEL_TOOL_SCOPE_MISMATCH")
        if selection.mode == "exact" and (
            selection.model_id != self.bound_model_id or selection.model_version != self.bound_model_version
        ):
            raise SfcAssetUnavailable("MODEL_SELECTION_MISMATCH")
        if selection.mode == "champion" and selection.scope_id != self.business_scope_id:
            raise SfcAssetUnavailable("MODEL_CHAMPION_SCOPE_MISMATCH")
        # Champion resolution is an assembly concern.  This runtime is bound
        # to the model snapshot used to create the job and never re-resolves a
        # mutable registry while executing it.
        model, entry = self._load_bound_model()
        if require_snapshots and (
            normalized_structures is None
            or len(normalized_structures) != len(request.structure_refs)
            or normalized_modifier_structures is None
        ):
            raise SfcAssetUnavailable("SFC_INPUT_SNAPSHOT_MISSING")
        structures = (
            [str(value) for value in normalized_structures]
            if normalized_structures is not None
            else [self._resolve(self.structure_resolver, ref) for ref in request.structure_refs]
        )
        raw_candidates, scope = self._resolve_scope(request)
        candidates = [self._candidate(item, index) for index, item in enumerate(raw_candidates)]
        if self.allowed_tool == "single_column":
            if request.input.column_id not in entry.allowed_columns:
                raise SfcAssetUnavailable("MODEL_COLUMN_SCOPE_MISMATCH")
            candidates = [
                item for item in candidates
                if item.column == request.input.column_id
                and item.stationary_phase == entry.stationary_phase
                and item.mode == entry.mode
            ]
        if not candidates:
            raise ValueError("candidate scope has no candidates for this tool")
        if normalized_modifier_structures is not None:
            try:
                modifiers = [
                    (str(normalized_modifier_structures[item.structure_ref]), float(item.fraction))
                    for item in request.input.modifier_components
                ]
            except KeyError as exc:
                raise SfcAssetUnavailable("SFC_INPUT_SNAPSHOT_MISSING") from exc
        elif require_snapshots:
            raise SfcAssetUnavailable("SFC_INPUT_SNAPSHOT_MISSING")
        else:
            modifiers = [
                (self._resolve(self.structure_resolver, item.structure_ref), float(item.fraction))
                for item in request.input.modifier_components
            ]
        self._validate_domain(request, entry=entry, candidates=candidates, modifiers=modifiers)
        frame = candidate_frame(candidates, structures[0], structures[1], modifiers, request.input.modifier_total_fraction)
        names = list(entry.feature_names)
        missing = [name for name in names if name not in frame.columns]
        if missing:
            raise SfcAssetUnavailable(f"MODEL_FEATURE_COLUMNS_MISMATCH missing={missing}")
        try:
            import numpy as np
            numeric_names = [name for name in names if name not in _CATEGORICAL_FEATURES]
            numeric = frame[numeric_names].to_numpy(dtype=float)
            if np.isinf(numeric).any():
                raise SfcAssetUnavailable("MODEL_FEATURE_INFINITE")
            nan_columns = [
                name for index, name in enumerate(numeric_names)
                if np.isnan(numeric[:, index]).any()
            ]
            unexpected_nan = sorted(set(nan_columns) - _ALLOWED_NAN_FEATURES)
            if unexpected_nan:
                raise SfcAssetUnavailable(f"MODEL_FEATURE_NAN_UNEXPECTED features={unexpected_nan}")
            warning_messages = [f"FEATURE_VALUE_MISSING:{name}" for name in nan_columns]
            probabilities = model.predict_proba(frame[names])
            classes = list(getattr(model, "classes_", (0, 1)))
            positive = classes.index(1) if 1 in classes else len(classes) - 1
            probabilities = np.asarray(probabilities, dtype=float)
        except SfcAssetUnavailable:
            raise
        except Exception as exc:
            raise SfcAssetUnavailable("MODEL_INFERENCE_FAILED") from exc
        if (
            probabilities.ndim != 2
            or probabilities.shape[0] != len(candidates)
            or probabilities.shape[1] != len(classes)
            or classes != [0, 1]
            or positive < 0
            or positive >= probabilities.shape[1]
            or not np.all(np.isfinite(probabilities))
            or np.any(probabilities < 0.0)
            or np.any(probabilities > 1.0)
        ):
            raise SfcAssetUnavailable("MODEL_OUTPUT_INVALID")
        scores = [float(np.round(score, 15)) for score in probabilities[:, positive].tolist()]
        if any(not math.isfinite(score) or score < 0.0 or score > 1.0 for score in scores):
            raise SfcAssetUnavailable("MODEL_OUTPUT_INVALID")
        ranked = sorted(zip(candidates, scores), key=lambda item: item[1], reverse=True)
        rows = [
            {
                "candidate_id": candidate.candidate_id,
                "rank": rank,
                "separation": score,
                "conditions": {
                    **candidate.as_conditions(),
                    **(
                        {"observed_in_source": candidate.observed_in_source}
                        if scope is not None and scope.get("policy") is not None else {}
                    ),
                },
                "score_semantics": entry.score_semantics,
            }
            for rank, (candidate, score) in enumerate(ranked[: request.input.top_k], 1)
        ]
        if scope is not None and scope.get("policy") is not None:
            scope = {
                "scope_id": scope["scope_id"],
                "policy": scope["policy"],
                "candidate_count": scope["candidate_count"],
                "observed_count": scope["observed_count"],
                "unobserved_count": scope["unobserved_count"],
            }
        else:
            scope = None
        if return_scope_provenance:
            return rows, warning_messages, entry, scope, len(candidates)
        return rows, warning_messages, entry
