"""Shared diff utilities — parsing and generation.

Used by both the revision review surface (``routes/revisions.py``) and the
post compare view (``routes/posts.py``) so that the same parsing logic drives
the same diff-viewer partial template in both contexts.
"""

from __future__ import annotations

import difflib
import re

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)")


def compute_diff(old_text: str, new_text: str, *, context: int = 3) -> str:
    """Return a unified diff string comparing *old_text* to *new_text*.

    Parameters
    ----------
    old_text:
        The "before" content.
    new_text:
        The "after" content.
    context:
        Number of surrounding unchanged lines to include around each change
        hunk (passed straight to :func:`difflib.unified_diff`).
    """
    old_lines = (old_text or "").splitlines(keepends=True)
    new_lines = (new_text or "").splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile="original",
        tofile="proposed",
        lineterm="\n",  # ensure header lines (---, +++, @@) are newline-terminated
        n=context,
    )
    return "".join(diff)


def parse_diff_lines(diff_text: str) -> list[dict]:
    """Parse a unified diff string into structured line objects for the diff-viewer template.

    Each element in the returned list is a dict with keys:

    ``sign``
        ``'+'``, ``'-'``, or ``' '`` (context).
    ``content``
        The line text (without the leading sign character).
    ``kind``
        ``'add'``, ``'del'``, ``'ctx'``, or ``'hunk'``.
    ``is_hunk``
        ``True`` when this entry represents a ``@@`` hunk header.
    ``old_num``
        1-based line number in the original file, or ``None`` for add/hunk rows.
    ``new_num``
        1-based line number in the new file, or ``None`` for del/hunk rows.
    """
    lines: list[dict] = []
    old_num = 0
    new_num = 0
    for raw in (diff_text or "").splitlines():
        if raw.startswith("+++") or raw.startswith("---"):
            continue  # skip file-header lines
        if raw.startswith("@@"):
            m = _HUNK_RE.match(raw)
            if m:
                old_num = int(m.group(1))
                new_num = int(m.group(2))
            lines.append(
                {
                    "sign": "",
                    "content": raw,
                    "kind": "hunk",
                    "is_hunk": True,
                    "old_num": None,
                    "new_num": None,
                }
            )
        elif raw.startswith("+"):
            lines.append(
                {
                    "sign": "+",
                    "content": raw[1:],
                    "kind": "add",
                    "is_hunk": False,
                    "old_num": None,
                    "new_num": new_num,
                }
            )
            new_num += 1
        elif raw.startswith("-"):
            lines.append(
                {
                    "sign": "-",
                    "content": raw[1:],
                    "kind": "del",
                    "is_hunk": False,
                    "old_num": old_num,
                    "new_num": None,
                }
            )
            old_num += 1
        else:
            lines.append(
                {
                    "sign": " ",
                    "content": raw[1:] if raw else "",
                    "kind": "ctx",
                    "is_hunk": False,
                    "old_num": old_num,
                    "new_num": new_num,
                }
            )
            old_num += 1
            new_num += 1
    return lines
