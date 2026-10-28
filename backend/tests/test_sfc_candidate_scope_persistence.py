from __future__ import annotations

import sqlite3
import hashlib
import json
from pathlib import Path

import pytest

from chrom_platform.adapters.sfc.runtime import (
    FULL_COLUMN_GOLDEN_CONDITIONS,
    MAX_CANDIDATES,
    SfcAssetUnavailable,
    SfcCandidateCatalog,
    SfcModelDomain,
)
from chrom_platform.modules.prediction.schemas import SfcCandidateScopeView


def _catalog(tmp_path: Path) -> tuple[Path, SfcCandidateCatalog]:
    source = tmp_path / "candidates.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.execute(
            """
            CREATE TABLE result_with_advanced_descriptors (
                column_2 TEXT, stationary_phase TEXT, mode TEXT, length_mm REAL,
                inner_diameter_mm REAL, particle_size_um REAL,
                column_temperature REAL, flow_rate REAL
            )
            """
        )
        connection.executemany(
            "INSERT INTO result_with_advanced_descriptors VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("C1", "P1", "coated", 150.0, 4.6, 5.0, 35.0, 2.0),
                ("C1", "P1", "coated", 250.0, 4.6, 5.0, 35.0, 2.0),
            ],
        )
    return source, SfcCandidateCatalog(source)


def test_scope_survives_new_catalog_instance_and_keeps_binding(tmp_path: Path) -> None:
    source, catalog = _catalog(tmp_path)
    created = catalog.create_scope(
        {"column": ["C1"], "stationary_phase": ["P1"]},
        project_id="project-a",
        model_id="model-a",
        model_version="7",
        max_candidates=3,
    )
    assert created["project_id"] == "project-a"
    assert created["model_id"] == "model-a"
    assert created["model_version"] == "7"
    assert created["candidate_count"] == 2
    assert created["max_candidates"] == 3
    SfcCandidateScopeView.model_validate(created)
    repeated = catalog.create_scope(
        {"column": ["C1"], "stationary_phase": ["P1"]},
        project_id="project-a",
        model_id="model-a",
        model_version="7",
        max_candidates=4,
    )
    assert repeated["id"] == created["id"]
    assert repeated["max_candidates"] == 3
    with pytest.raises(ValueError, match="exceeds the limit"):
        catalog.create_scope(
            {"column": ["C1"], "stationary_phase": ["P1"]},
            project_id="project-a",
            model_id="model-a",
            model_version="7",
            max_candidates=1,
        )
    restarted = SfcCandidateCatalog(source)
    candidates = restarted.resolve(
        created["id"], project_id="project-a", model_id="model-a", model_version="7"
    )
    assert len(candidates) == 2
    assert restarted.describe(
        created["id"], project_id="project-a", model_id="model-a", model_version="7"
    )["max_candidates"] == 3
    assert restarted.options(
        created["id"], project_id="project-a", model_id="model-a", model_version="7"
    )["max_candidates"] == 3
    assert candidates[0].candidate_id.startswith(created["id"])
    assert [item.as_conditions() for item in candidates] == created["conditions"]


@pytest.mark.parametrize("stored_limit", [1, MAX_CANDIDATES + 1, "invalid"])
def test_corrupt_persisted_scope_limit_fails_closed_without_repair(tmp_path: Path, stored_limit: int | str) -> None:
    source, catalog = _catalog(tmp_path)
    created = catalog.create_scope(
        {"column": ["C1"], "stationary_phase": ["P1"]},
        project_id="project-a", model_id="model-a", model_version="1", max_candidates=3,
    )
    with sqlite3.connect(catalog.scope_storage_path) as connection:
        connection.execute(
            "UPDATE sfc_candidate_scope SET max_candidates = ? WHERE scope_id = ?",
            (stored_limit, created["id"]),
        )

    restarted = SfcCandidateCatalog(source, scope_storage_path=catalog.scope_storage_path)
    with pytest.raises(SfcAssetUnavailable, match="CANDIDATE_SCOPE_STORE_INVALID"):
        restarted.resolve(created["id"], project_id="project-a", model_id="model-a", model_version="1")
    with sqlite3.connect(catalog.scope_storage_path) as connection:
        persisted = connection.execute(
            "SELECT max_candidates FROM sfc_candidate_scope WHERE scope_id = ?",
            (created["id"],),
        ).fetchone()[0]
    assert persisted == stored_limit


