"""Exclusive research sessions. Not a venue holiday calendar."""

from datetime import UTC, datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

NAMES = ("Asia", "London", "London-NY", "New York", "Sydney")
Z = {
    n: ZoneInfo(z)
    for n, z in [
        ("Asia", "Asia/Tokyo"),
        ("London", "Europe/London"),
        ("New York", "America/New_York"),
        ("Sydney", "Australia/Sydney"),
    ]
}


@lru_cache(maxsize=8192)
def _session(minute):
    t = datetime.fromtimestamp(minute * 60, UTC)
    if t.weekday() >= 5 or not 25 <= minute % 1440 < 1438:
        return ""
    ny = t.astimezone(Z["New York"])
    m = 60 * ny.hour + ny.minute
    if 16 * 60 + 45 <= m < 18 * 60 + 15:
        return ""

    def active(n, lo, hi):
        loc = t.astimezone(Z[n])
        return loc.weekday() < 5 and lo <= loc.hour < hi

    london = active("London", 8, 17)
    new = active("New York", 8, 17)
    if london and new:
        return "London-NY"
    if london:
        return "London"
    if new:
        return "New York"
    if active("Asia", 9, 17):
        return "Asia"
    if active("Sydney", 8, 17):
        return "Sydney"
    return ""


def session(ms):
    return _session(int(ms) // 60000)
