"""
Ready-to-post content for the Masar X (Twitter) account, rebuilt every run.

Writes <site>/social.json: a few short Arabic posts built only from today's
real numbers (open jobs, fields, skills, companies, salaries). The page
share.html shows each post with a copy button and an image card to download.

SITE_URL (env) is the link put in the posts (default: the Cloudflare site).
"""
from __future__ import annotations

import os
from collections import Counter
from datetime import date, timedelta

from masar import storage

MAX_POST = 280
CITY_AR = {"Riyadh": "الرياض", "Jeddah": "جدة", "Makkah": "مكة المكرمة", "Medina": "المدينة المنورة",
           "Dammam": "الدمام", "Khobar": "الخبر", "Jubail": "الجبيل", "Yanbu": "ينبع", "Tabuk": "تبوك"}


def site_url() -> str:
    return (os.environ.get("SITE_URL") or "https://masar-e94.pages.dev").rstrip("/")


def _day(key) -> date | None:
    s = str(key or "")
    return date(int(s[:4]), int(s[4:6]), int(s[6:8])) if len(s) == 8 and s.isdigit() else None


def fmt(n) -> str:
    return f"{int(round(n)):,}"


def fit(text: str) -> str:
    """Keep a post under X's limit by dropping list lines from the end."""
    lines = text.split("\n")
    while len("\n".join(lines)) > MAX_POST and len(lines) > 3:
        lines.pop(-3)          # keep the last two lines (source line + link)
    return "\n".join(lines)


