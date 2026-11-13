"""Pull Python source out of a model reply."""
from __future__ import annotations

import ast
import re

_FENCE = re.compile(r"```[ \t]*([A-Za-z0-9_+.-]*)[^\n]*\n(.*?)```", re.DOTALL)


def _parses(src: str) -> bool:
    try:
        ast.parse(src)
    except (SyntaxError, ValueError):
        return False
    return True


def extract_code(text: str | None) -> str | None:
    """Return the Python source in `text`, or None.

    Prefers fenced blocks tagged python/py, then untagged ones; among those it
    returns the longest that parses (one defining `class Forecaster` wins).
    With no fence, the whole reply is returned if it parses as Python and
    contains at least one statement that is not a bare expression (so plain
    prose that happens to parse, like "ok", is not mistaken for code)."""
    if not text or not text.strip():
        return None
    blocks = [(lang.lower(), body.strip("\n")) for lang, body in _FENCE.findall(text)]
    if blocks:
        candidates = [b for lang, b in blocks if lang in ("python", "py", "python3")] or \
                     [b for lang, b in blocks if lang == ""]
        good = [b for b in candidates if b.strip() and _parses(b)]
        if not good:
            return None
        with_cls = [b for b in good if re.search(r"^class\s+Forecaster\b", b, re.M)]
        return max(with_cls or good, key=len) + "\n"
    src = text.strip()
    if not _parses(src):
        return None
    tree = ast.parse(src)
    if not any(not isinstance(node, ast.Expr) for node in tree.body):
        return None
    return src + "\n"
