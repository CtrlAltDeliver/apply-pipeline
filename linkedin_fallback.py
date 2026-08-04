#!/usr/bin/env python3
"""
linkedin_fallback.py — the honest fallback layer.

The main engine (``discover.py``) goes straight to structured ATS JSON APIs.
That's the right source when a company is on one of the supported platforms —
but the long tail isn't. Plenty of companies run on ATSes with no clean public
JSON (custom career pages, hardened Workday, SuccessFactors, Gr8People, …), and
for those the structured path is blind.

Rather than pretend that tail doesn't exist, this module degrades gracefully to
a best-effort source: LinkedIn's logged-out job search. It is deliberately kept
*separate* from the engine and clearly labelled a fallback, because it is a
different, weaker kind of data:

  * It scrapes guest-view HTML instead of reading a structured API, so it breaks
    when LinkedIn changes its markup (the JD-body extractor below has already had
    to chase one such change — see ``JD_MARKUP_RE``).
  * It can't verify a posting against the company's own careers page.
  * Salary/location are parsed heuristically, not read from structured fields.

So the contract is: **structured-first; fall back to this only for coverage the
APIs can't reach, and treat its output as needing a human's verification.**

To stay consistent with the engine, it reuses ``discover.py``'s own location
classifier, salary parser, freshness check, title filter, and role-dict shape —
it is a second *source*, not a second set of rules. Output is the same
``{"candidates": [...], "stats": {...}}`` payload ``discover.py`` emits.

Usage:
    python3 linkedin_fallback.py [--max-enrich N] [--pretty]
    # or fold it into a single ranked list alongside the ATS pass:
    python3 discover.py --linkedin-fallback
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config  # noqa: E402
import title_filter  # noqa: E402
from discover import classify_location, _parse_salary, _age_days  # noqa: E402
from normalize import normalize_title, normalize_company  # noqa: E402

# ---------------------------------------------------------------------------
# Search configuration

KEYWORDS = ["Technical Program Manager", "Technical Project Manager"]

# LinkedIn TPR filter r2592000 = past 30 days; sortBy=DD (date-descending) also
# makes the `start` paging cursor actually advance on this endpoint.
SEARCH_TEMPLATE = (
    "https://www.linkedin.com/jobs/search"
    "?keywords={keywords}&location={location}&f_TPR=r2592000&sortBy=DD&start={start}"
)
SEARCH_LOCATION = "Canada"   # mirrors the example geography in config.py
SEARCH_PAGE_SIZE = 25
MAX_SEARCH_PAGES = 5
SEARCH_PAGE_SLEEP = 0.8

# Enrich the top survivors, sorted by the engine's location ranking so the
# priority tiers get the budget first. The cap is high enough that a role whose
# guest-view card location is blank (ranked lowest) still gets enriched rather
# than stranded — see the sort before the cap in run().
DEFAULT_MAX_ENRICH = 100
ENRICH_SLEEP = 1.0
CONTENT_CHARS = 3000  # tighter than the ATS path (6000): far more candidates here
REQUEST_TIMEOUT = 20

# ---------------------------------------------------------------------------
# Regexes

JOB_HREF_RE = re.compile(
    r'href="(https://[a-z]*\.?linkedin\.com/jobs/view/([^/"]+?)-at-([^/"]+?)-(\d+)[^"]*)"'
)
CARD_LOCATION_RE = re.compile(r'job-search-card__location[^>]*>\s*([^<]+)')
LOCATION_META_RE = re.compile(
    r'<span[^>]*class="[^"]*topcard__flavor[^"]*flavor--bullet[^"]*"[^>]*>(.*?)</span>',
    re.DOTALL,
)
LOCATION_FALLBACK_RE = re.compile(r'"addressLocality"\s*:\s*"([^"]+)"')
# Primary JD-body source (2026-08): the guest view renders the description as
# HTML inside <div class="show-more-less-html__markup …">…</div>, closed just
# before the show-more toggle <button class="show-more-less-html__button …">.
# Non-greedy to that specific button so inner </div>s don't truncate the capture.
# (LinkedIn dropped the old JSON-LD "description" field, which is why a naive
# JSON extractor returns nothing — the classic guest-view scraper fragility.)
JD_MARKUP_RE = re.compile(
    r'show-more-less-html__markup[^>]*>(.*?)</div>\s*<button\s+class="show-more-less-html__button',
    re.DOTALL,
)
JSON_BODY_RE = re.compile(r'"description"\s*:\s*"((?:\\.|[^"\\])*)"')  # legacy fallback
SALARY_RANGE_RE = re.compile(
    r"\$?\s*([0-9]{2,3}(?:,[0-9]{3})+)\s*(?:[-–to]+)\s*\$?\s*([0-9]{2,3}(?:,[0-9]{3})+)"
)
POSTED_DATE_RE = re.compile(r'"datePosted"\s*:\s*"([0-9]{4}-[0-9]{2}-[0-9]{2})')


# ---------------------------------------------------------------------------
# HTTP

def _fetch(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": config.USER_AGENT_BROWSER,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    })
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
            return r.read().decode("utf-8", errors="replace")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        return None


# ---------------------------------------------------------------------------
# Search-page parsing

def _deslug(s):
    return " ".join(w.capitalize() for w in s.split("-") if w)


def _parse_search_page(body):
    """One dict per job card, capturing the card's location string (each href
    takes the nearest location span that follows it in document order)."""
    loc_spans = [(m.start(), html.unescape(m.group(1)).strip())
                 for m in CARD_LOCATION_RE.finditer(body)]

    def loc_after(pos):
        for span_pos, text in loc_spans:
            if span_pos >= pos:
                return text
        return ""

    seen, out = set(), []
    for m in JOB_HREF_RE.finditer(body):
        _full, title_slug, company_slug, job_id = m.groups()
        if job_id in seen:
            continue
        seen.add(job_id)
        out.append({
            "job_id": job_id,
            "url": f"https://www.linkedin.com/jobs/view/{job_id}/",
            "title": _deslug(title_slug),
            "company": _deslug(company_slug),
            "card_location": loc_after(m.start()),
        })
    return out


def _collect(keywords):
    by_id = {}
    for kw in keywords:
        kwq = urllib.request.quote(kw)
        for page in range(MAX_SEARCH_PAGES):
            url = SEARCH_TEMPLATE.format(
                keywords=kwq, location=urllib.request.quote(SEARCH_LOCATION),
                start=page * SEARCH_PAGE_SIZE,
            )
            body = _fetch(url)
            if not body:
                break
            jobs = _parse_search_page(body)
            if not jobs:
                break
            new = 0
            for j in jobs:
                if j["job_id"] not in by_id:
                    by_id[j["job_id"]] = j
                    new += 1
            if new == 0:
                break
            if page < MAX_SEARCH_PAGES - 1:
                time.sleep(SEARCH_PAGE_SLEEP)
    return list(by_id.values())


# ---------------------------------------------------------------------------
# Enrichment

def _enrich(job):
    """Fetch /jobs/view/<id>/ and pull location, salary, posted date, JD body."""
    body = _fetch(job["url"])
    if not body:
        return job

    location = None
    m = LOCATION_META_RE.search(body)
    if m:
        location = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", m.group(1)))).strip()
    if not location:
        m = LOCATION_FALLBACK_RE.search(body)
        if m:
            location = html.unescape(m.group(1)).strip()
    if location:
        job["location"] = location

    m = SALARY_RANGE_RE.search(body)
    if m:
        salary_str = f"${m.group(1)} - ${m.group(2)}"
        job["salary"] = salary_str
        # Reuse the engine's currency-aware parser (returns {min,max,currency,raw}).
        job["salary_parsed"] = _parse_salary(salary_str, "", job.get("location", ""))

    m = POSTED_DATE_RE.search(body)
    if m:
        job["updated_at"] = m.group(1)

    # JD body: markup div first, then legacy JSON-LD fallback.
    raw = None
    m = JD_MARKUP_RE.search(body)
    if m:
        raw = m.group(1)
    else:
        m = JSON_BODY_RE.search(body)
        if m:
            raw = m.group(1).encode("utf-8").decode("unicode_escape", errors="replace")
    if raw is not None:
        text = re.sub(r"<[^>]+>", " ", raw)
        text = html.unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        job["content"] = text[:CONTENT_CHARS]
    return job


# ---------------------------------------------------------------------------
# Pipeline

def run(max_enrich=DEFAULT_MAX_ENRICH):
    stats = {"fetched": 0, "title_pass": 0, "location_pre_pass": 0,
             "enriched": 0, "location_pass": 0}

    raw = _collect(KEYWORDS)
    stats["fetched"] = len(raw)

    # Title filter — same rules as the ATS path.
    title_pass = []
    for j in raw:
        ok, reason = title_filter.matches(j["title"])
        if ok:
            j["title_match_reason"] = reason
            title_pass.append(j)
    stats["title_pass"] = len(title_pass)

    # Pre-enrich location filter on the card location string — drop only clear
    # rejects (foreign/other-home-onsite); keep everything the engine's classifier
    # would still consider, including blank ('maybe') locations.
    pre = [j for j in title_pass
           if classify_location(j.get("card_location", "")) != "reject"]
    stats["location_pre_pass"] = len(pre)

    # Sort by the engine's own location ranking BEFORE the cap, so priority tiers
    # get the enrichment budget first and a blank-location role isn't stranded.
    pre.sort(key=lambda j: config.RANK_ORDER.get(
        classify_location(j.get("card_location", "")), 9))

    enriched = []
    for j in pre[:max_enrich]:
        enriched.append(_enrich(j))
        stats["enriched"] += 1
        time.sleep(ENRICH_SLEEP)

    # Post-enrich decision using the enriched location + the engine's classifier
    # and salary floor — identical logic to discover.discover().
    candidates = []
    for j in enriched:
        tier = classify_location(j.get("location") or j.get("card_location", ""))
        if tier == "reject":
            continue
        if tier == "conditional" and config.CONDITIONAL_SALARY_FLOOR:
            sp = j.get("salary_parsed")
            if sp and sp.get("max", 0) < config.CONDITIONAL_SALARY_FLOOR:
                continue
            if not sp:
                j["needs_salary_review"] = True
        j["ats"] = "linkedin"           # provenance: this came from the fallback
        j["ats_slug"] = ""
        j["location"] = j.get("location") or j.get("card_location", "")
        j.setdefault("salary", "")
        j.setdefault("salary_parsed", None)
        j.setdefault("content", "")
        j["location_tier"] = tier
        j["age_days"] = _age_days(j.get("updated_at"))
        j["source"] = "linkedin_fallback"   # so downstream can flag "verify manually"
        candidates.append(j)
    stats["location_pass"] = len(candidates)

    candidates.sort(key=lambda r: (
        config.RANK_ORDER.get(r["location_tier"], 9),
        r["age_days"] if r["age_days"] is not None else 999,
    ))
    return {"candidates": candidates, "stats": stats}


def main():
    ap = argparse.ArgumentParser(description="LinkedIn fallback source for the discovery engine.")
    ap.add_argument("--max-enrich", type=int, default=DEFAULT_MAX_ENRICH)
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args()

    result = run(max_enrich=args.max_enrich)
    if args.pretty:
        s = result["stats"]
        print(f"[fallback] fetched={s['fetched']} title_pass={s['title_pass']} "
              f"pre={s['location_pre_pass']} enriched={s['enriched']} "
              f"kept={s['location_pass']}")
        for c in result["candidates"]:
            age = f"{c['age_days']}d" if c.get("age_days") is not None else "age?"
            print(f"  [{c['location_tier']:<12}] [{age:>5}] {c['company']:<22} {c['title']}")
            print(f"  {'':<23} {c['location']}  ({len(c.get('content',''))} chars JD)")
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
