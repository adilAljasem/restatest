"""Gregorian -> Hijri (Umm al-Qura) conversion.

Uses the ``hijridate`` package (accurate Umm al-Qura tables) when available,
and falls back to the arithmetical (tabular) Islamic calendar, which can be
off by one day.
"""
import datetime
import math

try:
    from hijridate import Gregorian
except ImportError:  # pragma: no cover - fallback path
    Gregorian = None

HIJRI_MONTHS_AR = [
    'محرم', 'صفر', 'ربيع الأول', 'ربيع الآخر', 'جمادى الأولى', 'جمادى الآخرة',
    'رجب', 'شعبان', 'رمضان', 'شوال', 'ذو القعدة', 'ذو الحجة',
]


def _tabular(date):
    jd = date.toordinal() + 1721424.5
    jd = math.floor(jd) + 0.5
    year = math.floor((30 * (jd - 1948439.5) + 10646) / 10631)
    month = min(12, math.ceil((jd - (29 + _hijri_to_jd(year, 1, 1))) / 29.5) + 1)
    day = int(jd - _hijri_to_jd(year, month, 1) + 1)
    return int(year), int(month), day


def _hijri_to_jd(year, month, day):
    return (day + math.ceil(29.5 * (month - 1)) + (year - 1) * 354
            + math.floor((3 + 11 * year) / 30) + 1948439.5 - 1)


def to_hijri(date):
    """Return (year, month, day) or None."""
    if not date:
        return None
    if isinstance(date, datetime.datetime):
        date = date.date()
    if Gregorian is not None:
        try:
            h = Gregorian(date.year, date.month, date.day).to_hijri()
            return h.year, h.month, h.day
        except (ValueError, OverflowError):
            pass
    return _tabular(date)


def to_hijri_str(date, lang='ar'):
    parts = to_hijri(date)
    if not parts:
        return ''
    year, month, day = parts
    if lang and lang.startswith('ar'):
        return '%d %s %dهـ' % (day, HIJRI_MONTHS_AR[month - 1], year)
    return '%02d/%02d/%d AH' % (day, month, year)