def test_full_column_default_and_persisted_scope_resolve_observed_tuples(tmp_path: Path) -> None:
    source = tmp_path / "candidates.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.execute(
            """
            CREATE TABLE result_with_advanced_descriptors (
                column_2 TEXT, stationary_phase TEXT, mode TEXT, length_mm REAL,
                inner_diameter_mm REAL, particle_size_um REAL,
                column_temperature REAL, flow_rate REAL
            )
            """
        )
        connection.executemany(
            "INSERT INTO result_with_advanced_descriptors VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (" AD ", FULL_COLUMN_GOLDEN_CONDITIONS["stationary_phase"], "coated", 150.0, 4.6, 5.0, 35.0, 2.5),
                ("AD", FULL_COLUMN_GOLDEN_CONDITIONS["stationary_phase"], "coated", 150.0, 4.6, 5.0, 35.0, 2.5),
                ("AMY 1", FULL_COLUMN_GOLDEN_CONDITIONS["stationary_phase"], "coated", 250.0, 4.6, 5.0, 35.0, 2.0),
            ],
        )
    scope_store = tmp_path / "scopes.sqlite3"
    catalog = SfcCandidateCatalog(source, scope_storage_path=scope_store)
    catalog.bind_model_scope(model_id="model-a", model_version="1", scope="all_column")

    default = catalog.resolve(catalog.DEFAULT_SCOPE_ID)
    assert len(default) == 2
    assert default[0].column == "AD"
    assert default[0].candidate_id == "sfc-candidate-0001"
    assert default[1].column == "AMY 1"
    options = catalog.options_for_model(SfcModelDomain(
        model_id="model-a", model_version="1", scope="all_column",
    ))
    assert options["candidate_count"] == 2
    assert {item["key"]: item["values"] for item in options["fields"]}["flow_rate_ml_min"] == [2.0, 2.5]

    created = catalog.create_scope(
        {
            "column": ["AD", "AMY 1"],
            "stationary_phase": [FULL_COLUMN_GOLDEN_CONDITIONS["stationary_phase"]],
            "mode": ["coated"],
            "length_mm": [150.0, 250.0],
            "inner_diameter_mm": [4.6],
            "particle_size_um": [5.0],
            "column_temperature_c": [35.0],
            "flow_rate_ml_min": [2.0, 2.5],
        },
        project_id="project-a", model_id="model-a", model_version="1",
    )
    restarted = SfcCandidateCatalog(source, scope_storage_path=scope_store)
    restarted.bind_model_scope(model_id="model-a", model_version="1", scope="all_column")
    assert [item.candidate_id for item in restarted.resolve(catalog.DEFAULT_SCOPE_ID)] == [
        item.candidate_id for item in default
    ]
    persisted = restarted.resolve(
        created["id"], project_id="project-a", model_id="model-a", model_version="1"
    )
    assert len(persisted) == 2
    assert [item.as_conditions() for item in persisted] == created["conditions"]
    assert [item.candidate_id for item in persisted] == [
        f"{created['id']}-{index:04d}" for index in (1, 2)
    ]


def test_scope_rejects_cross_project_or_model_binding(tmp_path: Path) -> None:
    source, catalog = _catalog(tmp_path)
    catalog.bind_model_scope(model_id="model-a", model_version="1", scope="all_column")
    created = catalog.create_scope(
        {"column": ["C1"]}, project_id="project-a", model_id="model-a", model_version="1"
    )
    restarted = SfcCandidateCatalog(source)
    for binding in (
        {"project_id": "project-b", "model_id": "model-a", "model_version": "1"},
        {"project_id": "project-a", "model_id": "model-b", "model_version": "1"},
        {"project_id": "project-a", "model_id": "model-a", "model_version": "2"},
    ):
        with pytest.raises(KeyError):
            restarted.resolve(created["id"], **binding)


def test_unknown_scope_does_not_fall_back_to_default(tmp_path: Path) -> None:
    _, catalog = _catalog(tmp_path)
    with pytest.raises(KeyError):
        catalog.resolve("missing-scope", project_id="project-a", model_id="model-a", model_version="1")


