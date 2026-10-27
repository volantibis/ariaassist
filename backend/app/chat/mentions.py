"""Detect and strip @Aria mentions."""

from __future__ import annotations

import re

MENTION_RE = re.compile(r"(?<![\w.])@aria\b[,:]?\s*", re.IGNORECASE)


def is_mention(text: str) -> bool:
    """True for '@Aria hi' but not for 'bob@aria.com' or '@ariana'."""
    return bool(MENTION_RE.search(text))


def strip_mention(text: str) -> str:
    return MENTION_RE.sub("", text).strip()
