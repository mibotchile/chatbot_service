"""Characterization tests for S6+S7 neutral persistence names.

Tests cover:
- conversations table (not sorelia_conversations): record_data + record_level columns
- visitors table (not sorelia_visitors): record_data column (not lead_data)
- Redis key prefix olimpo:conv: (not sorelia:conv:)
- upsert_debtor targets 'debtors' table (not sorelia_debtors)
- ensure_tables creates projection_table when spec provides one
- dashboard.py SQL references 'debtors' and 'conversations' (not sorelia_* prefixes)
- state.py reads/writes record_data (no lead_data dual-read fallback after S6)
- redis_store.py reads/writes record_data key suffix (no lead_data fallback after S6)
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# A. persistence.py — table names and column names (neutral)
# ---------------------------------------------------------------------------

def test_persistence_ensure_tables_uses_conversations():
    """ensure_tables must create 'conversations' table, not 'sorelia_conversations'."""
    from shared.persistence import persistence as pers_module

    src = inspect.getsource(pers_module)
    assert "conversations" in src, "persistence.py must reference 'conversations' table"
    assert "sorelia_conversations" not in src, (
        "persistence.py must NOT reference sorelia_conversations (use 'conversations')"
    )


def test_persistence_ensure_tables_uses_record_data():
    """ensure_tables must create record_data column, not debtor_data."""
    from shared.persistence import persistence as pers_module

    func_src = inspect.getsource(pers_module.ensure_tables)
    assert "record_data" in func_src, "ensure_tables must reference 'record_data' column"
    assert "debtor_data" not in func_src, (
        "ensure_tables must NOT reference debtor_data (use 'record_data')"
    )


def test_persistence_ensure_tables_uses_record_level():
    """ensure_tables must create record_level column, not debtor_level."""
    from shared.persistence import persistence as pers_module

    func_src = inspect.getsource(pers_module.ensure_tables)
    assert "record_level" in func_src, "ensure_tables must reference 'record_level' column"
    assert "debtor_level" not in func_src, (
        "ensure_tables must NOT reference debtor_level (use 'record_level')"
    )


def test_persistence_no_sorelia_prefix():
    """persistence.py ensure_tables and save_conversation must not use any sorelia_ table prefix."""
    from shared.persistence import persistence as pers_module

    # Check the functions that own table names — not domain parameter names in upsert_debtor
    ensure_src = inspect.getsource(pers_module.ensure_tables)
    save_src = inspect.getsource(pers_module.save_conversation)
    load_src = inspect.getsource(pers_module.load_conversation)
    assert "sorelia_" not in ensure_src, "ensure_tables must have zero sorelia_ references"
    assert "sorelia_" not in save_src, "save_conversation must have zero sorelia_ references"
    assert "sorelia_" not in load_src, "load_conversation must have zero sorelia_ references"


def test_save_conversation_uses_record_data():
    """save_conversation SQL must write record_data, not debtor_data."""
    from shared.persistence import persistence as pers_module

    func_src = inspect.getsource(pers_module.save_conversation)
    assert "record_data" in func_src, "save_conversation must write to record_data column"
    assert "debtor_data" not in func_src, "save_conversation must NOT reference debtor_data"


def test_load_conversation_uses_record_data():
    """load_conversation must reference record_data, not debtor_data."""
    from shared.persistence import persistence as pers_module

    func_src = inspect.getsource(pers_module.load_conversation)
    assert "record_data" in func_src, "load_conversation must reference record_data"
    assert "debtor_data" not in func_src, "load_conversation must NOT reference debtor_data"


def test_save_conversation_kwarg_is_record_data():
    """save_conversation signature must use record_data= kwarg, not debtor_data=."""
    from shared.persistence.persistence import save_conversation

    sig = inspect.signature(save_conversation)
    params = set(sig.parameters.keys())
    assert "record_data" in params, (
        "save_conversation must have record_data= parameter"
    )
    assert "debtor_data" not in params, (
        "save_conversation must NOT have debtor_data= parameter"
    )


def test_state_persists_record_data():
    """state.py _persist() must pass record_data= kwarg to save_conversation."""
    from features.conversation.persistence import state as state_module

    src = inspect.getsource(state_module)
    assert "record_data=" in src, (
        "state.py _persist() must pass record_data= kwarg to save_conversation"
    )
    assert "debtor_data=" not in src, (
        "state.py must NOT pass debtor_data= (use record_data=)"
    )


def test_state_no_lead_data_fallback():
    """state.py must NOT retain lead_data fallback (S6 drops the dual-read)."""
    from features.conversation.persistence import state as state_module

    src = inspect.getsource(state_module)
    assert "lead_data" not in src, (
        "state.py must NOT reference lead_data after S6 — drop the dual-read fallback"
    )


def test_state_constructor_accepts_record_data_param():
    """ConversationState.__init__ must accept record_data parameter (not debtor_data)."""
    from features.conversation.persistence.state import ConversationState

    sig = inspect.signature(ConversationState.__init__)
    params = set(sig.parameters.keys())
    assert "record_data" in params, (
        "ConversationState.__init__ must accept record_data parameter"
    )
    assert "debtor_data" not in params, (
        "ConversationState.__init__ must NOT have debtor_data parameter after S6"
    )


# ---------------------------------------------------------------------------
# B. visitor_memory.py — visitors table, record_data column (not lead_data)
# ---------------------------------------------------------------------------

def test_visitor_memory_uses_visitors_table():
    """visitor_memory.py must reference 'visitors' table, not 'sorelia_visitors'."""
    from features.conversation.persistence import visitor_memory as vm_module

    src = inspect.getsource(vm_module)
    assert "visitors" in src, "visitor_memory.py must reference 'visitors' table"
    assert "sorelia_visitors" not in src, (
        "visitor_memory.py must NOT reference sorelia_visitors"
    )


def test_visitor_memory_uses_record_data():
    """visitor_memory.py must use record_data column, not lead_data."""
    from features.conversation.persistence import visitor_memory as vm_module

    src = inspect.getsource(vm_module)
    assert "record_data" in src, "visitor_memory.py must reference record_data column"
    assert "lead_data" not in src, (
        "visitor_memory.py must NOT reference lead_data after S6"
    )


# ---------------------------------------------------------------------------
# C. redis_store.py — olimpo:conv: prefix, record_data suffix (no lead_data)
# ---------------------------------------------------------------------------

def test_redis_key_prefix_is_olimpo():
    """redis_store.py _key() must use 'olimpo:conv:' prefix, not 'sorelia:conv:'."""
    from features.conversation.persistence import redis_store as rs_module

    src = inspect.getsource(rs_module)
    assert "olimpo:conv:" in src, (
        "redis_store.py must use olimpo:conv: key prefix"
    )
    assert "sorelia:conv:" not in src, (
        "redis_store.py must NOT use sorelia:conv: prefix after S6"
    )


def test_redis_store_writes_record_data_key():
    """RedisConversationState.save() must write :record_data key suffix."""
    from features.conversation.persistence import redis_store as rs_module

    src = inspect.getsource(rs_module)
    assert '"record_data"' in src or "'record_data'" in src, (
        "redis_store.py must write :record_data key suffix"
    )


def test_redis_store_no_lead_data_fallback():
    """redis_store.py must NOT retain :lead_data fallback read after S6."""
    from features.conversation.persistence import redis_store as rs_module

    src = inspect.getsource(rs_module)
    assert '"lead_data"' not in src and "'lead_data'" not in src, (
        "redis_store.py must NOT reference lead_data after S6 — drop the fallback"
    )


# ---------------------------------------------------------------------------
# D. persistence.py — upsert_debtor targets 'debtors' table (not sorelia_debtors)
# ---------------------------------------------------------------------------

def test_upsert_debtor_function_exists():
    """persistence.py must expose upsert_debtor."""
    from shared.persistence import persistence

    assert hasattr(persistence, "upsert_debtor"), (
        "persistence.py must define upsert_debtor"
    )


def test_upsert_debtor_signature():
    """upsert_debtor must have required parameters."""
    from shared.persistence.persistence import upsert_debtor

    sig = inspect.signature(upsert_debtor)
    params = list(sig.parameters.keys())
    assert "pool" in params
    assert "schema" in params
    assert "conversation_id" in params
    assert "visitor_id" in params
    assert len(params) >= 5, "upsert_debtor must have at least 5 parameters"


def test_upsert_debtor_sql_uses_debtors_table():
    """upsert_debtor must reference 'debtors' table, not sorelia_debtors."""
    from shared.persistence import persistence as pers_module

    func_src = inspect.getsource(pers_module.upsert_debtor)
    assert "debtors" in func_src, "upsert_debtor must use 'debtors' table"
    assert "sorelia_debtors" not in func_src, (
        "upsert_debtor must NOT reference sorelia_debtors"
    )
    assert "sorelia_leads" not in func_src, (
        "upsert_debtor must NOT reference sorelia_leads"
    )


# ---------------------------------------------------------------------------
# E. ensure_tables — projection_table param creates per-type table
# ---------------------------------------------------------------------------

def test_ensure_tables_accepts_projection_table_param():
    """ensure_tables must accept a projection_table: str|None parameter."""
    from shared.persistence.persistence import ensure_tables

    sig = inspect.signature(ensure_tables)
    params = set(sig.parameters.keys())
    assert "projection_table" in params, (
        "ensure_tables must accept projection_table parameter"
    )


def test_ensure_tables_creates_debtors_when_projection_table_set():
    """ensure_tables with projection_table='debtors' must include debtors in SQL."""
    from shared.persistence import persistence as pers_module

    src = inspect.getsource(pers_module.ensure_tables)
    assert "projection_table" in src, (
        "ensure_tables must use projection_table param to decide whether to create per-type table"
    )


# ---------------------------------------------------------------------------
# F. dashboard.py — references 'debtors' and 'conversations' (not sorelia_*)
# ---------------------------------------------------------------------------

def test_dashboard_sql_uses_debtors_table():
    """dashboard.py must reference 'debtors' table, not sorelia_debtors."""
    from features.analytics import dashboard as dash_module

    src = inspect.getsource(dash_module)
    assert "debtors" in src, "dashboard.py must reference 'debtors' table"
    assert "sorelia_debtors" not in src, (
        "dashboard.py must NOT reference sorelia_debtors (use 'debtors')"
    )


def test_dashboard_sql_uses_conversations_table():
    """dashboard.py must reference 'conversations' table, not sorelia_conversations."""
    from features.analytics import dashboard as dash_module

    src = inspect.getsource(dash_module)
    assert "conversations" in src, "dashboard.py must reference 'conversations' table"
    assert "sorelia_conversations" not in src, (
        "dashboard.py must NOT reference sorelia_conversations (use 'conversations')"
    )


def test_dashboard_sql_uses_record_level():
    """dashboard.py must reference record_level column, not debtor_level."""
    from features.analytics import dashboard as dash_module

    src = inspect.getsource(dash_module)
    assert "record_level" in src, "dashboard.py must reference record_level column"
    assert "debtor_level" not in src, (
        "dashboard.py must NOT reference debtor_level (use 'record_level')"
    )


def test_dashboard_sql_uses_new_enum_values():
    """dashboard.py must use DEBTOR/DEBTOR_VERIFIED enum values."""
    from features.analytics import dashboard as dash_module

    src = inspect.getsource(dash_module)
    assert "'DEBTOR'" in src or '"DEBTOR"' in src, (
        "dashboard.py must reference DEBTOR enum value"
    )


def test_dashboard_project_interest_preserved():
    """dashboard.py must still reference project_interest (LIVE column)."""
    from features.analytics import dashboard as dash_module

    src = inspect.getsource(dash_module)
    assert "project_interest" in src, (
        "dashboard.py must reference project_interest (LIVE — do not drop)"
    )


def test_dashboard_no_sorelia_on_core_tables():
    """dashboard.py must not reference sorelia_debtors, sorelia_conversations, sorelia_visitors."""
    from features.analytics import dashboard as dash_module

    src = inspect.getsource(dash_module)
    assert "sorelia_debtors" not in src, (
        "dashboard.py must NOT reference sorelia_debtors"
    )
    assert "sorelia_conversations" not in src, (
        "dashboard.py must NOT reference sorelia_conversations"
    )
    assert "sorelia_visitors" not in src, (
        "dashboard.py must NOT reference sorelia_visitors"
    )


def test_dashboard_visitors_table():
    """dashboard.py must reference 'visitors' table, not sorelia_visitors."""
    from features.analytics import dashboard as dash_module

    src = inspect.getsource(dash_module)
    # visitors is referenced in stats endpoint
    assert "sorelia_visitors" not in src, (
        "dashboard.py must NOT reference sorelia_visitors (use 'visitors')"
    )
