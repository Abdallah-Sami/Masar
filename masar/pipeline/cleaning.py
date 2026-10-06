"""
Standardizers used by the Silver layer. All are pure functions (unit-tested).

Fixes vs the old notebooks:
- Skills are split on ';' (how scrapers store them) as well as ',' / '|'.
- City: Tanqeeb locations like "Riyadh, Saudi Arabia" now give "Riyadh"
  (the old code took the LAST part, producing "Saudi"); many spelling variants
  are merged; country-only values become "Unknown"; "Ha'Il" title-case bug fixed.
- HTML entities are decoded.
- Dates keep their timezone (converted to UTC) instead of being cut at 19 chars.
- Salary and experience are parsed into numbers (min/max) as well as text.
"""
from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timezone

from dateutil import parser as dateparser

EMPTY = {"", "n/a", "na", "nan", "none", "null", "not specified", "unknown", "-", "0"}


def is_empty(value) -> bool:
    return value is None or str(value).strip().lower() in EMPTY


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def clean_text(value) -> str | None:
    if value is None:
        return None
    text = html.unescape(_TAG_RE.sub(" ", str(value)))
    text = _WS_RE.sub(" ", text).strip()
    return text or None


def smart_title(text: str) -> str:
    """Title-case without the str.title() apostrophe bug (Ha'Il -> Ha'il)."""
    return " ".join(w[:1].upper() + w[1:].lower() if w else w for w in text.split(" "))


def clean_title(value) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    letters = re.sub(r"[^A-Za-z]", "", text)
    if len(letters) > 4 and letters.isupper():  # "SALES REPRESENTATIVE" -> "Sales Representative"
        text = smart_title(text)
    return text


def title_key(title: str | None) -> str:
    """Loose key for matching the same title across sources."""
    t = (title or "").lower()
    t = re.sub(r"\(.*?\)|\[.*?\]", " ", t)          # drop "(Riyadh)", "[Urgent]"
    t = re.sub(r"[^\w\s]", " ", t)
    return _WS_RE.sub(" ", t).strip()


def company_key(company: str | None) -> str:
    c = (company or "").lower()
    c = re.sub(r"[^\w\s]", " ", c)
    c = re.sub(r"\b(co|company|ltd|llc|est|inc|group|holding|for|trading)\b", " ", c)
    return _WS_RE.sub(" ", c).strip()


# ---------------------------------------------------------------------------
# City
# ---------------------------------------------------------------------------
_CITY_VARIANTS = {
    "Riyadh": ["الرياض", "رياض", "riyadh", "riyadh region", "ar riyadh", "al riyadh", "riyad"],
    "Ad Diriyah": ["الدرعية", "ad diriyah", "diriyah", "dir'iyah"],
    "Al Kharj": ["الخرج", "al kharj", "al-kharj", "kharj"],
    "Al Majmaah": ["المجمعة", "al majmaah", "majmaah"],
    "Ad Duwadimi": ["الدوادمي", "ad duwadimi", "dawadmi"],
    "Jeddah": ["جدة", "جده", "jeddah", "jedda", "jiddah"],
    "Makkah": ["مكة", "مكة المكرمة", "مكه", "makkah", "mecca", "makkah region", "makkah al mukarramah"],
    "Medina": ["المدينة", "المدينة المنورة", "medina", "madinah", "al madinah", "al madinah al munawwarah",
               "madinah al munawwarah", "al-madinah"],
    "Yanbu": ["ينبع", "yanbu", "yanbu al bahr"],
    "Taif": ["الطائف", "taif", "at taif", "al taif"],
    "Rabigh": ["رابغ", "rabigh"],
    "Al Ula": ["العلا", "al ula", "alula", "al-ula"],
    "Al Qunfudhah": ["القنفذة", "al qunfudhah", "al-qunfudhah", "al qunfidhah"],
    "King Abdullah Economic City": ["مدينة الملك عبدالله الاقتصادية", "king abdullah economic city", "kaec"],
    "Dammam": ["الدمام", "dammam", "al dammam", "ad dammam", "al damam"],
    "Khobar": ["الخبر", "khobar", "al khobar", "al-khobar", "alkhobar"],
    "Dhahran": ["الظهران", "dhahran", "al dhahran", "az zahran"],
    "Jubail": ["الجبيل", "jubail", "al jubail", "al jubayl", "jubayl", "al-jubail"],
    "Al Ahsa": ["الأحساء", "الاحساء", "al ahsa", "al-ahsa", "al hasa", "al-hasa", "hasa", "ahsa", "al ahsaa"],
    "Al Hofuf": ["الهفوف", "al hofuf", "hofuf", "al hafuf", "al-hafof", "hufuf"],
    "Al Mubarraz": ["المبرز", "al mubarraz", "mubarraz"],
    "Qatif": ["القطيف", "qatif", "al qatif"],
    "Abqaiq": ["بقيق", "abqaiq", "buqayq"],
    "Ras Tanura": ["رأس تنورة", "ras tanura", "ras tannurah"],
    "Al Khafji": ["الخفجي", "al khafji", "khafji", "al-khafgy"],
    "Hafar Al Batin": ["حفر الباطن", "hafar al batin", "hafar al-batin", "hafr al batin"],
    "Eastern Province": ["الشرقية", "المنطقة الشرقية", "eastern province", "eastern", "eastern region",
                         "ash sharqiyah", "sharqia"],
    "Qassim": ["القصيم", "qassim", "al qassim", "al-qassim", "qassim region", "al qassim region"],
    "Buraydah": ["بريدة", "buraydah", "buraidah", "buraidha"],
    "Unaizah": ["عنيزة", "unaizah", "onaizah"],
    "Abha": ["أبها", "ابها", "abha"],
    "Khamis Mushait": ["خميس مشيط", "khamis mushait", "khamis mushayt", "khamis mushait"],
    "Asir": ["عسير", "asir", "'asir", "aseer", "asir region"],
    "Bisha": ["بيشة", "bisha"],
    "Jizan": ["جازان", "جيزان", "jizan", "jazan", "gizan", "jazan region"],
    "Sabya": ["صبيا", "sabya"],
    "Najran": ["نجران", "najran"],
    "Al Bahah": ["الباحة", "al bahah", "al baha", "baha"],
    "Tabuk": ["تبوك", "tabuk", "tabok", "tabouk"],
    "Umluj": ["أملج", "املج", "umluj"],
    "NEOM": ["نيوم", "neom"],
    "Hail": ["حائل", "hail", "ha'il", "hail region"],
    "Al Jawf": ["الجوف", "al jawf", "al jouf", "jouf"],
    "Sakaka": ["سكاكا", "sakaka"],
    "Arar": ["عرعر", "arar"],
    "Al Qurayyat": ["القريات", "al qurayyat", "qurayyat"],
    "Northern Borders": ["الحدود الشمالية", "northern borders", "northern border"],
}
_COUNTRY_WORDS = {"saudi", "saudi arabia", "ksa", "kingdom of saudi arabia", "السعودية",
                  "المملكة العربية السعودية", "المملكة", "sa"}
