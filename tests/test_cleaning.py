from masar.pipeline import cleaning as c


def test_city_normalization():
    assert c.normalize_city("Riyadh, Saudi Arabia") == "Riyadh"
    assert c.normalize_city("الرياض") == "Riyadh"
    assert c.normalize_city("Al Jubail") == "Jubail"
    assert c.normalize_city(None, "Jeddah - Makkah Region") == "Jeddah"
    assert c.normalize_city("Saudi Arabia") == "Unknown"
    assert c.normalize_city(None) == "Unknown"


def test_non_saudi():
    assert c.is_non_saudi("Dubai", None, None)
    assert c.is_non_saudi(None, "United Arab Emirates", None)
    assert not c.is_non_saudi("Riyadh", "Saudi Arabia", None)
    assert not c.is_non_saudi("Riyadh", None, None)


def test_work_mode_unknown_is_not_onsite():
    assert c.map_work_mode(None) == "Unknown"
    assert c.map_work_mode("HYBRID") == "Hybrid"
    assert c.map_work_mode("عن بعد") == "Remote"
    assert c.map_work_mode("On-site") == "On-site"


def test_employment_and_qualification():
    assert c.map_employment_type("FULL_TIME") == "Full-time"
    assert c.map_employment_type("دوام كامل") == "Full-time"
    assert c.map_qualification("بكالوريوس محاسبة") == "Bachelor's Degree"
    assert c.map_qualification(None) == "Unknown"


def test_experience():
    assert c.parse_experience("2 - 4 Years") == (2, 4)
    assert c.parse_experience("5+ years") == (5, None)
    assert c.parse_experience(3) == (3, 3)
    assert c.parse_experience(None) == (None, None)


def test_salary():
    s = c.parse_salary("7,000 - 9,000 SAR per month")
    assert (s["salary_min"], s["salary_max"], s["salary_currency"], s["salary_period"]) == (7000, 9000, "SAR", "month")
    assert c.parse_salary(None, 0, None, "SAR")["salary_min"] is None      # "0" is not a salary
    assert c.parse_salary(None, 8000, 12000, "SAR", "Monthly")["salary_max"] == 12000


def test_skills_split_on_semicolon():
    assert c.split_skills("SQL; Power BI; Excel") == ["SQL", "Power BI", "Excel"]
    assert c.split_skills("Excel, excel , SAP") == ["Excel", "SAP"]
    assert c.split_skills(None) == []


def test_dates_and_ids():
    assert c.parse_datetime("2026-10-01T08:00:00Z").isoformat() == "2026-10-01T08:00:00"
    assert c.parse_datetime(1790000000).year == 2026
    assert c.parse_datetime("garbage") is None
    assert c.stable_id("Sabbar", "https://x") == c.stable_id("sabbar", "https://x ")
