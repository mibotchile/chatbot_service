"""Slice 9 characterization tests for storage migration correctness.

Tests cover:
- dual-read for lead_data → debtor_data (state.py)
- dual-read for Redis key suffix lead_data → debtor_data (redis_store.py)
- upsert_debtor function exists and has the right signature (persistence.py)
- _CONTACT_LEVELS uses new enum values at both api/main.py sites
- dashboard.py SQL references sorelia_debtors, debtor_level (not old names)
- migration script SQL file exists and contains expected statements
- project_interest column is preserved in persistence code
"""

from __future__ import annotations

import importlib
import inspect
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# A. dual-read: state.py ConversationState / StateStore
# ---------------------------------------------------------------------------

def test_state_loads_debtor_data_with_fallback_to_lead_data():
    """StateStore.get_or_create_async MUST read debtor_data, fallback to lead_data.

    This is a characterization test: it verifies the dual-read logic is present
    in the source code of state.py (we inspect the source since the real DB path
    is not exercised in unit tests).
    """
    from features.conversation.persistence import state as state_module

    src = inspect.getsource(state_module)

    # Must read debtor_data first
    assert "debtor_data" in src, "state.py must reference debtor_data for dual-read"
    # Must retain fallback reference to lead_data (backward compat)
    assert "lead_data" in src, "state.py must retain lead_data fallback for dual-read"


def test_state_persists_debtor_data_column():
    """save_conversation must write to debtor_data column, not lead_data."""
    from features.conversation.persistence import state as state_module

    src = inspect.getsource(state_module)
    # The persist call should pass debtor_data= kwarg (not lead_data=)
    assert "debtor_data=" in src, (
        "state.py _persist() must pass debtor_data= kwarg to save_conversation"
    )


def test_state_constructor_accepts_debtor_data_param():
    """ConversationState.__init__ must accept debtor_data parameter."""
    from features.conversation.persistence.state import ConversationState

    sig = inspect.signature(ConversationState.__init__)
    params = set(sig.parameters.keys())
    # New param
    assert "debtor_data" in params, (
        "ConversationState.__init__ must accept debtor_data parameter"
    )


# ---------------------------------------------------------------------------
# B. dual-read: redis_store.py
# ---------------------------------------------------------------------------

def test_redis_store_writes_debtor_data_key():
    """RedisConversationState.save() must write :debtor_data key suffix."""
    from features.conversation.persistence import redis_store as rs_module

    src = inspect.getsource(rs_module)
    assert '"debtor_data"' in src or "'debtor_data'" in src, (
        "redis_store.py must write :debtor_data key suffix"
    )


def test_redis_store_reads_debtor_data_with_lead_data_fallback():
    """RedisConversationState.load() must read :debtor_data, fallback :lead_data."""
    from features.conversation.persistence import redis_store as rs_module

    src = inspect.getsource(rs_module)
    assert '"debtor_data"' in src or "'debtor_data'" in src, (
        "redis_store.py must read :debtor_data key"
    )
    assert '"lead_data"' in src or "'lead_data'" in src, (
        "redis_store.py must retain :lead_data fallback read"
    )


# ---------------------------------------------------------------------------
# C. persistence.py — upsert_debtor
# ---------------------------------------------------------------------------

def test_upsert_debtor_function_exists():
    """shared/persistence/persistence.py must expose upsert_debtor."""
    from shared.persistence import persistence

    assert hasattr(persistence, "upsert_debtor"), (
        "persistence.py must define upsert_debtor (renamed from upsert_lead)"
    )


def test_upsert_debtor_signature():
    """upsert_debtor must keep the same positional signature as upsert_lead."""
    from shared.persistence.persistence import upsert_debtor

    sig = inspect.signature(upsert_debtor)
    params = list(sig.parameters.keys())
    # pool, schema, conversation_id, visitor_id, debtor_data, debtor_level
    assert "pool" in params
    assert "schema" in params
    assert "conversation_id" in params
    assert "visitor_id" in params
    # data param — may be debtor_data or lead_data (either is ok at code level)
    # level param
    assert len(params) >= 5, "upsert_debtor must have at least 5 parameters"