_NON_SAUDI = {"cairo", "egypt", "giza", "alexandria", "dubai", "abu dhabi", "doha", "kuwait",
              "manama", "muscat", "amman", "uae", "united arab emirates", "qatar", "bahrain", "oman"}


def _norm_place(value: str) -> str:
    v = value.lower().strip()
    v = re.sub("[أإآ]", "ا", v).replace("ة", "ه")
    v = re.sub(r"[\-_`’'.]+", " ", v)
    return _WS_RE.sub(" ", v).strip()


CITY_LOOKUP = {_norm_place(variant): city for city, variants in _CITY_VARIANTS.items() for variant in variants}
CITY_LOOKUP.update({_norm_place(city): city for city in _CITY_VARIANTS})
KNOWN_CITIES = set(_CITY_VARIANTS)


def normalize_city(*candidates) -> str:
    """Return a canonical city from any of the given raw values, or 'Unknown'.

    Each candidate may hold several parts ("Riyadh, Saudi Arabia",
    "Jeddah - Makkah Region"); the first part that is a known city wins.
    """
    parts = []
    for cand in candidates:
        if is_empty(cand):
            continue
        parts.extend(p for p in re.split(r"[,\|/;،]| - ", str(cand)) if p.strip())

    for p in parts:
        key = _norm_place(p)
        if key in CITY_LOOKUP:
            return CITY_LOOKUP[key]
        stripped = re.sub(r"^(al|el|ar|ad|as|at|az)\s+", "", key)   # "al jubail" vs "jubail"
        if stripped in CITY_LOOKUP:
            return CITY_LOOKUP[stripped]

    for p in parts:
        key = _norm_place(p)
        if key and key not in _COUNTRY_WORDS and not is_empty(key):
            return smart_title(clean_text(p) or "")
    return "Unknown"


def is_non_saudi(city: str | None, country: str | None, url: str | None) -> bool:
    if url and "jobs-in-egypt" in url:
        return True
    c = _norm_place(country or "")
    if c and not any(w in c for w in ("saudi", "ksa", "سعود", "المملكه")) and c not in EMPTY:
        return True
    return _norm_place(city or "") in _NON_SAUDI


# ---------------------------------------------------------------------------
# Categorical
# ---------------------------------------------------------------------------
def map_work_mode(value) -> str:
    v = (str(value or "")).lower()
    if "remote" in v or "عن بعد" in v:
        return "Remote"
    if "hybrid" in v or "هجين" in v:
        return "Hybrid"
    if "field" in v or "ميدان" in v:
        return "Field"
    if is_empty(value):
        return "Unknown"     # old code defaulted to On-site, which inflated that number
    return "On-site"


