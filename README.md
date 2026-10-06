# Masar v2

Masar collects Saudi job postings from Sabbar, Tanqeeb and Wuzzuf, cleans them, models them as a star schema and publishes them to the Masar website. It runs every day for free on GitHub Actions. There is no Azure and no Databricks: the data is stored as Parquet files and processed with DuckDB.

```
scrapers ──> data/landing ──> bronze ──> silver ──> gold (star schema) ──> checks ──> site/jobs.json + stats.json
                                                                                     │
        data branch (all data, one commit) <─────────────────────────────────────────┘ gh-pages (website)
```

## التشغيل لأول مرة (5 دقايق)

1. ارفع محتوى هذا المجلد على فرع `main`.
2. افتح Settings ثم Actions ثم General، وتحت **Workflow permissions** اختر **Read and write permissions** واحفظ.
3. افتح تبويب Actions، اختر **Masar daily** واضغط **Run workflow**.
   - أول تشغيل ينشئ فرع `data`، وياخذ وظايف الموقع القديم كتاريخ، وينشئ فرع `gh-pages` للموقع.
4. بعد ما يخلص، افتح Settings ثم Pages، واختر Branch: `gh-pages` و `/ (root)` واحفظ.
5. بعدها يشتغل كل يوم الساعة 5:17 الصبح بتوقيت الرياض.

أي تعديل على الموقع تسويه في `website/index.html` على `main`، وينزل مع التشغيل الجاي.

لو فشل أي سكريبر (حظر أو تغيّر في شكل الموقع) يصير التشغيل أحمر وتوصلك رسالة من GitHub على الإيميل. الموقع ما يتحدث إلا إذا نجحت كل الفحوصات.

### الأسرار (لا تحطها في ملفات أبدًا)
تنحط في Settings ثم Secrets and variables ثم Actions، وكلها اختيارية:
`PROXY_ENABLED`, `PROXY_HOST`, `PROXY_PORT`, `PROXY_USER`, `PROXY_PASS`.
مفتاح Jooble يمشي بنفس الطريقة لما نضيفه.

## تشغيله على جهازك

```bash
pip install -r requirements.txt
python -m playwright install chromium      # Sabbar only

python -m masar.sources.tanqeeb            # each scraper writes to data/landing/<source>/
python -m masar.sources.wuzzuf
python -m masar.sources.sabbar
python -m masar.pipeline.run               # writes data/lake/... and site/jobs.json, site/stats.json

python -m pytest -q                        # 20 tests, no internet needed
```

للتجربة السريعة بدون سحب، انسخ jobs.json حق الموقع إلى `data/landing/seed/` وشغّل `python -m masar.pipeline.run`.
ملفات Azure القديمة (Raw) تقدر ترجعها بنفس الطريقة: حطها في `data/landing/<source>/`، والبايبلاين يفهم الصيغة القديمة والجديدة.

## Data layout (`data` branch)

| Path | What |
|---|---|
| `landing/<source>/*.json` | raw scraper output waiting to be loaded |
| `archive/<source>/*.json.gz` | raw files already loaded (kept `ARCHIVE_KEEP_DAYS`, default 60) |
| `checkpoints/<source>.json` | URLs each scraper has fully handled |
| `failed/<source>/*.json` | URLs that failed (retried automatically next run) |
| `runs/<source>_<ts>.json` | per-scraper run summary and health status |
| `lake/bronze/*.parquet` | every raw record ever loaded (append only) |
| `lake/silver/posted_month=*/` | one clean row per posting |
| `lake/rejected.parquet` | rows that failed validation, with the reason |
| `lake/gold/*.parquet` | star schema: `fact_job_posting`, `bridge_job_posting_skill`, `dim_source/company/location/job/skill/date` |
| `reports/<ts>.json` | per-run report: counts, rejects, checks, scraper health |

Power BI can read the gold Parquet files directly (Get Data, then Parquet), or you can download the `data` branch as a zip.

## What v2 fixes

**Scrapers** (shared rules in `masar/sources/base.py`):
- Landing data is written before the checkpoint, so a crash can no longer lose jobs.
- Each run gets its own landing file, so a second run on the same day no longer overwrites the first.
- Failed or invalid pages are not checkpointed. They are retried on the next run.
- "Page loaded but no job" counts as removed only while it is rare. If it happens to most pages, the run fails instead of silently skipping everything.
- Health exit codes: 2 = no listings found (blocked), 3 = most pages failed to parse (layout changed).
- Wuzzuf no longer attaches a different job's data when the slug is missing. Tanqeeb no longer follows footer links.

**Pipeline**:
- All landing files are loaded, not just the latest one. Each file is loaded exactly once (tracked by content hash).
- `job_id` is a hash of source + canonical URL instead of the title, so two different jobs with the same title stay separate.
- Description is optional. Unknown work mode stays "Unknown" instead of defaulting to On-site.
- A missing posted date falls back to the first-seen date and is flagged as estimated.
- Salary and experience are parsed into numbers. Skills are split on `;`.
- Duplicates across sources (same title, company and city on another source within 30 days) are flagged with `is_primary` rather than deleted. The website shows each job once.
- Silver and gold are rebuilt on every run with deterministic keys. Running twice gives identical files (covered by a test).
- 18 data-quality checks run before the website is touched.

## Configuration

| Variable | Default |
|---|---|
| `TANQEEB_MAX_JOBS` / `WUZZUF_MAX_JOBS` / `SABBAR_MAX_JOBS` | 300 / 300 / 900 new jobs per run |
| `SABBAR_CONCURRENCY` | 4 |
| `EXPORT_MAX_AGE_DAYS` | 0 (export everything) |
| `ARCHIVE_KEEP_DAYS` | 60 |
