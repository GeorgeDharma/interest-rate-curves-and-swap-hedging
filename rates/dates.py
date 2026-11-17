"""Date rules used by the EUR case. Years before 2000 are outside its scope."""

from datetime import date, timedelta
from calendar import monthrange
from functools import lru_cache
import re


@lru_cache(None)
def easter_sunday(year):
    """Gregorian computus, used only to locate TARGET Easter holidays."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def business_day(d):
    if d.year < 2000:
        raise ValueError("Calendar supports years 2000 onward.")
    fixed = {(1, 1), (5, 1), (12, 25), (12, 26)}
    easter = easter_sunday(d.year)
    return (
        d.weekday() < 5
        and (d.month, d.day) not in fixed
        and d not in {easter - timedelta(days=2), easter + timedelta(days=1)}
        and d != date(2001, 12, 31)
    )


def advance_business(d, n):
    step = 1 if n >= 0 else -1
    for _ in range(abs(n)):
        d += timedelta(days=step)
        while not business_day(d):
            d += timedelta(days=step)
    return d


def adjust(d, convention="MF"):
    if convention not in {"MF", "Following"}:
        raise ValueError(convention)
    if business_day(d):
        return d
    later = d
    while not business_day(later):
        later += timedelta(days=1)
    if convention == "MF" and later.month != d.month:
        earlier = d
        while not business_day(earlier):
            earlier -= timedelta(days=1)
        return earlier
    if convention not in {"MF", "Following"}:
        raise ValueError(convention)
    return later


def add_months(d, n):
    months = d.year * 12 + d.month - 1 + n
    year, month = divmod(months, 12)
    month += 1
    return date(year, month, min(d.day, monthrange(year, month)[1]))


def add_tenor(d, tenor):
    parts = re.findall(r"(\d+)([DWMY])", tenor)
    if "".join(n + u for n, u in parts) != tenor:
        raise ValueError(tenor)
    months = sum(int(n) * (12 if u == "Y" else 1) for n, u in parts if u in "MY")
    days = sum(int(n) * (7 if u == "W" else 1) for n, u in parts if u in "DW")
    return add_months(d, months) + timedelta(days=days)


def year_fraction(a, b, rule="ACT360"):
    if b < a:
        raise ValueError("Period end precedes its start.")
    if rule == "ACT360":
        return (b - a).days / 360
    if rule == "ACT365":
        return (b - a).days / 365
    if rule != "30U360":
        raise ValueError(rule)
    # US 30/360 as specified by the source's plain "30/360" convention.
    da, db = a.day, b.day
    last_feb_a = a.month == 2 and da == monthrange(a.year, 2)[1]
    last_feb_b = b.month == 2 and db == monthrange(b.year, 2)[1]
    if last_feb_a and last_feb_b:
        db = 30
    if last_feb_a:
        da = 30
    if da == 31:
        da = 30
    if db == 31 and da >= 30:
        db = 30
    return (360 * (b.year - a.year) + 30 * (b.month - a.month) + db - da) / 360


def schedule(
    start, raw_end, months=12, convention="MF", payment_lag=0, day_count="30U360"
):
    """Backward schedule; each accrual boundary is adjusted before day counting."""
    if raw_end <= start or months <= 0 or payment_lag < 0:
        raise ValueError("Invalid schedule boundaries or interval.")
    raw = [raw_end]
    n = 1
    while add_months(raw_end, -months * n) > start:
        raw.append(add_months(raw_end, -months * n))
        n += 1
    raw.append(start)
    dates = [adjust(d, convention) for d in reversed(raw)]
    if len(set(dates)) != len(dates):
        raise ValueError("Collapsed accrual period.")
    return [
        (a, b, advance_business(b, payment_lag), year_fraction(a, b, day_count))
        for a, b in zip(dates[:-1], dates[1:])
    ]