def map_employment_type(value) -> str:
    v = (str(value or "")).lower().replace("_", " ")
    for needle, label in (("full", "Full-time"), ("دوام كامل", "Full-time"), ("part", "Part-time"),
                          ("دوام جزئي", "Part-time"), ("contract", "Contract"), ("عقد", "Contract"),
                          ("intern", "Internship"), ("تدريب", "Internship"), ("freelance", "Freelance"),
                          ("temporary", "Temporary"), ("seasonal", "Temporary"), ("flexible", "Part-time")):
        if needle in v:
            return label
    return "Unknown"


def map_qualification(value) -> str:
    v = (str(value or "")).lower()
    if re.search("ماجستير|master|دكتوراه|phd|doctor", v):
        return "Master's Degree / PhD"
    if re.search("بكالوريوس|bachelor|جامعي|university|degree", v):
        return "Bachelor's Degree"
    if re.search("دبلوم|diploma", v):
        return "Diploma"
    if re.search("ثانوي|high school|secondary", v):
        return "High School"
    return "Unknown"


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")


def _nums(text: str) -> list[float]:
    return [float(n.replace(",", "")) for n in _NUM_RE.findall(text)]


def parse_experience(value) -> tuple[float | None, float | None]:
    """'5-10' / '0 to 5 years' / '+10' / 3 -> (min, max)."""
    if value is None or is_empty(value) and str(value).strip() != "0":
        return None, None
    if isinstance(value, (int, float)):
        return float(value), float(value)
    text = str(value)
    nums = [n for n in _nums(text) if n <= 50]
    if not nums:
        return None, None
    if "+" in text or "more" in text.lower() or "أكثر" in text:
        return nums[0], None
    return (nums[0], nums[1]) if len(nums) >= 2 else (nums[0], nums[0])


def experience_band(min_years: float | None) -> str:
    if min_years is None:
        return "Unknown"
    if min_years < 2:
        return "0-1 years (entry)"
    if min_years < 5:
        return "2-4 years"
    if min_years < 10:
        return "5-9 years"
    return "10+ years"


def parse_salary(text=None, smin=None, smax=None, currency=None, period=None) -> dict:
    """Return salary_min/max/currency/period/text. Implausible numbers are dropped."""
    lo = _to_float(smin)
    hi = _to_float(smax)
    raw = clean_text(text)
    if lo is None and raw:
        nums = [n for n in _nums(raw) if n >= 100]
        if nums:
            lo, hi = nums[0], (nums[1] if len(nums) > 1 else None)
    if lo is not None and lo < 100:      # "0", "1" -> not a real salary
        lo = None
    if hi is not None and (hi < 100 or (lo is not None and hi < lo)):
        hi = None
    cur = (currency or ("SAR" if raw and re.search(r"sar|ريال|ر\.س", raw, re.I) else None))
    per = (str(period).lower() if period else None)
    if per is None and raw:
        per = "month" if re.search(r"month|شهر", raw, re.I) else ("year" if re.search(r"year|سنو", raw, re.I) else None)
    if lo is None:
        return {"salary_min": None, "salary_max": None, "salary_currency": None,
                "salary_period": None, "salary_text": raw}
    shown = f"{lo:,.0f}" + (f" - {hi:,.0f}" if hi and hi != lo else "") + (f" {cur}" if cur else "")
    return {"salary_min": lo, "salary_max": hi, "salary_currency": cur, "salary_period": per,
            "salary_text": raw or shown}


def _to_float(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------
_SKILL_SPLIT = re.compile(r"\s*[;,|•\n]\s*")


def split_skills(value) -> list[str]:
    """'Excel; Power BI, SQL' -> ['Excel', 'Power BI', 'SQL'] (deduped, junk removed)."""
    if is_empty(value):
        return []
    out, seen = [], set()
    for part in _SKILL_SPLIT.split(str(value)):
        s = clean_text(part)
        if not s or is_empty(s) or len(s) > 60:
            continue
        k = s.lower()
        if k not in seen:
            seen.add(k)
            out.append(s)
    return out


# ---------------------------------------------------------------------------
# Dates and ids
# ---------------------------------------------------------------------------
def parse_datetime(value) -> datetime | None:
    """Parse ISO strings, d/m/Y, epoch seconds/millis. Returns naive UTC."""
    if value is None or is_empty(value):
        return None
    if isinstance(value, (int, float)) or re.fullmatch(r"\d{10}(\d{3})?", str(value).strip()):
        n = float(value)
        dt = datetime.fromtimestamp(n / 1000 if n > 1e11 else n, tz=timezone.utc)
        return dt.replace(tzinfo=None)
    text = str(value).strip()
    try:
        dayfirst = bool(re.match(r"^\d{1,2}/\d{1,2}/\d{4}", text)) and int(text.split("/")[0]) > 12
        dt = dateparser.parse(text, dayfirst=dayfirst)
    except (ValueError, OverflowError):
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    if not (2000 <= dt.year <= 2100):
        return None
    return dt


def stable_id(*parts) -> str:
    key = "|".join(str(p or "").strip().lower() for p in parts)
    return hashlib.md5(key.encode("utf-8")).hexdigest()[:16]
