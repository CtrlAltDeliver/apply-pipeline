# apply-pipeline

**A parallel job-discovery engine that queries 150+ company career sites at once, filters postings down to the handful actually worth your time, and prints them as ranked JSON.**

In one real run: **151 companies queried → 16,345 live roles scanned → 8 candidates** that passed title, location, freshness, and dedup filters. No scraping, no login, no paid API — just the public JSON endpoints every major applicant-tracking system already exposes.

One fine day I got fed up of seeing the same old roles on LinkedIn that LinkedIn thought I was a good match for but I disagreed. I also didn't have enough hours in the day to search jobs, check each of them individually and then apply. So, I did what a good TPM should do, I stood up a roadmap, identified the blockers and dependencies, and built a solution for the problem. I still judge every role and decide which one's worth my time.

---

## Why it exists

Job boards are noisy and stale; company career pages are fresh but there are hundreds of them. This tool goes straight to the source — the ATS JSON APIs behind Greenhouse, Lever, Ashby, SmartRecruiters, and Workday — and does the triage a person would do by hand, in about a minute:

1. **Fan out** across every company in `ats-targets.yaml`, in parallel.
2. **Normalize** five different ATS payload shapes into one common role record.
3. **Filter** by job title, location preference, and posting freshness.
4. **Dedupe** against roles you've already applied to or passed on.
5. **Rank and emit** the survivors as JSON (or a readable summary).

## Quickstart

```bash
pip install -r requirements.txt

python3 discover.py --pretty              # human-readable summary
python3 discover.py                       # full JSON on stdout
python3 discover.py --max-age 14          # only roles posted in last 14 days
python3 discover.py --seen seen.json      # skip roles you've already handled
```

Requires Python 3.8+ and a single dependency (PyYAML). The discovery itself uses only the standard library.

## How it works

| Stage | File | What it does |
|-------|------|--------------|
| Discovery | `discover.py` | Parallel HTTP against each ATS's public JSON endpoint; per-platform adapters normalize the results |
| Title filter | `title_filter.py` | Two-phase include/exclude regex ruleset, with 51 self-tests |
| Location + salary | `config.py` + `discover.py` | Tier-based location classifier; currency-aware salary parsing with an optional floor |
| Normalization | `normalize.py` | Canonicalizes titles and company names so dedup matches across source drift |
| Dedup | `discover.py` | Optional `seen.json` of applied/declined roles |

### Supported ATS platforms

Greenhouse · Lever · Ashby · SmartRecruiters · Workday. Adding another is one adapter function that maps its payload to the common role dict.

## Beyond discovery — the full pipeline

Discovery is the part that generalizes, so it's the part I open-sourced. In my own setup it's stage one of a larger pipeline that runs the whole application lifecycle end to end. The later stages are coupled to private data — my application spreadsheet, my per-company document folders, my email inbox — so they aren't in this repo, but here's what they do:

**Opportunity tracking.** Discovered roles land in a two-sheet spreadsheet: an active backlog and an append-only *rejected* sheet. When I mark a role not worth applying to, it moves to the rejected sheet with a date and a reason, so the same posting never resurfaces. Roles I've already applied to are dropped from the backlog entirely. (The `seen.json` in this repo is the stripped-down, shareable version of that memory.)

**Application folders as state.** Each role I pursue gets a folder holding its job description, my tailored resume, and a cover letter. A role starts in a `pending/` staging area; once the resume is in place, the folder graduates to the applied set and the tracker's status and date update automatically. A rejection moves the folder to a `rejected/` archive. The folder's location *is* the state — where a company sits on disk tells me exactly where it is in the funnel.

**Inbox sweep.** On a schedule, the pipeline scans my inbox since the last run and reconciles it against the tracker. Auto-confirmations, auto-rejections, recruiter outreach, assessment and interview invites, and offers each map to a status change; a post-interview rejection both updates the status and archives the folder. When a role advances to an interview stage, that transition kicks off a company-research step so prep starts on its own. Every run records where it stopped, so the next one resumes exactly there and no email is processed twice.

Together these turn a scattered, manual search into a system with one source of truth: every role has a known state, and nothing falls through the cracks.

## Configuration

All the knobs live in **`config.py`**:

- **`DEFAULT_MAX_AGE_DAYS`** — freshness window.
- **`CONDITIONAL_SALARY_FLOOR`** / **`USD_TO_CAD`** — salary gate and FX for the threshold check.
- **Location ruleset** — the shipped example is Canada-focused (Calgary preferred, Ontario allowed above a salary floor, elsewhere-onsite rejected). The classifier in `discover.py` is geography-agnostic; swap the regexes in `config.py` for your own search and nothing else changes.

**Companies to search** live in `ats-targets.yaml` — add or remove entries freely. Each maps a company to its ATS type and slug.

## Deduping against your own history

Copy `seen.example.json` to `seen.json` and keep it current:

```json
{
  "applied_companies": ["Acme Corp"],
  "declined_roles": [{ "company": "Acme Corp", "title": "Senior PM, Marketing" }]
}
```

`seen.json` is gitignored, so your search history stays private. A company you've applied to still surfaces *other* roles (flagged to verify it's a different one); an exact declined role is dropped.

## Tests

```bash
python3 tests/test_filters.py     # or: pytest
python3 title_filter.py           # the title ruleset's own 51 self-tests
```

## Project layout

```
apply-pipeline/
├── discover.py          # the engine: fan-out, adapters, filters, ranking
├── title_filter.py      # include/exclude title ruleset + self-tests
├── config.py            # all tunable knobs (freshness, salary, location)
├── normalize.py         # title/company canonicalization for dedup
├── ats-targets.yaml     # the companies to search
├── seen.example.json    # template for your private applied/declined list
└── tests/
    └── test_filters.py
```

## License

MIT — see [LICENSE](LICENSE).