def build(jobs: list[dict], fields: dict, today: date | None = None) -> dict:
    url = site_url()
    days = [d for d in (_day(j.get("posted_date_key")) for j in jobs) if d]
    newest = max(days) if days else (today or date.today())
    week = [j for j in jobs if (_day(j.get("posted_date_key")) or date.min) > newest - timedelta(days=7)]
    names = {f["id"]: f["name_ar"] for f in fields.get("fields", [])}
    posts = []

    # 1. Daily pulse
    field_week = Counter(j.get("job_field") for j in week if j.get("job_field") not in (None, "other"))
    cities = Counter(j.get("city") for j in jobs if j.get("city") not in (None, "Unknown"))
    modes = Counter(j.get("work_mode") for j in jobs if j.get("work_mode") not in (None, "Unknown"))
    remote = 100 * (modes["Remote"] + modes["Hybrid"]) / max(sum(modes.values()), 1)
    if week and field_week and cities:
        top_f, top_n = field_week.most_common(1)[0]
        city, city_n = cities.most_common(1)[0]
        share = round(100 * city_n / max(sum(cities.values()), 1))
        posts.append({
            "id": "pulse", "title": "سوق العمل هالأسبوع",
            "text": fit("\n".join([
                "وش صار في سوق العمل السعودي هالأسبوع؟",
                f"• {fmt(len(week))} وظيفة جديدة",
                f"• أكثر تخصص يوظف: {names.get(top_f, top_f)} ({fmt(top_n)} وظيفة)",
                f"• {CITY_AR.get(city, city)} فيها {share}% من الوظائف المفتوحة",
                f"• {round(remote)}% بس عن بُعد أو هجين",
                f"كل الوظائف في مكان واحد: {url}"])),
            "card": {"kind": "stats", "headline": "سوق العمل السعودي هالأسبوع",
                     "items": [[fmt(len(week)), "وظيفة جديدة"],
                               [names.get(top_f, top_f), "أكثر تخصص يوظف"],
                               [f"{share}%", f"من الوظائف في {CITY_AR.get(city, city)}"],
                               [f"{round(remote)}%", "عن بُعد أو هجين"]]},
        })

    # 2. Most requested skills (hard skills only)
    market = fields.get("market") or {}
    skills = [s for s in market.get("top_skills", []) if not s.get("soft")][:5]
    if len(skills) >= 3:
        posts.append({
            "id": "skills", "title": "المهارات الأكثر طلبًا",
            "text": fit("\n".join(
                ["أكثر المهارات طلبًا في إعلانات الوظائف بالسعودية الحين:"]
                + [f"{i}. {s['name']}: {s['pct']}%" for i, s in enumerate(skills, 1)]
                + [f"من {fmt(market.get('skills_basis', 0))} إعلان فيه وصف كامل.",
                   f"وش يطلب السوق في تخصصك؟ {url}/insights.html"])),
            "card": {"kind": "bars", "headline": "أكثر المهارات طلبًا",
                     "note": f"نسبة الإعلانات اللي تطلب المهارة · من {fmt(market.get('skills_basis', 0))} إعلان",
                     "items": [[s["name"], s["pct"]] for s in skills]},
        })

    # 3. Who is hiring this week
    comp = Counter(j.get("company_name") for j in week if j.get("company_name") not in (None, "Unknown"))
    top_c = comp.most_common(5)
    if len(top_c) >= 3:
        posts.append({
            "id": "companies", "title": "مين يوظف هالأسبوع",
            "text": fit("\n".join(
                ["أكثر الشركات نشرًا للوظائف هالأسبوع:"]
                + [f"{i}. {c} ({fmt(n)} وظيفة)" for i, (c, n) in enumerate(top_c, 1)]
                + ["", f"قدّم من هنا: {url}"])),
            "card": {"kind": "list", "headline": "مين يوظف هالأسبوع؟",
                     "items": [[c, f"{fmt(n)} وظيفة"] for c, n in top_c]},
        })

    # 4. Entry level
    entry = [j for j in jobs if j.get("experience_band") == "0-1 years (entry)"]
    if entry:
        titles = Counter(j.get("job_title") for j in entry).most_common(3)
        posts.append({
            "id": "entry", "title": "لحديثي التخرج",
            "text": fit("\n".join([
                f"حديث تخرج؟ فيه الحين {fmt(len(entry))} وظيفة مفتوحة تطلب خبرة 0-1 سنة.",
                "أكثر المسميات: " + "، ".join(t for t, _ in titles),
                f"شوفها كلها: {url}"])),
            "card": {"kind": "big", "headline": "وظائف لحديثي التخرج", "number": fmt(len(entry)),
                     "label": "وظيفة مفتوحة تطلب خبرة 0-1 سنة",
                     "sub": "أكثر المسميات: " + "، ".join(t for t, _ in titles)},
        })

    # 5. Salary of one field (rotates daily so the account doesn't repeat itself)
    with_salary = [f for f in fields.get("fields", []) if f.get("salary") and f["id"] != "other"]
    if with_salary:
        f = with_salary[newest.toordinal() % len(with_salary)]
        s = f["salary"]
        posts.append({
            "id": "salary", "title": f"رواتب {f['name_ar']}",
            "text": fit("\n".join([
                f"كم الراتب في {f['name_ar']}؟",
                f"المتوسط المعلن: {fmt(s['median'])} ريال شهريًا",
                f"وغالب الرواتب بين {fmt(s['p25'])} و {fmt(s['p75'])} ريال",
                f"من {fmt(s['sample'])} إعلان فيه راتب.",
                f"التحليل كامل: {url}/insights.html#{f['id']}"])),
            "card": {"kind": "big", "headline": f"الرواتب في {f['name_ar']}", "number": fmt(s["median"]),
                     "label": "ريال شهريًا · متوسط الراتب المعلن",
                     "sub": f"غالب الرواتب بين {fmt(s['p25'])} و {fmt(s['p75'])} ريال · من {fmt(s['sample'])} إعلان"},
        })

    # A link makes an X post cost ~13x more (pay-per-use API), so by default the
    # link line becomes "link in bio". X_POST_LINKS=1 keeps the real links.
    if os.environ.get("X_POST_LINKS", "0") != "1":
        for p in posts:
            p["text"] = "\n".join(
                "كل الوظائف والتحليل في موقع مسار، الرابط في البايو." if url in line else line
                for line in p["text"].split("\n"))
    # Hashtags: two general ones plus one per kind, added only if the post stays under the limit.
    tags = {"pulse": "#سوق_العمل", "skills": "#مهارات", "companies": "#توظيف", "entry": "#حديثي_التخرج",
            "salary": "#رواتب"}
    for p in posts:
        line = " ".join(["#وظائف", "#وظائف_السعودية", tags.get(p["id"], "")]).strip()
        if len(p["text"]) + len(line) + 1 <= MAX_POST:
            p["text"] += "\n" + line
        elif len(p["text"]) + 18 <= MAX_POST:
            p["text"] += "\n#وظائف_السعودية"
    # One post per day, rotating through the kinds so the account doesn't repeat itself.
    today_id = posts[newest.toordinal() % len(posts)]["id"] if posts else None
    return {"updated_at": storage.iso(), "date": newest.isoformat(), "site_url": url,
            "today": today_id, "posts": posts}
