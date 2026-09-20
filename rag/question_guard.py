"""Question guard (rag/question_guard.py) - the untrusted-reviewer-text choke point.

``question`` arrives from GET /why/{store_id} and GET /advisory/{store_id}
arbitrary caller text. It is embedded in three model-facing surfaces:

  1. the explainer's fenced evidence block   (``QUESTION: ...``)
  2. the advisory block                      (``REVIEWER_QUESTION: ...``)
  3. the classifier.dev pre-filter instructions (rag/prefilter.py)

Injection shape that matters here: the evidence/advisory blocks are
line-structured. A newline smuggled inside the question can forge top-level
structure lines (``TEMPLATE_NARRATIVE:``, ``METHODOLOGY_CHUNKS:``,
``SUGGESTED_ACTION:``) that the downstream parser treats as trusted. The
defence is structural, not lexical: collapse ALL whitespace (including
newlines) to single spaces and strip control characters, so the question can
never contain a line break no matter what the caller sends.

Discipline (same as rag/llm_explainer.py):
- The transform is idempotent and never raises; callers may apply it at the
  boundary AND defensively at each consumer.
- The static system prompts are byte-stable and stay untouched (pinned by
  scripts/check.py); this guard shapes only untrusted input.
"""
from __future__ import annotations

import re

MAX_QUESTION_CHARS = 500

# Control characters (incl. \n \r \t \x00-\x1f \x7f) become spaces; then all
# runs of whitespace collapse. The result is a single printable line.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")
_WHITESPACE_RUN_RE = re.compile(r"\s+")


def sanitize_question(raw: object) -> str:
    """Return the reviewer question as a single safe line. Never raises.

    Idempotent: sanitize_question(sanitize_question(q)) == sanitize_question(q).
    """
    if not raw:
        return ""
    text = _CONTROL_CHARS_RE.sub(" ", str(raw))
    text = _WHITESPACE_RUN_RE.sub(" ", text).strip()
    return text[:MAX_QUESTION_CHARS]