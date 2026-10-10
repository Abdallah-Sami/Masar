"""
Field (job family) classification and skill extraction.

* job_field(title)                -> field id, e.g. "data_ai", or "other"
* extract_skills(title, text)     -> skills found in the title and description

Both are plain keyword rules, so they are fast, explainable and easy to
extend: add a keyword to a field or a skill below and the next pipeline run
applies it to all history (silver is rebuilt every run).
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Fields. Order matters: the first field whose keyword matches the title wins,
# so specific fields come before broad ones (e.g. "sales engineer" -> sales).
# (id, English name, Arabic name, English keywords, Arabic keywords)
# ---------------------------------------------------------------------------
FIELDS = [
    ("cybersecurity", "Cybersecurity", "الأمن السيبراني",
     ["cyber", "security analyst", "soc analyst", "penetration", "infosec", "information security", "grc"],
     ["أمن سيبراني", "الأمن السيبراني", "امن المعلومات", "أمن المعلومات"]),
    ("security_drivers", "Security & Driving", "الأمن والقيادة",
     ["security guard", "security officer", "guard", "cctv", "driver", "chauffeur", "lifeguard", "life guard",
      "security supervisor", "security cameras"],
     ["حارس", "أمن", "سائق", "حراسة", "منقذ"]),
    ("data_ai", "Data & AI", "البيانات والذكاء الاصطناعي",
     ["data analyst", "data scientist", "data engineer", "data architect", "business intelligence", "bi developer",
      "bi analyst", "machine learning", "ml engineer", "ai engineer", "artificial intelligence",
      "analytics", "big data", "data management", "data governance", "power bi"],
     ["محلل بيانات", "علم البيانات", "مهندس بيانات", "ذكاء اصطناعي"]),
    ("software_it", "Software & IT", "البرمجة وتقنية المعلومات",
     ["software", "developer", "programmer", "devops", "front end", "frontend", "back end", "backend", "full stack",
      "fullstack", "mobile app", "ios", "android", "web developer", "it ", "it-", "information technology",
      "system administrator", "systems engineer", "network", "erp", "sap ", "oracle", "cloud", "qa engineer",
      "test engineer", "technical support", "helpdesk", "help desk", "it support", "middleware", "database",
      "solution architect", "product manager", "scrum", "ui/ux", "ux", "odoo", "salesforce", "computing",
      "technology support", "disaster recovery"],
     ["مبرمج", "مطور", "تقنية المعلومات", "دعم فني", "شبكات", "تقنية معلومات"]),
    ("sales", "Sales & Business Development", "المبيعات وتطوير الأعمال",
     ["sales", "seller", "business development", "account manager", "account executive", "key account",
      "commercial", "merchandiser", "salesman", "pre-sales", "presales", "preseller", "telesales", "retail",
      "store manager", "shop"],
     ["مبيعات", "مندوب", "بائع", "بائعة", "تطوير أعمال", "تطوير الأعمال", "تسويق ومبيعات"]),
    ("hse", "Health, Safety & Environment", "الصحة والسلامة والبيئة",
     ["hse", "hsse", "qhse", "safety", "osh ", "osh engineer", "occupational health", "environmental", "fire and life"],
     ["سلامة", "صحة وسلامة", "السلامة المهنية"]),
    ("healthcare", "Healthcare & Medical", "الرعاية الصحية والطب",
     ["nurse", "nursing", "doctor", "physician", "medicine", "dentist", "dental", "pharmac", "medical", "clinical",
      "radiograph", "laborator", "physiotherap", "therapist", "paramedic", "surgeon", "anesthe", "pediatric",
      "cardio", "optometr", "midwife", "nutritionist", "dietitian", "psycholog", "speech", "patient", "hospital",
      "health", "dermatolog", "orthodont", "caregiver", "social worker"],
     ["طبيب", "ممرض", "تمريض", "صيدل", "أسنان", "اسنان", "مختبر", "أشعة", "اشعة", "علاج طبيعي", "طبي", "نفسي"]),
    ("engineering", "Engineering", "الهندسة",
     ["engineer", "engineering", "architect", "quantity surveyor", "surveyor", "draftsman", "drafter", "autocad",
      "structural", "geotechnical", "piping", "commissioning", "instrumentation", "inspector", "quality",
      "geolog", "gis ", "bim ", "qa/qc", "qc "],
     ["مهندس", "هندسة", "معماري", "مساح"]),
    ("construction_trades", "Technicians & Trades", "الفنيين والحرف",
     ["technician", "electrician", "plumber", "welder", "welding", "carpenter", "mechanic", "foreman", "hvac",
      "maintenance", "operator", "fitter", "painter", "mason", "installer", "elevator", "rigger", "scaffold",
      "site supervisor", "construction", "civil works", "facilities", "facility", "tailor", "production supervisor",
      "technical supervisor"],
     ["فني", "كهربائي", "سباك", "لحام", "نجار", "ميكانيكي", "صيانة", "مشغل", "تكييف", "عامل", "إنتاج", "انتاج", "خياط"]),
    ("project_management", "Project Management", "إدارة المشاريع",
     ["project manager", "project director", "project coordinator", "project control", "pmo", "planning",
      "planner", "program manager", "portfolio", "document control", "document controller", "cost control",
      "scheduler", "contracts"],
     ["مدير مشروع", "مدير مشاريع", "إدارة مشاريع", "ادارة مشاريع", "تخطيط", "مراقب وثائق"]),
    ("finance_accounting", "Finance & Accounting", "المالية والمحاسبة",
     ["account", "finance", "financial", "auditor", "audit", "treasur", "tax", "zakat", "payroll", "cashier",
      "controller", "billing", "collection", "credit", "budget", "investment", "banking", "aml", "risk", "insurance"],
     ["محاسب", "مالي", "مالية", "مراجع", "تدقيق", "خزينة", "رواتب", "صندوق", "تحصيل", "ائتمان", "استثمار"]),
    ("marketing", "Marketing & Communications", "التسويق والاتصال",
     ["marketing", "brand", "social media", "content", "copywriter", "public relations", "pr ", "communications",
      "seo", "digital media", "media buyer", "advertising", "campaign", "growth", "crm", "promoter"],
     ["تسويق", "علاقات عامة", "محتوى", "إعلام", "اعلام", "تواصل اجتماعي", "مسوق"]),
    ("design_creative", "Design & Creative", "التصميم والإبداع",
     ["designer", "graphic", "photographer", "videographer", "video editor", "motion", "illustrator", "interior",
      "fashion", "art director", "creative", "animator", "3d"],
     ["مصمم", "تصميم", "مصور", "مونتاج"]),
    ("hr", "Human Resources", "الموارد البشرية",
     ["human resources", "hr ", "hr-", "hrbp", "recruit", "talent", "people ", "personnel", "training",
      "learning & development", "learning and development", "payroll specialist", "compensation", "government relations"],
     ["موارد بشرية", "الموارد البشرية", "توظيف", "تدريب", "شؤون الموظفين", "علاقات حكومية"]),
    ("customer_service", "Customer Service", "خدمة العملاء",
     ["customer service", "customer services", "customer support", "customer experience", "call center",
      "call centre", "contact center", "client service", "guest relations", "guest experience", "complaints",
      "passenger service", "customer enquiry", "at your service", "order taker"],
     ["خدمة العملاء", "خدمة عملاء", "مركز اتصال", "اتصالات العملاء"]),
    ("logistics", "Logistics & Supply Chain", "اللوجستيات وسلاسل الإمداد",
     ["logistic", "supply chain", "procurement", "purchas", "buyer", "warehouse", "storekeeper", "store keeper",
      "inventory", "shipping", "freight", "customs", "import", "export", "fleet", "dispatcher", "courier",
      "material", "expedit", "delivery"],
     ["مشتريات", "مستودع", "مخزون", "مخازن", "شحن", "لوجست", "توريد", "جمارك", "أمين مستودع"]),
    ("admin", "Administration & Office", "الإدارة والسكرتارية",
     ["administrative", "admin ", "admin.", "secretary", "office manager", "office assistant", "executive assistant",
      "receptionist", "clerk", "coordinator", "personal assistant", "pa ", "front desk", "data entry", "archiv",
      "administrator", "translator", "invoice"],
     ["إداري", "اداري", "سكرتير", "استقبال", "كاتب", "منسق", "مساعد إداري", "إدخال بيانات", "ادخال بيانات", "سنترال", "مترجم"]),
    ("hospitality", "Hospitality & Food Service", "الضيافة والمطاعم",
     ["chef", "cook", "commis", "barista", "waiter", "waitress", "server", "bartender", "hostess", "host ",
      "restaurant", "f&b", "food", "kitchen", "pastry", "bakery", "hotel", "housekeeping", "concierge", "spa",
      "steward", "catering", "baker", "butcher", "room attendant", "laundry", "front office", "reservation",
      "attendant", "kids club", "captain", "bellman", "valet", "beverage", "recreation", "housing supervisor", "b&f"],
     ["طاهي", "طباخ", "باريستا", "نادل", "مطعم", "فندق", "ضيافة", "قهوة", "حلويات", "مخبز", "شيف", "ويتر"]),
    ("education", "Education & Training", "التعليم",
     ["teacher", "lecturer", "professor", "tutor", "instructor", "educat", "school", "kindergarten", "academic",
      "curriculum", "teaching", "trainer"],
     ["معلم", "معلمة", "مدرس", "محاضر", "تعليم", "روضة", "أستاذ"]),
    ("legal", "Legal", "القانون",
     ["legal", "lawyer", "counsel", "paralegal", "attorney", "compliance", "governance", "litigation", "contracts manager"],
     ["قانون", "محامي", "مستشار قانوني", "امتثال", "قانوني"]),
    ("real_estate", "Real Estate", "العقار",
     ["real estate", "property", "leasing", "broker", "valuation", "realtor"],
     ["عقار", "عقاري", "عقارية"]),
    ("management", "Management & Strategy", "الإدارة العليا والاستراتيجية",
     ["general manager", "managing director", "ceo", "coo", "cfo", "director", "head of", "chief", "vice president",
      "strategy", "business analyst", "consultant", "operations manager", "branch manager", "manager"],
     ["مدير عام", "مدير", "استراتيج", "مستشار"]),
]

FIELD_NAMES = {fid: (en, ar) for fid, en, ar, _, _ in FIELDS}
FIELD_NAMES["other"] = ("Other", "أخرى")


def _norm(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"[إأآ]", "ا", text)
    return " " + re.sub(r"\s+", " ", text).strip() + " "


def _en_rule(keyword: str) -> str:
    """'it ' = whole word; 'pharmac' = word start (matches pharmacist, pharmacy)."""
    k = keyword.strip()
    tail = r"(?![a-z0-9])" if keyword.endswith(" ") or len(k) <= 3 else ""
    return r"(?<![a-z0-9])" + re.escape(k) + tail


_FIELD_RULES = [(fid, re.compile("|".join([_en_rule(k) for k in en] + [re.escape(_norm(k).strip()) for k in ar])))
                for fid, _, _, en, ar in FIELDS]


def job_field(title: str | None) -> str:
    t = _norm(title)
    if not t.strip():
        return "other"
    for fid, rule in _FIELD_RULES:
        if rule.search(t):
            return fid
    return "other"


# ---------------------------------------------------------------------------
# Skills: canonical name -> patterns (lowercase). English patterns are matched
# as whole words; Arabic patterns as substrings. Ambiguous short words
# ("R", "Go", "C") are left out on purpose.
# ---------------------------------------------------------------------------
SKILLS = {
    # data & software
    "SQL": ["sql"], "Python": ["python"], "Excel": ["excel", "إكسل", "اكسل"], "Power BI": ["power bi", "powerbi"],
    "Tableau": ["tableau"], "Java": ["java"], "JavaScript": ["javascript", "js"], "TypeScript": ["typescript"],
    "React": ["react", "react.js", "reactjs"], "Angular": ["angular"], "Vue": ["vue", "vue.js"],
    "Node.js": ["node.js", "nodejs", "node js"], ".NET": [".net", "asp.net", "dotnet"], "C#": ["c#"], "C++": ["c++"],
    "PHP": ["php"], "Laravel": ["laravel"], "Django": ["django"], "Flutter": ["flutter"], "Swift": ["swift"],
    "Kotlin": ["kotlin"], "HTML/CSS": ["html", "css"], "Git": ["git", "github", "gitlab"],
    "Docker": ["docker"], "Kubernetes": ["kubernetes", "k8s"], "AWS": ["aws", "amazon web services"],
    "Azure": ["azure"], "Google Cloud": ["gcp", "google cloud"], "Linux": ["linux"], "DevOps": ["devops"],
    "CI/CD": ["ci/cd", "continuous integration"], "REST APIs": ["rest api", "restful", "apis"],
    "Machine Learning": ["machine learning", "ml models"], "Deep Learning": ["deep learning"],
    "AI": ["artificial intelligence", "ai", "genai", "generative ai", "llm", "الذكاء الاصطناعي"],
    "Data Analysis": ["data analysis", "data analytics", "تحليل البيانات"], "Statistics": ["statistics", "statistical"],
    "Spark": ["spark", "pyspark"], "Databricks": ["databricks"], "Snowflake": ["snowflake"], "ETL": ["etl"],
    "Data Warehousing": ["data warehouse", "data warehousing"], "SAP": ["sap"], "Oracle": ["oracle"],
    "Salesforce": ["salesforce"], "Odoo": ["odoo"], "ERP": ["erp"], "CRM": ["crm"], "Jira": ["jira"],
    "Agile/Scrum": ["agile", "scrum"], "Networking": ["networking", "tcp/ip", "cisco", "ccna"],
    "Cybersecurity": ["cybersecurity", "cyber security", "information security", "الأمن السيبراني"],
    "SIEM": ["siem"], "ISO 27001": ["iso 27001", "iso27001"], "NCA ECC": ["nca", "ecc"],
    "UI/UX": ["ui/ux", "ux", "user experience", "figma"], "Figma": ["figma"],
    # office & business
    "Microsoft Office": ["ms office", "microsoft office", "office 365", "ms word", "powerpoint"],
    "Project Management": ["project management", "إدارة المشاريع", "ادارة المشاريع"], "PMP": ["pmp"],
    "Primavera P6": ["primavera", "p6"], "MS Project": ["ms project", "microsoft project"],
    "Budgeting": ["budgeting", "budget", "الميزانية"], "Financial Analysis": ["financial analysis", "financial modeling", "financial modelling"],
    "Financial Reporting": ["financial reporting", "financial statements", "القوائم المالية"],
    "IFRS": ["ifrs"], "SOCPA": ["socpa"], "CPA": ["cpa"], "CMA": ["cma"], "Zakat & VAT": ["vat", "zakat", "الزكاة", "ضريبة القيمة المضافة"],
    "Auditing": ["auditing", "internal audit", "التدقيق", "المراجعة"], "Payroll": ["payroll", "الرواتب"],
    "Bookkeeping": ["bookkeeping", "accounts payable", "accounts receivable", "general ledger"],
    "Negotiation": ["negotiation", "negotiating", "التفاوض"], "Sales": ["sales", "selling", "المبيعات"],
    "Business Development": ["business development", "تطوير الأعمال"], "Account Management": ["account management", "key accounts"],
    "B2B Sales": ["b2b"], "Digital Marketing": ["digital marketing", "التسويق الرقمي"], "SEO": ["seo"],
    "Social Media": ["social media", "وسائل التواصل"], "Content Creation": ["content creation", "content writing", "copywriting", "كتابة المحتوى"],
    "Google Ads": ["google ads", "adwords"], "Market Research": ["market research", "أبحاث السوق"],
    "Customer Service": ["customer service", "customer support", "خدمة العملاء"],
    "Communication": ["communication skills", "communication", "مهارات التواصل", "مهارات الاتصال"],
    "Leadership": ["leadership", "القيادة"], "Teamwork": ["teamwork", "team player", "العمل الجماعي"],
    "Problem Solving": ["problem solving", "problem-solving", "حل المشكلات"], "Time Management": ["time management", "إدارة الوقت"],
    "English": ["english", "الإنجليزية", "الانجليزية"], "Arabic": ["arabic", "العربية"],
    "Recruitment": ["recruitment", "talent acquisition", "التوظيف"], "Saudi Labor Law": ["labor law", "labour law", "نظام العمل"],
    "GOSI/Qiwa": ["gosi", "qiwa", "mudad", "التأمينات", "قوى", "مدد"], "Procurement": ["procurement", "purchasing", "المشتريات"],
    "Supply Chain": ["supply chain", "سلاسل الإمداد", "سلسلة الإمداد"], "Inventory Management": ["inventory management", "inventory control", "إدارة المخزون"],
    "Logistics": ["logistics", "اللوجستية"], "Driving License": ["driving license", "driving licence", "رخصة قيادة"],
    # engineering & trades
    "AutoCAD": ["autocad", "اوتوكاد", "أوتوكاد"], "Revit": ["revit"], "Civil 3D": ["civil 3d"], "SolidWorks": ["solidworks"],
    "ETAP": ["etap"], "MATLAB": ["matlab"], "BIM": ["bim"], "HVAC": ["hvac", "تكييف"], "PLC": ["plc", "scada"],
    "Electrical Systems": ["electrical systems", "low voltage", "medium voltage", "high voltage", "switchgear"],
    "Commissioning": ["commissioning"], "Quality Control": ["quality control", "qa/qc", "qc", "مراقبة الجودة"],
    "ISO 9001": ["iso 9001", "iso9001"], "HSE": ["hse", "health and safety", "occupational safety", "السلامة المهنية"],
    "NEBOSH": ["nebosh"], "OSHA": ["osha"], "Saudi Council of Engineers": ["saudi council of engineers", "sce", "هيئة المهندسين"],
    "Welding": ["welding", "لحام"], "Preventive Maintenance": ["preventive maintenance", "الصيانة الوقائية"],
    "Oil & Gas": ["oil and gas", "oil & gas", "petrochemical", "refinery"], "Aramco Experience": ["aramco"],
    # healthcare & hospitality
    "SCFHS License": ["scfhs", "saudi commission for health specialties", "الهيئة السعودية للتخصصات الصحية"],
    "BLS/ACLS": ["bls", "acls"], "Patient Care": ["patient care", "رعاية المرضى"], "Infection Control": ["infection control"],
    "Food Safety": ["food safety", "haccp", "سلامة الغذاء"], "Hospitality": ["hospitality", "الضيافة"],
    "Cash Handling": ["cash handling", "pos", "point of sale"], "Teaching": ["teaching", "lesson planning", "التدريس"],
}

# General (soft) skills, shown apart from technical skills on the site.
SOFT_SKILLS = {"Communication", "Leadership", "Teamwork", "Problem Solving", "Time Management",
               "English", "Arabic", "Negotiation", "Customer Service", "Attention to Detail",
               "Collaboration", "Decision-Making", "Critical Thinking", "Adaptability", "Coordination",
               "Organization", "Interpersonal Skills", "Strategic Planning", "Multitasking",
               "Relationship Building", "Strategic Thinking", "Planning"}

_SKILL_PATTERNS: list[tuple[str, re.Pattern]] = []
for _skill, _pats in SKILLS.items():
    parts = []
    for p in _pats:
        if re.search(r"[؀-ۿ]", p):
            parts.append(re.escape(_norm(p).strip()))
        else:
            parts.append(r"(?<![a-z0-9])" + re.escape(p) + r"(?![a-z0-9+#])")
    _SKILL_PATTERNS.append((_skill, re.compile("|".join(parts))))


def extract_skills(*texts: str | None) -> list[str]:
    """Skills mentioned in any of the texts (title, description...), in dictionary order."""
    blob = _norm(" ".join(t for t in texts if t))
    if not blob.strip():
        return []
    return [skill for skill, pat in _SKILL_PATTERNS if pat.search(blob)]
