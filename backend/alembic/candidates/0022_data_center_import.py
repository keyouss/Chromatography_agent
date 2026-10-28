"""CANDIDATE ONLY: align import enums and add project-scoped experiment rows.

Not in versions/: activation and execution require a separate reviewed migration
window. Historical migrations stay unchanged. Legacy values are never renamed.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0022_data_center_import"
down_revision = "0021_assistant_framework_state"
branch_labels = None
depends_on = None

_MYSQL = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"}


def _require_empty(query: str, reason: str) -> None:
    if op.get_bind().execute(sa.text(query)).scalar_one():
        raise RuntimeError(reason)


def upgrade() -> None:
    # MySQL DDL may commit implicitly. Run all data guards before any DDL.
    bind = op.get_bind()
    heads = set(bind.execute(sa.text("SELECT version_num FROM alembic_version")).scalars())
    if heads != {down_revision}:
        raise RuntimeError("Migration head differs from the reviewed candidate baseline")
    inspector = sa.inspect(bind)
    for table, expected_name in (
        ("data_import", "ck_data_import_ck_data_import_import_mode"),
        ("data_domain", "ck_data_domain_ck_data_domain_deduplication_policy"),
    ):
        if expected_name not in {item["name"] for item in inspector.get_check_constraints(table)}:
            raise RuntimeError("Existing CHECK names differ from the reviewed database DDL")
    _require_empty(
        "SELECT COUNT(*) FROM data_import WHERE import_mode NOT IN ('append','upsert')",
        "Legacy import modes require explicit review; replace is not upsert",
    )
    _require_empty(
        "SELECT COUNT(*) FROM data_domain WHERE deduplication_policy NOT IN ('reject','update_existing')",
        "Legacy deduplication policies require explicit review; no automatic mapping",
    )
    # The reviewed 0021 database has these double-prefixed legacy names.
    # op.f prevents the active naming convention from prefixing final names again.
    op.drop_constraint(op.f("ck_data_import_ck_data_import_import_mode"), "data_import", type_="check")
    op.create_check_constraint(op.f("ck_data_import_import_mode"), "data_import", "import_mode IN ('append','upsert')")
    op.drop_constraint(op.f("ck_data_domain_ck_data_domain_deduplication_policy"), "data_domain", type_="check")
    op.create_check_constraint(op.f("ck_data_domain_deduplication_policy"), "data_domain", "deduplication_policy IN ('reject','update_existing')")
    op.create_unique_constraint("uq_data_version_project_id_id", "data_version", ["project_id", "id"])
    op.create_unique_constraint("uq_structure_project_id_id", "structure", ["project_id", "id"])
    op.create_table(
        "molecule",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("project_id", sa.String(96), nullable=False),
        sa.Column("structure_id", sa.String(96), nullable=False),
        sa.Column("canonical_smiles", sa.Text(), nullable=False),
        sa.Column("inchikey", sa.String(32), nullable=True),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("external_reference", sa.String(255), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("quality_status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_molecule"),
        sa.UniqueConstraint("project_id", "structure_id", name="uq_molecule_project_structure"),
        sa.UniqueConstraint("project_id", "id", name="uq_molecule_project_id_id"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_molecule_project_id_project", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id", "structure_id"], ["structure.project_id", "structure.id"], name="fk_molecule_project_structure", ondelete="RESTRICT"),
        sa.CheckConstraint("quality_status IN ('UNKNOWN','VALID','WARNING','INVALID')", name=op.f("ck_molecule_quality_status")),
        **_MYSQL,
    )
    op.create_index("ix_molecule_project_created", "molecule", ["project_id", "created_at"])
    op.create_table(
        "experiment_record",
        sa.Column("id", sa.String(96), nullable=False),
        sa.Column("project_id", sa.String(96), nullable=False),
        sa.Column("molecule_id", sa.String(96), nullable=False),
        sa.Column("domain_id", sa.String(96), nullable=False),
        sa.Column("chromatography_type", sa.String(32), nullable=False),
        sa.Column("data_version_id", sa.String(96), nullable=False),
        sa.Column("identity_hash", sa.String(64), nullable=False),
        sa.Column("values", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_experiment_record"),
        sa.UniqueConstraint("project_id", "domain_id", "data_version_id", "identity_hash", name="uq_experiment_record_version_identity"),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], name="fk_experiment_record_project_id_project", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id", "molecule_id"], ["molecule.project_id", "molecule.id"], name="fk_experiment_record_project_molecule", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id", "data_version_id"], ["data_version.project_id", "data_version.id"], name="fk_experiment_record_project_data_version", ondelete="RESTRICT"),
        sa.CheckConstraint("CHAR_LENGTH(identity_hash) = 64", name=op.f("ck_experiment_record_identity_hash_length")),
        **_MYSQL,
    )
    op.create_index("ix_experiment_record_project_molecule", "experiment_record", ["project_id", "molecule_id"])
    op.create_index("ix_experiment_record_project_version", "experiment_record", ["project_id", "data_version_id"])


def downgrade() -> None:
    # A rollback cannot discard observations or silently reinterpret policies.
    _require_empty("SELECT COUNT(*) FROM experiment_record", "Downgrade would discard experiment records")
    _require_empty("SELECT COUNT(*) FROM molecule", "Downgrade would discard molecule identities")
    _require_empty("SELECT COUNT(*) FROM data_import WHERE import_mode <> 'append'", "Downgrade needs explicit import-mode review")
    _require_empty("SELECT COUNT(*) FROM data_domain WHERE deduplication_policy <> 'reject'", "Downgrade needs explicit deduplication review")
    op.drop_table("experiment_record")
    op.drop_table("molecule")
    op.drop_constraint("uq_structure_project_id_id", "structure", type_="unique")
    op.drop_constraint("uq_data_version_project_id_id", "data_version", type_="unique")
    op.drop_constraint(op.f("ck_data_import_import_mode"), "data_import", type_="check")
    op.create_check_constraint(op.f("ck_data_import_ck_data_import_import_mode"), "data_import", "import_mode IN ('append','replace')")
    op.drop_constraint(op.f("ck_data_domain_deduplication_policy"), "data_domain", type_="check")
    op.create_check_constraint(op.f("ck_data_domain_ck_data_domain_deduplication_policy"), "data_domain", "deduplication_policy IN ('reject','update','allow')")
