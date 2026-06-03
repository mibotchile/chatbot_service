"""Response guard — strips repeated data asks from agent responses.

Prevents the agent from nagging the user by asking for the same contact
data field multiple times in consecutive messages.
"""

import re

# Map lead field names to Spanish detection patterns.
# Each pattern matches common ways the agent asks for that field.
_FIELD_PATTERNS: dict[str, re.Pattern] = {
    "email": re.compile(
        r"\b(correo|e[\-\s]?mail)\b", re.IGNORECASE
    ),
    "phone": re.compile(
        r"\b(tel[eé]fono|celular|n[uú]mero|whatsapp)\b", re.IGNORECASE
    ),
    "name": re.compile(
        r"\b(nombre|c[oó]mo te llamas|con qui[eé]n)\b", re.IGNORECASE
    ),
    "document_number": re.compile(
        r"\b(dni|documento)\b", re.IGNORECASE
    ),
}

# Sentence boundary: split on period, question mark, exclamation, or newline
# while keeping the delimiter attached to the preceding sentence.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")

_LOOKBACK = 2  # how many previous assistant messages to scan


def _detect_fields_in_text(text: str) -> set[str]:
    """Return which lead fields are being asked for in *text*."""
    found: set[str] = set()
    for field, pattern in _FIELD_PATTERNS.items():
        if pattern.search(text):
            found.add(field)
    return found


def _recent_assistant_messages(history: list[dict], n: int) -> list[str]:
    """Return the last *n* assistant message contents from history."""
    msgs: list[str] = []
    for entry in reversed(history):
        if entry.get("role") == "assistant":
            msgs.append(entry["content"])
            if len(msgs) >= n:
                break
    return msgs


def _strip_sentences_for_fields(text: str, fields: set[str]) -> str:
    """Remove sentences that ask for any of the given *fields*."""
    sentences = _SENTENCE_SPLIT.split(text)
    kept: list[str] = []
    for sentence in sentences:
        sentence_fields = _detect_fields_in_text(sentence)
        if sentence_fields & fields:
            # Drop this sentence entirely
            continue
        kept.append(sentence)

    result = " ".join(kept).strip()
    # If we stripped everything, return the original — better to repeat
    # than to return an empty response.
    return result if result else text


def guard_response(
    content: str,
    history: list[dict],
    debtor_status: dict,
) -> str:
    """Clean *content* by removing repeated data asks.

    Args:
        content: The agent's draft response text.
        history: Full conversation history (list of role/content dicts).
                 Should NOT include *content* yet.
        debtor_status: Output of ``DebtorState.get_status()`` — must contain
                       a ``collected`` dict.

    Returns:
        The (possibly trimmed) response text.
    """
    current_asks = _detect_fields_in_text(content)
    if not current_asks:
        return content

    collected = set(debtor_status.get("collected", {}).keys())
    fields_to_strip: set[str] = set()

    # Rule 1: field already collected — strip unconditionally
    fields_to_strip |= current_asks & collected

    # Rule 2: same ask appeared in recent assistant messages — stop nagging
    recent = _recent_assistant_messages(history, _LOOKBACK)
    previously_asked: set[str] = set()
    for msg in recent:
        previously_asked |= _detect_fields_in_text(msg)

    fields_to_strip |= current_asks & previously_asked

    if not fields_to_strip:
        return content

    return _strip_sentences_for_fields(content, fields_to_strip)