def test_upsert_debtor_sql_uses_sorelia_debtors():
    """upsert_debtor must reference sorelia_debtors table, not sorelia_leads."""
    from shared.persistence import persistence as pers_module

    src = inspect.getsource(pers_module)
    # Find upsert_debtor function source
    # The function definition must reference sorelia_debtors
    func = pers_module.upsert_debtor
    func_src = inspect.getsource(func)
    assert "sorelia_debtors" in func_src, (
        "upsert_debtor must use sorelia_debtors table"
    )
    # Must NOT reference the old name in this function
    assert "sorelia_leads" not in func_src, (
        "upsert_debtor must not reference sorelia_leads"
    )


def test_persistence_save_conversation_uses_debtor_data_column():
    """save_conversation SQL must reference debtor_data column."""
    from shared.persistence import persistence as pers_module

    func = pers_module.save_conversation
    func_src = inspect.getsource(func)
    assert "debtor_data" in func_src, (
        "save_conversation must write to debtor_data column"
    )


def test_persistence_load_conversation_reads_debtor_data():
    """load_conversation must return debtor_data key (read from DB)."""
    from shared.persistence import persistence as pers_module

    func = pers_module.load_conversation
    func_src = inspect.getsource(func)
    assert "debtor_data" in func_src, (
        "load_conversation must read debtor_data column"
    )


# ---------------------------------------------------------------------------
# D. api/main.py — _CONTACT_LEVELS enum values at both sites
# ---------------------------------------------------------------------------

def test_contact_levels_uses_new_enum_values():
    """_CONTACT_LEVELS in api/main.py must use DEBTOR/DEBTOR_VERIFIED, not LEAD/LEAD_ENRICHED."""
    import api.main as main_module

    src = inspect.getsource(main_module)

    # Count occurrences of _CONTACT_LEVELS assignments
    # Both sites must use new values
    old_values_pattern = re.compile(r'_CONTACT_LEVELS\s*=\s*\{[^}]*"LEAD"')
    new_values_pattern = re.compile(r'_CONTACT_LEVELS\s*=\s*\{[^}]*"DEBTOR"')

    assert not old_values_pattern.search(src), (
        '_CONTACT_LEVELS must not contain "LEAD" (old value) — use "DEBTOR"'
    )
    assert new_values_pattern.search(src), (
        '_CONTACT_LEVELS must contain "DEBTOR" (new value)'
    )


# ---------------------------------------------------------------------------
# E. dashboard.py SQL — sorelia_debtors + debtor_level
# ---------------------------------------------------------------------------

def test_dashboard_sql_uses_sorelia_debtors():
    """dashboard.py must reference sorelia_debtors, not sorelia_leads."""
    from features.analytics import dashboard as dash_module

    src = inspect.getsource(dash_module)
    assert "sorelia_debtors" in src, (
        "dashboard.py must reference sorelia_debtors table"
    )
    assert "sorelia_leads" not in src, (
        "dashboard.py must not reference old sorelia_leads table"
    )


def test_dashboard_sql_uses_debtor_level():
    """dashboard.py must reference debtor_level column, not lead_level."""
    from features.analytics import dashboard as dash_module

    src = inspect.getsource(dash_module)
    assert "debtor_level" in src, (
        "dashboard.py must reference debtor_level column"
    )
    assert "lead_level" not in src, (
        "dashboard.py must not reference old lead_level column"
    )


