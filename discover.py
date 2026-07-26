"""Parallel ATS job-discovery engine.

Reads ``ats-targets.yaml``, queries each verified company's public JSON
endpoint in parallel, and filters the roles by title, location, and freshness.
Optionally dedupes against a ``seen.json`` of roles you've already applied to
or declined. Emits a ranked JSON candidate list on stdout.

Supported ATS platforms: Greenhouse, Lever, Ashby, SmartRecruiters, Workday.

Usage:
    python3 discover.py                    # discover + print JSON to stdout
    python3 discover.py --pretty           # human-readable summary
    python3 discover.py --max-age N        # override freshness cutoff (days)
    python3 discover.py --seen seen.json   # dedupe against prior applications
"""

import argparse
import concurrent.futures
import datetime as dt
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request

import yaml

import config
from normalize import normalize_title, normalize_company

_HERE = os.path.dirname(os.path.abspath(__file__))
YAML_PATH = os.path.join(_HERE, "ats-targets.yaml")


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _fetch(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
        with urllib.request.urlopen(req, timeout=config.HTTP_TIMEOUT) as r:
            return r.read().decode("utf-8", errors="ignore")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return None


def _post_json(url, payload):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "User-Agent": config.USER_AGENT,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=config.HTTP_TIMEOUT) as r:
            return r.read().decode("utf-8", errors="ignore")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return None


def _strip_html(s):
    if not s:
        return ""
    # Some ATSes double-escape content (&amp;mdash;). Loop until stable.
    for _ in range(3):
        new = html.unescape(s)
        if new == s:
            break
        s = new
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# ---------------------------------------------------------------------------
# Salary parsing — currency-aware.
# ---------------------------------------------------------------------------

# Salary digit shape: comma-separated (120,000) OR plain int (120000) OR K-suffix.
_SALARY_NUM = r"(?:\d{2,3}(?:[,\.]\d{3})+|\d{4,7}|\d{2,3}[KkMm])(?:\.\d+)?"
_SALARY_RX = re.compile(
    r"(?:(?P<cur1>USD|CAD|US\$|C\$|\$))?\s*"
    r"(?P<min>" + _SALARY_NUM + r")"
    r"\s*(?:[-–—]|to|\bto\b)\s*"
    r"(?:(?P<cur2>USD|CAD|US\$|C\$|\$))?\s*"
    r"(?P<max>" + _SALARY_NUM + r")"
    r"(?:\s*(?P<cur3>USD|CAD))?",
)


def _looks_home(location):
    """True if the location string carries any home-region signal — used only
    to guess currency when a salary band uses a bare '$'."""
    return bool(
        config.PREFERRED.search(location)
        or config.HOME_COUNTRY.search(location)
        or config.CONDITIONAL.search(location)
        or config.CONDITIONAL_ABBR.search(location)
        or config.HOME_OTHER.search(location)
    )


def _to_int(n):
    """Convert '120,000' / '120K' / '120k' to int."""
    n = n.replace(",", "").replace(".", "")
    if n.lower().endswith("k"):
        return int(n[:-1]) * 1000
    if n.lower().endswith("m"):
        return int(n[:-1]) * 1_000_000
    return int(n)


def _parse_salary(salary_string, content="", location=""):
    """Parse a salary string (or fall back to JD content).

    Returns {min, max, currency, raw} normalized into the home currency, or
    None. Prefers a home-country-specific band in multi-region JDs.
    """
    candidates = []
    if salary_string:
        candidates.append(salary_string)
    if content:
        home_idx = re.search(r"(?i)\bcanada\b", content)
        if home_idx:
            candidates.append(content[home_idx.start():home_idx.start() + 800])
        candidates.append(content)

    for text in candidates:
        if not text:
            continue
        for m in _SALARY_RX.finditer(text):
            try:
                lo = _to_int(m.group("min"))
                hi = _to_int(m.group("max"))
            except (ValueError, TypeError):
                continue
            if lo < 30_000 or hi > 5_000_000 or lo > hi:
                continue  # likely not a salary (hours, IDs, equity, etc.)

            cur_token = (m.group("cur1") or m.group("cur2") or m.group("cur3") or "").upper()
            if "CAD" in cur_token or cur_token == "C$":
                currency = "CAD"
            elif "USD" in cur_token or cur_token == "US$":
                currency = "USD"
            elif _looks_home(location):
                # Bare "$" in a home-region role → assume home currency.
                currency = "CAD"
            else:
                currency = "USD"

            factor = 1.0 if currency == "CAD" else config.USD_TO_CAD
            return {
                "min": int(lo * factor),
                "max": int(hi * factor),
                "currency": currency,
                "raw": m.group(0).strip(),
            }
    return None