def test_full_column_scope_selects_observed_tuples_without_cartesian_expansion(tmp_path: Path) -> None:
    source = tmp_path / "observed.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.execute(
            """
            CREATE TABLE result_with_advanced_descriptors (
                column_2 TEXT, stationary_phase TEXT, mode TEXT, length_mm REAL,
                inner_diameter_mm REAL, particle_size_um REAL,
                column_temperature REAL, flow_rate REAL
            )
            """
        )
        connection.executemany(
            "INSERT INTO result_with_advanced_descriptors VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("C1", "P1", "coated", 150.0, 4.6, 5.0, 35.0, 1.0),
                ("C1", "P1", "coated", 250.0, 4.6, 5.0, 35.0, 2.0),
            ],
        )
    catalog = SfcCandidateCatalog(source)
    catalog.bind_model_scope(
        model_id="model-a", model_version="1", scope="all_column"
    )
    created = catalog.create_scope(
        {
            "column": ["C1"], "stationary_phase": ["P1"], "mode": ["coated"],
            "length_mm": [150.0, 250.0], "inner_diameter_mm": [4.6],
            "particle_size_um": [5.0], "column_temperature_c": [35.0],
            "flow_rate_ml_min": [1.0, 2.0],
        },
        project_id="project-a", model_id="model-a", model_version="1",
    )
    # The two selected axes have four theoretical combinations, but only the
    # two complete source rows are included.
    assert created["candidate_count"] == 2
    assert {(row["length_mm"], row["flow_rate_ml_min"]) for row in created["conditions"]} == {
        (150.0, 1.0), (250.0, 2.0),
    }
    with pytest.raises(ValueError, match="exceeds the limit"):
        catalog.create_scope(
            {"column": ["C1"]}, project_id="project-b", model_id="model-a",
            model_version="1", max_candidates=1,
        )
    with pytest.raises(ValueError, match="empty"):
        catalog.create_scope(
            {"column": ["missing"]}, project_id="project-b", model_id="model-a",
            model_version="1",
        )
    with pytest.raises(ValueError, match="finite"):
        catalog.create_scope(
            {"flow_rate_ml_min": [float("inf")]}, project_id="project-b",
            model_id="model-a", model_version="1",
        )


@pytest.mark.parametrize("tamper", ["unknown_tuple", "duplicate_id", "duplicate_tuple", "empty", "nonfinite"])
def test_full_column_persisted_scope_tampering_fails_closed_without_repair(
    tmp_path: Path, tamper: str,
) -> None:
    source, catalog = _catalog(tmp_path)
    catalog.bind_model_scope(model_id="model-a", model_version="1", scope="all_column")
    created = catalog.create_scope(
        {"column": ["C1"], "stationary_phase": ["P1"]},
        project_id="project-a", model_id="model-a", model_version="1", max_candidates=3,
    )
    with sqlite3.connect(catalog.scope_storage_path) as connection:
        raw = connection.execute(
            "SELECT candidates_json FROM sfc_candidate_scope WHERE scope_id = ?",
            (created["id"],),
        ).fetchone()[0]
    values = json.loads(raw)
    if tamper == "unknown_tuple":
        values[0]["length_mm"] = 999.0
    elif tamper == "duplicate_id":
        values[1]["candidate_id"] = values[0]["candidate_id"]
    elif tamper == "duplicate_tuple":
        for key in ("column", "stationary_phase", "mode", "length_mm", "inner_diameter_mm", "particle_size_um", "column_temperature", "flow_rate"):
            values[1][key] = values[0][key]
    elif tamper == "empty":
        values = []
    else:
        values[0]["flow_rate"] = float("inf")
    corrupted = json.dumps(values, allow_nan=True, sort_keys=True)
    with sqlite3.connect(catalog.scope_storage_path) as connection:
        connection.execute(
            "UPDATE sfc_candidate_scope SET candidates_json = ? WHERE scope_id = ?",
            (corrupted, created["id"]),
        )

    restarted = SfcCandidateCatalog(source, scope_storage_path=catalog.scope_storage_path)
    restarted.bind_model_scope(model_id="model-a", model_version="1", scope="all_column")
    with pytest.raises(SfcAssetUnavailable, match="CANDIDATE_SCOPE_STORE_INVALID"):
        restarted.resolve(created["id"], project_id="project-a", model_id="model-a", model_version="1")
    with sqlite3.connect(catalog.scope_storage_path) as connection:
        unchanged = connection.execute(
            "SELECT candidates_json FROM sfc_candidate_scope WHERE scope_id = ?",
            (created["id"],),
        ).fetchone()[0]
    assert unchanged == corrupted


def test_candidate_scope_rejects_nonfinite_axes(tmp_path: Path) -> None:
    source, catalog = _catalog(tmp_path)
    with pytest.raises(ValueError, match="finite"):
        catalog.create_scope(
            {"length_mm": [float("inf")]},
            project_id="project-a", model_id="model-a", model_version="1",
        )