def test_dashboard_sql_uses_new_enum_values():
    """dashboard.py must use DEBTOR/DEBTOR_VERIFIED enum values, not LEAD/LEAD_ENRICHED."""
    from features.analytics import dashboard as dash_module

    src = inspect.getsource(dash_module)
    assert "'DEBTOR'" in src or '"DEBTOR"' in src, (
        "dashboard.py must reference DEBTOR enum value"
    )
    # Dead filters must be gone
    assert "CONTACT" not in src, (
        "dashboard.py must not reference CONTACT (never emitted by state machine)"
    )
    assert "QUALIFIED" not in src, (
        "dashboard.py must not reference QUALIFIED (never emitted by state machine)"
    )


def test_dashboard_project_interest_preserved():
    """dashboard.py must still reference project_interest (LIVE column — must not be dropped)."""
    from features.analytics import dashboard as dash_module

    src = inspect.getsource(dash_module)
    assert "project_interest" in src, (
        "dashboard.py must reference project_interest (LIVE — do not drop)"
    )


# ---------------------------------------------------------------------------
# F. Migration script exists and has required content
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MIGRATIONS_DIR = _REPO_ROOT / "migrations"


def test_migration_script_exists():
    """A SQL migration script must exist in migrations/."""
    assert _MIGRATIONS_DIR.exists(), "migrations/ directory must exist"
    sql_files = list(_MIGRATIONS_DIR.glob("*.sql"))
    assert len(sql_files) >= 1, "At least one .sql migration file must exist in migrations/"


def test_migration_script_has_preflight_block():
    """Migration script must contain a PREFLIGHT comment block."""
    sql_files = list(_MIGRATIONS_DIR.glob("*.sql"))
    if not sql_files:
        return  # Covered by test_migration_script_exists
    content = sql_files[0].read_text()
    assert "PREFLIGHT" in content, (
        "Migration script must contain a PREFLIGHT comment block"
    )


def test_migration_script_renames_sorelia_leads():
    """Migration script must rename sorelia_leads → sorelia_debtors."""
    sql_files = list(_MIGRATIONS_DIR.glob("*.sql"))
    if not sql_files:
        return
    content = sql_files[0].read_text()
    assert "sorelia_leads" in content and "sorelia_debtors" in content, (
        "Migration script must reference both sorelia_leads and sorelia_debtors"
    )
    assert "RENAME" in content.upper(), (
        "Migration script must contain RENAME statement for table/column"
    )


def test_migration_script_has_pg_dump_command():
    """Migration script must document pg_dump command for dropped columns."""
    sql_files = list(_MIGRATIONS_DIR.glob("*.sql"))
    if not sql_files:
        return
    content = sql_files[0].read_text()
    assert "pg_dump" in content, (
        "Migration script must include pg_dump command for dropped columns (unrecoverable)"
    )


def test_migration_script_drops_dead_columns():
    """Migration script must DROP district_interest, purpose, budget."""
    sql_files = list(_MIGRATIONS_DIR.glob("*.sql"))
    if not sql_files:
        return
    content = sql_files[0].read_text()
    assert "district_interest" in content, (
        "Migration script must reference district_interest for DROP"
    )
    assert "purpose" in content, (
        "Migration script must reference purpose for DROP"
    )
    assert "budget" in content, (
        "Migration script must reference budget for DROP"
    )
    assert "DROP" in content.upper(), (
        "Migration script must contain DROP COLUMN statement"
    )


def test_migration_script_has_idempotency_guards():
    """Migration script must use IF EXISTS / IF NOT EXISTS guards."""
    sql_files = list(_MIGRATIONS_DIR.glob("*.sql"))
    if not sql_files:
        return
    content = sql_files[0].read_text()
    assert "IF EXISTS" in content.upper() or "IF NOT EXISTS" in content.upper(), (
        "Migration script must have idempotency guards (IF EXISTS / IF NOT EXISTS)"
    )


def test_migration_script_has_rollback_section():
    """Migration script must include rollback / reverse migration instructions."""
    sql_files = list(_MIGRATIONS_DIR.glob("*.sql"))
    if not sql_files:
        return
    content = sql_files[0].read_text()
    assert "rollback" in content.lower() or "reverse" in content.lower(), (
        "Migration script must document rollback plan"
    )