# ---------------------------------------------------------------------------
# Per-ATS adapters — normalize each payload into a common role dict.
# ---------------------------------------------------------------------------

def _parse_greenhouse(company, slug, body):
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return []
    roles = []
    for job in data.get("jobs", []):
        location = (job.get("location") or {}).get("name", "")
        content = _strip_html(job.get("content", ""))[:6000]
        salary_string = ""
        salary_parsed = None
        for meta in job.get("metadata") or []:
            name = (meta.get("name") or "").lower()
            value = meta.get("value")
            if not ("salary" in name or "pay" in name or "compensation" in name):
                continue
            if isinstance(value, dict) and value.get("min_value") and value.get("max_value"):
                try:
                    lo = float(value["min_value"])
                    hi = float(value["max_value"])
                    cur = (value.get("unit") or "USD").upper()
                    factor = 1.0 if cur == "CAD" else config.USD_TO_CAD
                    salary_parsed = {
                        "min": int(lo * factor),
                        "max": int(hi * factor),
                        "currency": cur,
                        "raw": f"{cur} {int(lo):,} - {int(hi):,}",
                    }
                    salary_string = salary_parsed["raw"]
                except (ValueError, TypeError):
                    pass
            elif value:
                salary_string = str(value)
            break
        if not salary_parsed:
            salary_parsed = _parse_salary(salary_string, content, location)
        roles.append({
            "company": company, "ats": "greenhouse", "ats_slug": slug,
            "title": job.get("title", ""), "location": location,
            "url": job.get("absolute_url", ""), "updated_at": job.get("updated_at", ""),
            "salary": salary_string, "salary_parsed": salary_parsed, "content": content,
        })
    return roles


def _parse_lever(company, slug, body):
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    roles = []
    for job in data:
        cats = job.get("categories") or {}
        location = cats.get("location") or ""
        workplace = job.get("workplaceType") or ""
        if workplace and workplace.lower() == "remote" and "remote" not in location.lower():
            location = f"Remote — {location}".strip(" —")
        ts = job.get("createdAt") or 0  # unix ms
        updated_iso = ""
        if ts:
            try:
                updated_iso = dt.datetime.utcfromtimestamp(ts / 1000).isoformat() + "Z"
            except (ValueError, OSError):
                pass
        content = _strip_html(job.get("descriptionPlain") or job.get("description", ""))[:6000]
        sr = job.get("salaryRange") or {}
        salary_string = ""
        if isinstance(sr, dict) and sr.get("min") and sr.get("max"):
            cur = sr.get("currency") or ""
            salary_string = f"{cur} {sr['min']} - {sr['max']}".strip()
        roles.append({
            "company": company, "ats": "lever", "ats_slug": slug,
            "title": job.get("text", ""), "location": location,
            "url": job.get("hostedUrl", ""), "updated_at": updated_iso,
            "salary": salary_string,
            "salary_parsed": _parse_salary(salary_string, content, location),
            "content": content,
        })
    return roles


