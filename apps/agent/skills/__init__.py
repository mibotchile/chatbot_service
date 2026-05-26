"""Skill loader for PIA agent platform.

Skills are modular prompt components loaded per-tenant.
Each skill is a directory with a SKILL.md file containing prompt
text with {variable} placeholders rendered at build time.
"""

from __future__ import annotations

import re
from pathlib import Path

_SKILLS_DIR = Path(__file__).resolve().parent
_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)


class _SafeDict(dict):
    """Returns {key} as-is for missing keys — safe for format_map."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def load_skill(name: str, context: dict, skills_dir: Path | None = None) -> str:
    """Load a skill's SKILL.md, strip frontmatter, render variables."""
    base = skills_dir or _SKILLS_DIR
    md_path = base / name / "SKILL.md"
    if not md_path.exists():
        return ""
    text = md_path.read_text()
    text = _FRONTMATTER_RE.sub("", text)
    return text.strip().format_map(_SafeDict(context))


# Default skill set (cobranza vertical).
# Generic engine skills + cobranza placeholders. The sales-specific skills
# (metodo-ventas, arsenal-ventas, faq, cultura-financiera, reglas-fundamentales)
# were NOT ported — replaced by negociacion-cobranza / regulacion-cobranza (TODO Fase 1).
# load_skill() returns "" for any missing skill, so unported names are harmless.
DEFAULT_SKILLS = [
    "herramientas",
    "anti-patrones",
    "negociacion-cobranza",
    "regulacion-cobranza",
    "formato-respuesta",
]

# Channel-conditional skills
WEB_ONLY_SKILLS = ["navegacion-web"]
WHATSAPP_ONLY_SKILLS = ["canal-whatsapp"]
