from masar.pipeline.taxonomy import FIELD_NAMES, extract_skills, job_field


def test_fields():
    cases = {
        "Data Analyst": "data_ai", "Sales Engineer": "sales", "Key Account Manager": "sales",
        "محاسب": "finance_accounting", "Accountant": "finance_accounting", "HSE Engineer": "hse",
        "Civil Engineer": "engineering", "HVAC Technician": "construction_trades", "Barista": "hospitality",
        "محضر قهوة باريستا": "hospitality", "Pharmacist": "healthcare", "حارس أمن": "security_drivers",
        "Document Controller": "project_management", "Salesforce Developer": "software_it",
        "Logistics Coordinator": "logistics", "Data Entry Agent": "admin", "General Manager": "management",
        "": "other", None: "other",
    }
    for title, field in cases.items():
        assert job_field(title) == field, title
    assert all(f in FIELD_NAMES for f in set(cases.values()))


def test_skills_from_text():
    got = extract_skills("Data Analyst", "Strong SQL and Python, Power BI dashboards. خبرة في تحليل البيانات. JavaScript is a plus.")
    assert {"SQL", "Python", "Power BI", "Data Analysis", "JavaScript"} <= set(got)
    assert "Java" not in got                 # "JavaScript" must not count as Java
    assert extract_skills("Driver", "Valid Saudi driving license") == ["Driving License"]
    assert extract_skills(None, None) == []
