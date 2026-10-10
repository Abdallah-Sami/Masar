"""
AI skill extraction: reads each job description once with an AI model and
stores the skills it finds. Optional and OFF unless ANTHROPIC_API_KEY is set.

    python -m masar.pipeline.ai_skills          # processes jobs not done yet

* Each job is sent once; results are cached in data/ai/skills.jsonl
  (job_id -> skills), so re-runs cost nothing for old jobs.
* AI_SKILLS_MAX_JOBS (default 1000) caps how many new jobs are processed per
  run, so a bug can never run up a large bill.
* Skill names come back in short English form ("Excel", "AutoCAD",
  "Customer Service") and are then merged with the keyword list in silver.

Env: ANTHROPIC_API_KEY (GitHub secret), AI_SKILLS_MODEL (default below),
     AI_SKILLS_MAX_JOBS, AI_SKILLS_BATCH (jobs per request, default 10).
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

import requests

from masar import storage
from masar.pipeline import io, taxonomy

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-haiku-4-5"
MAX_DESC_CHARS = 4000

PROMPT = """You read job postings from Saudi Arabia (in English or Arabic) and list the skills each job asks for.

For every job return:
- "skills": hard skills, tools, software and technical knowledge the job requires or mentions as a plus.
- "soft_skills": personal skills (e.g. Communication, Leadership, Teamwork, Negotiation, Problem Solving).
- "certifications": licenses and certificates (e.g. PMP, SCFHS License, Saudi Council of Engineers, Driving License, NEBOSH).
- "languages": spoken languages required (e.g. Arabic, English).

Rules:
- Use short, standard English names in Title Case, even when the posting is in Arabic
  ("إكسل" -> "Excel", "خدمة العملاء" -> "Customer Service", "رخصة قيادة" -> "Driving License").
- Use the common name of a tool, not a sentence ("Excel", not "advanced knowledge of MS Excel").
- Only include what the posting actually says. Do not guess skills from the job title alone.
- Leave out vague traits ("hard working", "passionate", "good attitude") and benefits.
- At most 15 skills, 6 soft skills per job. Empty lists are fine.
- Keep names general enough to repeat across jobs ("Budgeting", not "Annual Budget Preparation for Retail Branches").

Answer with JSON only, no other text, in this shape:
{"jobs": [{"job_id": "...", "skills": [], "soft_skills": [], "certifications": [], "languages": []}]}
"""


def cache_path():
    return storage.path("ai", "skills.jsonl")


def canonical(name: str) -> str:
    """Map an AI skill name onto our standard list when it clearly is one of
    them ("MS Excel" -> "Excel", "Customer Service Skills" -> "Customer Service")."""
    hits = taxonomy.extract_skills(name)
    return hits[0] if len(hits) == 1 else name


def skills_from(rec: dict) -> list[str]:
    """All skill names from one cached AI answer, in a stable order."""
    out, seen = [], set()
    for key in ("skills", "certifications", "soft_skills", "languages"):
        for name in rec.get(key) or []:
            name = canonical(str(name).strip())
            if name and len(name) <= 60 and name.lower() not in seen:
                seen.add(name.lower())
                out.append(name)
    return out


def load_cache() -> dict[str, dict]:
    p = cache_path()
    out = {}
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                out[rec["job_id"]] = rec
    return out


def build_request(jobs: list[dict]) -> str:
    parts = [PROMPT, "\nJobs:\n"]
    for j in jobs:
        desc = (j.get("description") or "")[:MAX_DESC_CHARS]
        parts.append(f'<job id="{j["job_id"]}">\nTitle: {j["job_title"]}\n{desc}\n</job>\n')
    return "".join(parts)


def parse_answer(text: str) -> list[dict]:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError("no JSON in answer")
    return json.loads(m.group(0)).get("jobs", [])


def call_model(prompt: str, key: str, model: str) -> tuple[str, dict]:
    for attempt in range(1, 4):
        r = requests.post(API_URL, timeout=120, headers={
            "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": model, "max_tokens": 4000, "messages": [{"role": "user", "content": prompt}]})
        if r.status_code in (429, 500, 502, 503, 529):
            time.sleep(10 * attempt)
            continue
        r.raise_for_status()
        data = r.json()
        return "".join(b.get("text", "") for b in data.get("content", [])), data.get("usage", {})
    raise RuntimeError("model unavailable after retries")


def run(log=print) -> dict:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        log("[ai_skills] ANTHROPIC_API_KEY not set: skipped.")
        return {"skipped": True}
    model = os.environ.get("AI_SKILLS_MODEL", DEFAULT_MODEL)
    cap = int(os.environ.get("AI_SKILLS_MAX_JOBS", "1000"))
    batch = int(os.environ.get("AI_SKILLS_BATCH", "10"))

    cache = load_cache()
    silver = storage.data_dir() / "lake" / "silver"
    todo = [j for j in io.read_rows(silver, """SELECT job_id, job_title, description FROM {t}
                                                WHERE is_primary AND description IS NOT NULL
                                                ORDER BY first_seen_at DESC""")
            if j["job_id"] not in cache][:cap]
    usage = {"input_tokens": 0, "output_tokens": 0}
    done = failed = 0
    with open(cache_path(), "a", encoding="utf-8") as fh:
        for i in range(0, len(todo), batch):
            chunk = todo[i:i + batch]
            try:
                text, u = call_model(build_request(chunk), key, model)
                ids = {j["job_id"] for j in chunk}
                for rec in parse_answer(text):
                    if rec.get("job_id") in ids:
                        rec["model"] = model
                        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        done += 1
                for k in usage:
                    usage[k] += u.get(k, 0)
            except Exception as e:      # one bad batch must not stop the rest
                failed += len(chunk)
                log(f"[ai_skills] batch failed: {str(e)[:120]}")
    result = {"processed": done, "failed": failed, "remaining": max(0, len(todo) - done), **usage}
    log(f"[ai_skills] {result}")
    return result


if __name__ == "__main__":
    r = run()
    sys.exit(0 if r.get("skipped") or not r.get("failed") else 1)