def _parse_ashby(company, slug, body):
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return []
    roles = []
    for job in data.get("jobs", []):
        locs = [job.get("location") or ""]
        for sl in job.get("secondaryLocations") or []:
            ln = sl.get("location")
            if ln:
                locs.append(ln)
        location = " / ".join(l for l in locs if l)
        if job.get("isRemote") and "remote" not in location.lower():
            location = (location + " — Remote") if location else "Remote"

        salary = ""
        comp = job.get("compensation") or {}
        if isinstance(comp, dict):
            salary = comp.get("compensationTierSummary") or ""
        salary_parsed = None
        tiers = comp.get("compensationTiers") if isinstance(comp, dict) else None
        if isinstance(tiers, list):
            for tier in tiers:
                for c in (tier.get("components") or []):
                    if c.get("compensationType") == "Salary" and c.get("minValue") and c.get("maxValue"):
                        cur = (c.get("currencyCode") or "USD").upper()
                        factor = 1.0 if cur == "CAD" else config.USD_TO_CAD
                        salary_parsed = {
                            "min": int(c["minValue"] * factor),
                            "max": int(c["maxValue"] * factor),
                            "currency": cur, "raw": salary,
                        }
                        break
                if salary_parsed:
                    break
        if not salary_parsed:
            salary_parsed = _parse_salary(salary, "", location)
        content = _strip_html(job.get("descriptionHtml") or job.get("descriptionPlain", ""))[:6000]
        roles.append({
            "company": company, "ats": "ashby", "ats_slug": slug,
            "title": job.get("title", ""), "location": location,
            "url": job.get("jobUrl", ""),
            "updated_at": job.get("publishedAt") or job.get("updatedAt", ""),
            "salary": salary, "salary_parsed": salary_parsed, "content": content,
        })
    return roles


def _parse_smartrecruiters(company, slug, body):
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return []
    roles = []
    for job in data.get("content", []):
        loc = job.get("location") or {}
        parts = [loc.get("fullLocation")] if loc.get("fullLocation") else [
            loc.get("city"), loc.get("region"), loc.get("country", "").upper()
        ]
        location = ", ".join(p for p in parts if p)
        if loc.get("remote") and "remote" not in location.lower():
            location = f"{location} — Remote" if location else "Remote"
        roles.append({
            "company": company, "ats": "smartrecruiters", "ats_slug": slug,
            "title": job.get("name", ""), "location": location,
            "url": f"https://jobs.smartrecruiters.com/{slug}/{job.get('id', '')}",
            "updated_at": job.get("releasedDate", ""),
            "salary": "", "salary_parsed": None,  # list endpoint omits comp
            "content": "",  # list endpoint omits JD; fetch URL if needed
        })
    return roles


_RELATIVE_AGE_RX = re.compile(r"(\d+)\s*\+?\s*(day|week|month)s?", re.IGNORECASE)


def _workday_age_days(posted_on):
    """Workday returns relative strings like 'Posted 5 Days Ago'."""
    if not posted_on:
        return None
    s = posted_on.lower()
    if "today" in s or "yesterday" in s:
        return 0 if "today" in s else 1
    m = _RELATIVE_AGE_RX.search(s)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    return {"day": n, "week": n * 7, "month": n * 30}.get(unit)


def _parse_workday(company, tenant, wd_host, board, body):
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return []
    roles = []
    for job in data.get("jobPostings", []):
        path = job.get("externalPath") or ""
        url = f"https://{tenant}.{wd_host}.myworkdayjobs.com/en-US/{board}{path}"
        age = _workday_age_days(job.get("postedOn", ""))
        updated_iso = ""
        if age is not None:
            d = dt.datetime.utcnow() - dt.timedelta(days=age)
            updated_iso = d.strftime("%Y-%m-%dT%H:%M:%S")
        roles.append({
            "company": company, "ats": "workday", "ats_slug": f"{tenant}/{board}",
            "title": job.get("title", ""), "location": job.get("locationsText", ""),
            "url": url, "updated_at": updated_iso,
            "salary": "", "salary_parsed": None, "content": "",
        })
    return roles