def test_candidate_source_hash_is_explicitly_verified(tmp_path: Path) -> None:
    source, catalog = _catalog(tmp_path)
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    catalog.verify_source_hash(expected)
    with pytest.raises(SfcAssetUnavailable, match="CANDIDATE_DATABASE_HASH_MISMATCH"):
        catalog.verify_source_hash("0" * 64)


def test_model_options_filter_readonly_catalog_by_trusted_domain_without_scope_write(tmp_path: Path) -> None:
    source = tmp_path / "candidates.sqlite3"
    scope_store = tmp_path / "scopes.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.execute(
            """
            CREATE TABLE result_with_advanced_descriptors (
                column_2 TEXT, stationary_phase TEXT, mode TEXT, length_mm REAL,
                inner_diameter_mm REAL, particle_size_um REAL,
                column_temperature REAL, flow_rate REAL
            )
            """
        )
        connection.executemany(
            "INSERT INTO result_with_advanced_descriptors VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("C1", "P1", "coated", 150.0, 2.1, 2.5, 25.0, 1.0),
                ("C2", "P1", "coated", 250.0, 3.0, 5.0, 40.0, 2.0),
                ("OTHER", "P1", "coated", 150.0, 30.0, 10.0, 60.0, 70.0),
                ("C1", "P2", "coated", 100.0, 4.6, 3.0, 35.0, 3.0),
                ("C1", "P1", "immobilized", 100.0, 4.6, 3.0, 35.0, 3.0),
            ],
        )
    catalog = SfcCandidateCatalog(source, scope_storage_path=scope_store)

    options = catalog.options_for_model(SfcModelDomain(
        model_id="model-a",
        model_version="1",
        scope="single_column",
        allowed_columns=("C1", "C2"),
        stationary_phase="P1",
        mode="coated",
    ))

    assert options["scope_id"] is None
    assert options["model_id"] == "model-a"
    assert options["model_version"] == "1"
    assert options["candidate_count"] == 2
    fields = {field["key"]: field for field in options["fields"]}
    assert fields["column"]["values"] == ["C1", "C2"]
    assert fields["length_mm"]["values"] == [150.0, 250.0]
    assert fields["inner_diameter_mm"]["values"] == [2.1, 3.0]
    assert fields["particle_size_um"]["values"] == [2.5, 5.0]
    assert fields["column_temperature_c"]["values"] == [25.0, 40.0]
    assert fields["flow_rate_ml_min"]["values"] == [1.0, 2.0]
    assert fields["length_mm"]["unit"] == "mm"
    assert fields["inner_diameter_mm"]["unit"] == "mm"
    assert fields["particle_size_um"]["unit"] == "um"
    assert fields["column_temperature_c"]["unit"] == "C"
    assert fields["flow_rate_ml_min"]["unit"] == "mL/min"
    assert not scope_store.exists()


def test_full_column_model_options_report_all_observed_complete_rows(tmp_path: Path) -> None:
    source = tmp_path / "candidates.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.execute(
            """
            CREATE TABLE result_with_advanced_descriptors (
                column_2 TEXT, stationary_phase TEXT, mode TEXT, length_mm REAL,
                inner_diameter_mm REAL, particle_size_um REAL,
                column_temperature REAL, flow_rate REAL
            )
            """
        )
        connection.executemany(
            "INSERT INTO result_with_advanced_descriptors VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                tuple(FULL_COLUMN_GOLDEN_CONDITIONS[key] for key in (
                    "column", "stationary_phase", "mode", "length_mm",
                    "inner_diameter_mm", "particle_size_um", "column_temperature_c",
                    "flow_rate_ml_min",
                )),
                ("AD", FULL_COLUMN_GOLDEN_CONDITIONS["stationary_phase"], "coated", 150.0, 4.6, 5.0, 35.0, 2.0),
                ("AD", FULL_COLUMN_GOLDEN_CONDITIONS["stationary_phase"], "coated", 150.0, 4.6, 5.0, 35.0, 2.0),
            ],
        )
    catalog = SfcCandidateCatalog(source)
    options = catalog.options_for_model(SfcModelDomain(
        model_id="full-model",
        model_version="1",
        scope="all_column",
    ))

    assert options["scope_id"] is None
    assert options["candidate_count"] == 2
    fields = {field["key"]: field["values"] for field in options["fields"]}
    assert fields["column"] == ["AD"]
    assert fields["flow_rate_ml_min"] == [2.0, 2.5]
