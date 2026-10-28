from __future__ import annotations

import importlib.util
from io import StringIO
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import JSON

from chrom_platform.assembly.profile import _profile_migrations
from chrom_platform.modules.assistant.persistence import AssistantBase, AssistantFrameworkStateRow


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
MIGRATION_PATH = BACKEND / "alembic" / "versions" / "0021_assistant_framework_state.py"


def test_private_framework_state_metadata_and_orm_keep_trusted_history() -> None:
    table = AssistantBase.metadata.tables["assistant_framework_state"]
    assert set(table.c.keys()) == {
        "agent_run_id", "project_id", "conversation_id", "engine",
        "framework_version", "serialization_version", "config_hash", "state",
        "stream_message_id", "created_at", "updated_at",
    }
    assert list(table.primary_key.columns.keys()) == ["agent_run_id"]
    assert isinstance(table.c.state.type, JSON)
    assert {fk.target_fullname for fk in table.c.agent_run_id.foreign_keys} == {"agent_run.id"}
    assert {fk.target_fullname for fk in table.c.stream_message_id.foreign_keys} == {"message.id"}

    state = {
        "messages": [
            {"kind": "request", "parts": [{"kind": "tool-call", "args": {"query": "silica"}}]},
            {"kind": "response", "parts": [{"kind": "tool-return", "content": {"matches": ["a", "b"]}}]},
        ],
        "usage": {"requests": 1, "input_tokens": 24},
        "pending": {"tool_call_id": "call-1", "arguments": {"revision": 3}},
    }
    row = AssistantFrameworkStateRow(
        agent_run_id="run-private",
        project_id="project-1",
        conversation_id="conversation-1",
        engine="pydantic-ai",
        framework_version="2.31.1",
        serialization_version="pydantic-ai:1",
        config_hash="a" * 64,
        state=state,
        stream_message_id=None,
    )
    assert row.engine == "pydantic-ai"
    assert row.state == state


def test_framework_state_migration_renders_offline_mysql_ddl_and_downgrade() -> None:
    spec = importlib.util.spec_from_file_location("assistant_framework_state_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "0021_assistant_framework_state"
    assert migration.down_revision == "0020_assistant_llm_configuration_lifecycle"

    upgrade_sql = StringIO()
    context = MigrationContext.configure(
        dialect_name="mysql",
        opts={"as_sql": True, "output_buffer": upgrade_sql},
    )
    with Operations.context(context):
        migration.upgrade()
    rendered = upgrade_sql.getvalue().lower()
    assert "create table assistant_framework_state" in rendered
    assert "state json not null" in rendered
    assert "primary key (agent_run_id)" in rendered
    assert "fk_assistant_framework_state_agent_run_id_agent_run" in rendered
    assert "fk_assistant_framework_state_stream_message_id_message" in rendered

    downgrade_sql = StringIO()
    context = MigrationContext.configure(
        dialect_name="mysql",
        opts={"as_sql": True, "output_buffer": downgrade_sql},
    )
    with Operations.context(context):
        migration.downgrade()
    assert "drop table assistant_framework_state" in downgrade_sql.getvalue().lower()


def test_profile_migration_pruning_selects_checkpoint_only_with_assistant() -> None:
    versions = BACKEND / "alembic" / "versions"
    assistant = _profile_migrations(versions, {"assistant"})
    prediction = _profile_migrations(versions, {"prediction"})
    assert "0021_assistant_framework_state.py" in assistant
    assert "0020_assistant_llm_configuration_lifecycle.py" in assistant
    assert "0021_assistant_framework_state.py" not in prediction