def _query_company(entry):
    ats = entry.get("ats")
    slug = entry.get("slug")
    company = entry.get("company")
    if ats == "greenhouse":
        body = _fetch(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
        return (company, slug, ats, _parse_greenhouse(company, slug, body) if body else None)
    if ats == "lever":
        body = _fetch(f"https://api.lever.co/v0/postings/{slug}?mode=json")
        return (company, slug, ats, _parse_lever(company, slug, body) if body else None)
    if ats == "ashby":
        body = _fetch(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true")
        return (company, slug, ats, _parse_ashby(company, slug, body) if body else None)
    if ats == "smartrecruiters":
        roles = []
        for offset in range(0, 500, 100):  # paginate, cap at 500
            body = _fetch(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset={offset}")
            if not body:
                break
            page = _parse_smartrecruiters(company, slug, body)
            if not page:
                break
            roles.extend(page)
            if len(page) < 100:
                break
        return (company, slug, ats, roles or None)
    if ats == "workday":
        tenant, wd_host, board = entry.get("tenant"), entry.get("wd_host"), entry.get("board")
        if not (tenant and wd_host and board):
            return (company, slug, ats, None)
        roles = []
        for offset in range(0, 100, 20):  # paginate, cap at 100
            body = _post_json(
                f"https://{tenant}.{wd_host}.myworkdayjobs.com/wday/cxs/{tenant}/{board}/jobs",
                {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": ""},
            )
            if not body:
                break
            page = _parse_workday(company, tenant, wd_host, board, body)
            if not page:
                break
            roles.extend(page)
            if len(page) < 20:
                break
        return (company, slug, ats, roles or None)
    return (company, slug, ats, None)


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

def classify_location(location):
    """Assign a role's location string to a preference tier.

    Returns one of: 'preferred', 'remote_home', 'conditional', 'maybe',
    'reject'. Tiers and their patterns are defined in config.py — this
    function is geography-agnostic.
    """
    if not location:
        return "maybe"

    has_preferred = bool(config.PREFERRED.search(location))
    has_home = bool(config.HOME_COUNTRY.search(location))
    has_remote = bool(config.REMOTE.search(location))
    has_conditional = bool(config.CONDITIONAL.search(location)) or bool(config.CONDITIONAL_ABBR.search(location))
    has_home_other = bool(config.HOME_OTHER.search(location))
    has_foreign = bool(config.FOREIGN_COUNTRY.search(location)) or bool(config.FOREIGN_CITY.search(location))

    if has_preferred:
        return "preferred"
    # Remote + any home-country signal → hireable from home.
    if has_remote and (has_home or has_conditional or has_home_other):
        return "remote_home"
    # Conditional tier passes regardless of foreign cities also listed
    # (multi-location role hireable in the conditional region). Caller still
    # checks the salary floor.
    if has_conditional:
        return "conditional"
    # Other home-country city, onsite only → reject.
    if has_home_other and not has_remote:
        return "reject"
    if has_foreign:
        return "reject"
    # Unspecified remote with no foreign city → possibly home-eligible.
    if has_remote:
        return "maybe"
    return "reject"


def _age_days(updated_at):
    """Return age in days, or None if unparseable."""
    if not updated_at:
        return None
    s = updated_at.rstrip("Z")
    s = re.sub(r"[+\-]\d{2}:?\d{2}$", "", s)  # strip trailing tz offset
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            d = dt.datetime.strptime(s, fmt)
            return (dt.datetime.utcnow() - d).days
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Dedup — optional, against a seen.json you maintain.
# ---------------------------------------------------------------------------

def load_seen(path):
    """Load a seen.json describing roles already applied to / declined.

    Schema (all fields optional):
        {
          "applied_companies": ["Acme", "Foo Inc"],
          "declined_roles":    [{"company": "Acme", "title": "Senior TPM"}]
        }

    Returns (applied_companies: set, declined_roles: set of (company, title)),
    both normalized. Missing/invalid file → two empty sets.
    """
    applied, declined = set(), set()
    if not path or not os.path.exists(path):
        return applied, declined
    try:
        with open(path) as f:
            data = json.load(f)
    except (ValueError, OSError):
        return applied, declined
    for c in data.get("applied_companies", []):
        applied.add(normalize_company(str(c)))
    for r in data.get("declined_roles", []):
        company = normalize_company(str(r.get("company", "")))
        title = normalize_title(str(r.get("title", "")))
        if company and title:
            declined.add((company, title))
    return applied, declined


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def discover(max_age_days=None, seen_path=None):
    if max_age_days is None:
        max_age_days = config.DEFAULT_MAX_AGE_DAYS

    with open(YAML_PATH) as f:
        targets = yaml.safe_load(f) or []
    verified = [e for e in targets if e.get("status") == "verified"]

    applied_companies, declined_roles = load_seen(seen_path)

    stats = {
        "companies_queried": len(verified), "companies_failed": 0,
        "roles_seen": 0, "title_pass": 0, "location_pass": 0,
        "fresh_pass": 0, "dedup_pass": 0,
    }
    candidates = []

    import title_filter
    with concurrent.futures.ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as ex:
        for company, slug, ats, roles in ex.map(_query_company, verified):
            if roles is None:
                stats["companies_failed"] += 1
                continue
            stats["roles_seen"] += len(roles)
            for r in roles:
                ok, reason = title_filter.matches(r["title"])
                if not ok:
                    continue
                stats["title_pass"] += 1

                loc = classify_location(r["location"])
                if loc == "reject":
                    continue
                if loc == "conditional" and config.CONDITIONAL_SALARY_FLOOR:
                    sp = r.get("salary_parsed")
                    if sp and sp.get("max", 0) < config.CONDITIONAL_SALARY_FLOOR:
                        continue  # salary listed and entirely below the floor
                    if not sp:
                        r["needs_salary_review"] = True  # surface, flag to verify
                stats["location_pass"] += 1

                age = _age_days(r["updated_at"])
                if age is not None and age > max_age_days:
                    continue
                stats["fresh_pass"] += 1

                key_company = normalize_company(r["company"])
                key_title = normalize_title(r["title"])
                if key_title and (key_company, key_title) in declined_roles:
                    continue  # exact role already declined
                if key_company in applied_companies:
                    # Second role at a company you've applied to: keep, but flag.
                    r["dedup_note"] = "company already applied — verify different role"
                stats["dedup_pass"] += 1

                r["location_tier"] = loc
                r["age_days"] = age
                r["title_match_reason"] = reason
                candidates.append(r)

    candidates.sort(key=lambda r: (
        config.RANK_ORDER.get(r["location_tier"], 9),
        r["age_days"] if r["age_days"] is not None else 999,
    ))
    return {"candidates": candidates, "stats": stats}


def main():
    p = argparse.ArgumentParser(description="Parallel ATS job-discovery engine.")
    p.add_argument("--pretty", action="store_true", help="human-readable summary")
    p.add_argument("--max-age", type=int, default=config.DEFAULT_MAX_AGE_DAYS,
                   help=f"max age in days (default {config.DEFAULT_MAX_AGE_DAYS})")
    p.add_argument("--seen", default=None,
                   help="path to a seen.json to dedupe against")
    args = p.parse_args()

    result = discover(max_age_days=args.max_age, seen_path=args.seen)
    if args.pretty:
        s = result["stats"]
        print(f"Queried {s['companies_queried']} companies ({s['companies_failed']} failed)")
        print(f"  {s['roles_seen']} roles seen")
        print(f"  {s['title_pass']} passed title filter")
        print(f"  {s['location_pass']} passed location filter")
        print(f"  {s['fresh_pass']} passed freshness filter (<{args.max_age}d)")
        print(f"  {s['dedup_pass']} passed dedup")
        print()
        for c in result["candidates"]:
            note = f"  [{c['dedup_note']}]" if c.get("dedup_note") else ""
            age = f"{c['age_days']}d" if c.get("age_days") is not None else "age?"
            print(f"  [{c['location_tier']:<12}] [{age:>5}] {c['company']:<20} {c['title']}")
            print(f"  {'':<23} {c['location']}")
            print(f"  {'':<23} {c['url']}{note}")
            print()
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
