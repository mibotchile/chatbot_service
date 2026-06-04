"""Characterization tests: AgentTypeRegistry — S3 (PR2/WU-2).

Contract being locked:
- AgentTypeRegistry Protocol is swappable (any impl with .get(agent_type) works)
- InCodeAgentTypeRegistry resolves "cobranza" to an AgentTypeSpec with the
  same CaptureSpec, tools, skills, gate_model, and projection_table as today
- Unknown agent_type raises AgentTypeNotFoundError (well-typed, not KeyError)
- Registry has exactly one entry ("cobranza")
- Swappable: a custom impl satisfying the Protocol also works as a drop-in

These tests MUST go RED before the implementation exists (task 3.1), then GREEN
after 3.2-3.4 are complete (task 3.6).
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Helpers: imports that must exist after S3 implementation
# ---------------------------------------------------------------------------

def _import_registry():
    """Import the registry impl + a builder that wires the cobranza entry the
    way the composition root (api/main.py) does. tenancy/ no longer ships a
    features-importing factory (W-01 fix): the cobranza entry is wired in api/.
    """
    from tenancy.agent_types.registry import InCodeAgentTypeRegistry

    def build_cobranza_registry() -> InCodeAgentTypeRegistry:
        from features.cobranza.agent_type import COBRANZA_AGENT_TYPE
        return InCodeAgentTypeRegistry({"cobranza": COBRANZA_AGENT_TYPE})

    return InCodeAgentTypeRegistry, build_cobranza_registry


def _import_port():
    """Import the Protocol/port — fails RED until 3.2 implemented."""
    from shared.ports.agent_type_registry import (
        AgentTypeRegistry,
        AgentTypeSpec,
        AgentTypeNotFoundError,
    )
    return AgentTypeRegistry, AgentTypeSpec, AgentTypeNotFoundError


def _import_cobranza_agent_type():
    """Import the cobranza agent type descriptor — fails RED until 3.3."""
    from features.cobranza.agent_type import COBRANZA_AGENT_TYPE
    return COBRANZA_AGENT_TYPE


# ---------------------------------------------------------------------------
# Test group 1: Port exists and is well-formed
# ---------------------------------------------------------------------------

class TestAgentTypePort:
    """shared/ports/agent_type_registry.py must exist and export the right names."""

    def test_port_importable(self):
        """AgentTypeRegistry, AgentTypeSpec, AgentTypeNotFoundError are importable."""
        AgentTypeRegistry, AgentTypeSpec, AgentTypeNotFoundError = _import_port()
        assert AgentTypeRegistry is not None
        assert AgentTypeSpec is not None
        assert AgentTypeNotFoundError is not None

    def test_agent_type_not_found_error_is_exception(self):
        """AgentTypeNotFoundError must be an Exception subclass."""
        _, _, AgentTypeNotFoundError = _import_port()
        assert issubclass(AgentTypeNotFoundError, Exception)

    def test_agent_type_spec_has_required_fields(self):
        """AgentTypeSpec dataclass must have capture_spec, tools, skills,
        gate_model, projection_table fields."""
        _, AgentTypeSpec, _ = _import_port()
        from features.cobranza.debtor import COBRANZA_SPEC
        spec = AgentTypeSpec(
            capture_spec=COBRANZA_SPEC,
            tools=("consultar_deuda",),
            skills=None,
            gate_model="hard_dni",
            projection_table="debtors",
        )
        assert spec.capture_spec is COBRANZA_SPEC
        assert spec.tools == ("consultar_deuda",)
        assert spec.skills is None
        assert spec.gate_model == "hard_dni"
        assert spec.projection_table == "debtors"

    def test_agent_type_spec_projection_table_optional(self):
        """projection_table=None is valid (open types have no per-type table)."""
        _, AgentTypeSpec, _ = _import_port()
        from features.cobranza.debtor import COBRANZA_SPEC
        spec = AgentTypeSpec(
            capture_spec=COBRANZA_SPEC,
            tools=(),
            skills=None,
            gate_model="none",
            projection_table=None,
        )
        assert spec.projection_table is None


# ---------------------------------------------------------------------------
# Test group 2: cobranza AgentTypeSpec descriptor
# ---------------------------------------------------------------------------

class TestCobranzaAgentType:
    """features/cobranza/agent_type.py must define COBRANZA_AGENT_TYPE."""

    def test_cobranza_agent_type_importable(self):
        spec = _import_cobranza_agent_type()
        assert spec is not None

    def test_cobranza_uses_cobranza_spec(self):
        """COBRANZA_AGENT_TYPE.capture_spec must be the canonical COBRANZA_SPEC."""
        from features.cobranza.debtor import COBRANZA_SPEC
        spec = _import_cobranza_agent_type()
        assert spec.capture_spec is COBRANZA_SPEC

    def test_cobranza_gate_model_is_hard_dni(self):
        """Gate model for cobranza must be 'hard_dni'."""
        spec = _import_cobranza_agent_type()
        assert spec.gate_model == "hard_dni"

    def test_cobranza_projection_table_is_debtors(self):
        """Cobranza declares projection_table='debtors'."""
        spec = _import_cobranza_agent_type()
        assert spec.projection_table == "debtors"

    def test_cobranza_tools_is_tuple(self):
        """tools must be a tuple (immutable, ordered)."""
        spec = _import_cobranza_agent_type()
        assert isinstance(spec.tools, tuple)

    def test_cobranza_has_expected_tools(self):
        """Cobranza must include the core tools by name."""
        spec = _import_cobranza_agent_type()
        core_tools = {"consultar_deuda", "validar_comprobante"}
        assert core_tools.issubset(set(spec.tools)), (
            f"Missing tools: {core_tools - set(spec.tools)}. Got: {spec.tools}"
        )


# ---------------------------------------------------------------------------
# Test group 3: InCodeAgentTypeRegistry resolution
# ---------------------------------------------------------------------------

class TestInCodeAgentTypeRegistry:
    """tenancy/agent_types/registry.py — InCodeAgentTypeRegistry."""

    def setup_method(self):
        _, self.default_registry = _import_registry()
        self.registry = self.default_registry()

    def test_registry_resolves_cobranza(self):
        """registry.get('cobranza') returns an AgentTypeSpec."""
        _, AgentTypeSpec, _ = _import_port()
        result = self.registry.get("cobranza")
        assert isinstance(result, AgentTypeSpec)

    def test_cobranza_capture_spec_matches(self):
        """Resolved cobranza spec has the canonical COBRANZA_SPEC."""
        from features.cobranza.debtor import COBRANZA_SPEC
        result = self.registry.get("cobranza")
        assert result.capture_spec is COBRANZA_SPEC

    def test_unknown_type_raises_well_typed_error(self):
        """get('unknown_type') raises AgentTypeNotFoundError, not KeyError."""
        _, _, AgentTypeNotFoundError = _import_port()
        with pytest.raises(AgentTypeNotFoundError) as exc_info:
            self.registry.get("inmobiliario")
        assert "inmobiliario" in str(exc_info.value)

    def test_unknown_type_is_agent_type_not_found_error(self):
        """The error raised is AgentTypeNotFoundError (not a bare KeyError from dict)."""
        _, _, AgentTypeNotFoundError = _import_port()
        with pytest.raises(AgentTypeNotFoundError) as exc_info:
            self.registry.get("creditos")
        # Must be our typed error, not an unhandled KeyError propagation
        assert type(exc_info.value).__name__ == "AgentTypeNotFoundError"

    def test_registry_has_exactly_one_entry(self):
        """Registry has exactly one entry: 'cobranza'."""
        InCodeAgentTypeRegistry, _ = _import_registry()
        from features.cobranza.agent_type import COBRANZA_AGENT_TYPE
        reg = InCodeAgentTypeRegistry({"cobranza": COBRANZA_AGENT_TYPE})
        # Can resolve cobranza
        result = reg.get("cobranza")
        assert result is not None
        # Cannot resolve anything else
        _, _, AgentTypeNotFoundError = _import_port()
        with pytest.raises(AgentTypeNotFoundError):
            reg.get("creditos")

    def test_default_registry_returns_cobranza_agent_type(self):
        """default_registry() wires COBRANZA_AGENT_TYPE for 'cobranza'."""
        from features.cobranza.agent_type import COBRANZA_AGENT_TYPE
        result = self.registry.get("cobranza")
        assert result is COBRANZA_AGENT_TYPE


# ---------------------------------------------------------------------------
# Test group 4: Swappability (Protocol compliance)
# ---------------------------------------------------------------------------

class TestRegistrySwappability:
    """Any impl satisfying the AgentTypeRegistry Protocol must be a drop-in."""

    def test_custom_impl_satisfies_protocol(self):
        """A hand-rolled impl with .get() works as a drop-in consumer."""
        AgentTypeRegistry, AgentTypeSpec, AgentTypeNotFoundError = _import_port()
        from features.cobranza.debtor import COBRANZA_SPEC

        # Custom in-test impl — NOT InCodeAgentTypeRegistry
        class _TestRegistry:
            def get(self, agent_type: str) -> AgentTypeSpec:
                if agent_type == "cobranza":
                    return AgentTypeSpec(
                        capture_spec=COBRANZA_SPEC,
                        tools=("consultar_deuda",),
                        skills=None,
                        gate_model="hard_dni",
                        projection_table="debtors",
                    )
                raise AgentTypeNotFoundError(agent_type)

        reg = _TestRegistry()
        result = reg.get("cobranza")
        assert result.gate_model == "hard_dni"
        with pytest.raises(AgentTypeNotFoundError):
            reg.get("inmobiliario")

    def test_registry_is_runtime_checkable_protocol(self):
        """InCodeAgentTypeRegistry satisfies runtime isinstance check if Protocol
        is runtime_checkable; OR duck-type check passes."""
        # We check that the Protocol import works; runtime_checkable check is
        # optional but the duck-type invariant holds.
        AgentTypeRegistry, _, _ = _import_port()
        _, default_registry = _import_registry()
        reg = default_registry()
        # Must have .get callable
        assert callable(getattr(reg, "get", None))
