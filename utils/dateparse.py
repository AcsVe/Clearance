"""Parse the date strings found in the Google Sheets exports.

The Apps Script wrote dates with ``new Date().toLocaleDateString('ar-SA')``,
which produces Umm al-Qura (Hijri) dates in Arabic-Indic digits with
right-to-left marks, e.g. ``"١٣‏/٤‏/١٤٤٨ هـ"``. Resign dates were real Date
cells, which a CSV export writes in the spreadsheet's locale format.
"""
import re
from datetime import datetime, date, time, timezone
from zoneinfo import ZoneInfo

_DIGITS = str.maketrans('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹', '01234567890123456789')
_MARKS = re.compile('[‎‏؜‪-‮⁦-⁩]')


class ParsedDate:
    def __init__(self, value=None, kind=None, warning=None):
        self.value, self.kind, self.warning = value, kind, warning   # value: date


def normalize(text):
    s = _MARKS.sub('', str(text or '')).translate(_DIGITS)
    return s.replace('،', ',').strip()


def parse_sheet_date(text, order='dmy'):
    raw = normalize(text)
    if not raw:
        return ParsedDate()
    hijri_marker = raw.rstrip('. ').endswith(('هـ', 'ه'))
    s = re.sub(r'\s*(هـ|ه|م)\.?\s*$', '', raw).strip()

    m = re.match(r'^(\d{4})-(\d{1,2})-(\d{1,2})', s)                 # ISO
    if m:
        y, mo, d = map(int, m.groups())
        return _maybe_hijri(y, mo, d, 'iso')

    m = re.match(r'^(\d{1,4})[/.\-](\d{1,2})[/.\-](\d{1,4})', s)
    if m:
        a, b, c = map(int, m.groups())
        if a > 999:                                                  # y/m/d
            y, mo, d = a, b, c
        else:                                                        # d/m/y or m/d/y
            y = c
            if order == 'mdy':
                mo, d = a, b
            else:
                d, mo = a, b
            if mo > 12 and d <= 12:                                   # obviously swapped
                d, mo = mo, d
        if y < 100:
            y += 2000
        if hijri_marker or 1300 <= y <= 1500:
            return _hijri(y, mo, d)
        res = _gregorian(y, mo, d, 'gregorian')
        if res.value and a <= 12 and b <= 12 and a != b and a <= 999:
            res.warning = 'تاريخ ملتبس (يوم/شهر) — فُسّر حسب الإعداد'
        return res

    for fmt in ('%a %b %d %Y %H:%M:%S', '%a %b %d %Y', '%d %b %Y', '%b %d, %Y'):
        try:
            return ParsedDate(datetime.strptime(s[:24].strip(), fmt).date(), 'text')
        except ValueError:
            continue
    return ParsedDate(None, None, f'صيغة تاريخ غير معروفة: {raw}')


def _maybe_hijri(y, mo, d, kind):
    if 1300 <= y <= 1500:
        return _hijri(y, mo, d)
    return _gregorian(y, mo, d, kind)


def _gregorian(y, mo, d, kind):
    try:
        return ParsedDate(date(y, mo, d), kind)
    except ValueError:
        return ParsedDate(None, None, f'تاريخ غير صالح: {y}-{mo}-{d}')


def _hijri(y, mo, d):
    try:
        from hijridate import Hijri
        g = Hijri(y, mo, d).to_gregorian()
        return ParsedDate(date(g.year, g.month, g.day), 'hijri')
    except Exception as e:
        return ParsedDate(None, None, f'تاريخ هجري غير صالح {d}/{mo}/{y}: {e}')


def date_to_utc_noon(d, tz_name='Asia/Amman'):
    """Store a date-only value as local noon converted to naive UTC, so it
    shows as the same calendar day in any nearby timezone."""
    local = datetime.combine(d, time(12, 0), tzinfo=ZoneInfo(tz_name))
    return local.astimezone(timezone.utc).replace(tzinfo=None)
