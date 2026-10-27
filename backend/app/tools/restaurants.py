"""Mock restaurant directory: search, fuzzy lookup, opening-hours and capacity checks."""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "restaurants.json"
PRICE_LABEL = {1: "$", 2: "$$", 3: "$$$"}


@dataclass(frozen=True)
class Restaurant:
    id: str
    name: str
    cuisine: str
    area: str
    price: int  # 1..3
    rating: float
    capacity: int  # largest party that can be booked
    opens: str  # "HH:MM" 24h
    closes: str  # "HH:MM"; "00:00" means midnight
    tags: tuple[str, ...] = ()

    def describe(self) -> str:
        return (
            f"{self.name} ({self.cuisine}, {self.area.title()}, {PRICE_LABEL[self.price]}, "
            f"rated {self.rating}, open {self.opens}-{self.closes})"
        )


@lru_cache(maxsize=1)
def load_restaurants(path: Path = DATA_FILE) -> tuple[Restaurant, ...]:
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    return tuple(Restaurant(**{**r, "tags": tuple(r.get("tags", []))}) for r in rows)


def search_restaurants(
    *,
    cuisine: str | None = None,
    area: str | None = None,
    max_price: int | None = None,
    party_size: int | None = None,
    limit: int = 3,
) -> list[Restaurant]:
    """Filter by whichever criteria are given, best rated first."""
    results = []
    for r in load_restaurants():
        if cuisine and cuisine.lower() not in (r.cuisine, *r.tags) and cuisine.lower() not in r.cuisine:
            continue
        if area and area.lower() not in r.area:
            continue
        if max_price and r.price > max_price:
            continue
        if party_size and party_size > r.capacity:
            continue
        results.append(r)
    return sorted(results, key=lambda r: r.rating, reverse=True)[:limit]


def get_by_id(restaurant_id: str) -> Restaurant | None:
    return next((r for r in load_restaurants() if r.id == restaurant_id), None)


def find_by_name(name: str) -> Restaurant | None:
    """Exact, substring, then fuzzy match on the restaurant name."""
    needle = name.strip().lower()
    if not needle:
        return None
    by_name = {r.name.lower(): r for r in load_restaurants()}
    if needle in by_name:
        return by_name[needle]
    for key, r in by_name.items():
        if needle in key or key in needle:
            return r
    close = difflib.get_close_matches(needle, list(by_name), n=1, cutoff=0.6)
    return by_name[close[0]] if close else None


_TIME_RE = re.compile(r"^\s*(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?\s*$", re.IGNORECASE)


def parse_time(text: str) -> str | None:
    """Normalise '8pm', '8:30 pm', '20:00' to 'HH:MM'. Returns None if unparseable.

    Without am/pm, hours 1-10 are read as evening (restaurants are mostly booked for
    dinner), so '8' becomes 20:00 while '11' and '12' stay as written.
    """
    m = _TIME_RE.match(text or "")
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2) or 0)
    meridiem = (m.group(3) or "").lower().replace(".", "")
    if minute > 59 or hour > 24:
        return None
    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    elif not meridiem and 1 <= hour <= 10:
        hour += 12
    if hour == 24:
        hour = 0
    return f"{hour:02d}:{minute:02d}"


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def is_open_at(r: Restaurant, hhmm: str) -> bool:
    start, end, t = _minutes(r.opens), _minutes(r.closes), _minutes(hhmm)
    if end == 0:
        end = 24 * 60  # "00:00" closing means midnight
    return start <= t < end
