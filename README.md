# apply-pipeline

**A parallel job-discovery engine that queries 150+ company career sites at once, filters postings down to the handful actually worth your time, and prints them as ranked JSON.**

In one real run: **151 companies queried → 16,345 live roles scanned → 8 candidates** that passed title, location, freshness, and dedup filters.

The core is **structured-first**: no login, no paid API — just the public JSON endpoints every major applicant-tracking system already exposes. But not every company is on a supported ATS, and pretending otherwise would leave a blind spot. So there's a second, clearly-labelled layer: an **optional LinkedIn fallback** for the long tail the APIs can't reach. It's best-effort and flagged for manual verification — the structured data stays the source of truth, and the fallback only adds coverage it would otherwise miss. Handling the messy real-world tail honestly, rather than hiding it, is the point.

One fine day I got fed up of seeing the same old roles on LinkedIn that LinkedIn thought I was a good match for but I disagreed. I also didn't have enough hours in the day to search jobs, check each of them individually and then apply. So, I did what a good TPM should do, I stood up a roadmap, identified the blockers and dependencies, and built a solution for the problem. I still judge every role and decide which one's worth my time.

---

## Why it exists

Job boards are noisy and stale; company career pages are fresh but there are hundreds of them. This tool goes straight to the source — the ATS JSON APIs behind Greenhouse, Lever, Ashby, SmartRecruiters, Workday, and HiBob — and does the triage a person would do by hand, in about a minute:

1. **Fan out** across every company in `ats-targets.yaml`, in parallel.
2. **Normalize** six different ATS payload shapes into one common role record.
3. **Filter** by job title, location preference, and posting freshness.
4. **Dedupe** against roles you've already applied to or passed on.
5. **Rank and emit** the survivors as JSON (or a readable summary).

Optionally, a **fallback layer** (`--linkedin-fallback`) then queries LinkedIn's logged-out search for the same title/location rules and merges in any roles at companies not on a supported ATS — deduped against the structured pass, and tagged so you know to verify them by hand.

## Quickstart

```bash
pip install -r requirements.txt

python3 discover.py --pretty              # human-readable summary
python3 discover.py                       # full JSON on stdout
python3 discover.py --max-age 14          # only roles posted in last 14 days
python3 discover.py --seen seen.json      # skip roles you've already handled
python3 discover.py --linkedin-fallback   # + best-effort LinkedIn tail (verify by hand)
python3 linkedin_fallback.py --pretty     # run the fallback source on its own

# The folder/tracker layer (needs openpyxl; reads your .xlsx trackers + folders)
python3 discover.py > candidates.json
python3 dedup_check.py --candidates candidates.json --pretty   # drop applied/declined roles
python3 purge_opportunities.py                                 # clean the opportunities sheet
python3 promote_pending.py                                     # graduate applied folders + write tracker
```

Discovery requires Python 3.8+ and only PyYAML (the discovery itself uses the standard library). The folder/tracker layer additionally needs `openpyxl` to read the `.xlsx` trackers — both are in `requirements.txt`.

## How it works

| Stage | File | What it does |
|-------|------|--------------|
| Discovery | `discover.py` | Parallel HTTP against each ATS's public JSON endpoint; per-platform adapters normalize the results |
| Title filter | `title_filter.py` | Two-phase include/exclude regex ruleset, with 51 self-tests |
| Location + salary | `config.py` + `discover.py` | Tier-based location classifier; currency-aware salary parsing with an optional floor |
| Normalization | `normalize.py` | Canonicalizes titles and company names so dedup matches across source drift |
| Dedup (light) | `discover.py` | Optional `seen.json` of applied/declined roles |
| Fallback (optional) | `linkedin_fallback.py` | Best-effort LinkedIn source for companies not on a supported ATS; reuses the engine's title/location/salary rules and role schema |
| **Folder/tracker dedup** | `dedup_check.py` + `read_jds.py` | Classifies candidates against your real `.xlsx` trackers and on-disk company folders — URL / ATS-id / company+title layers — so an applied or declined role never resurfaces |
| **Backlog purge** | `purge_opportunities.py` | Sweeps the opportunities sheet: moves declined rows to an append-only rejected sheet, drops applied rows, renumbers |
| **Promotion** | `promote_pending.py` | Graduates a `Pending-applications/<Company>/` folder to applied once your resume lands in it, and writes the tracker row |

### Supported ATS platforms

Greenhouse · Lever · Ashby · SmartRecruiters · Workday · HiBob. Adding another is one adapter function that maps its payload to the common role dict. HiBob shows the pattern extends past plain JSON endpoints too: it sits behind a Cloudflare bot-check, so its adapter does a quick page-then-JSON cookie handshake first — still no login and no API key.

### The fallback layer (`linkedin_fallback.py`)

Structured JSON is the right source *when it exists*. It doesn't for every company — plenty run on custom career pages or hardened ATSes with no clean public API, and the structured pass is simply blind to them. Rather than hide that gap, the engine can degrade to a best-effort source: LinkedIn's logged-out job search.

It's kept separate and labelled a fallback on purpose, because it's a weaker kind of data:

- It **scrapes guest-view HTML** instead of reading a structured API, so it breaks when LinkedIn changes its markup (the JD-body extractor has already had to chase one such change).
- It **can't verify** a posting against the company's own careers page.
- Salary and location are **parsed heuristically**, not read from structured fields.

So the contract is explicit: **structured-first; fall back only for coverage the APIs can't reach, and treat that output as needing a human's verification.** To stay consistent, the fallback reuses `discover.py`'s own location classifier, salary parser, freshness check, title filter, and role-dict shape — it's a second *source*, not a second set of rules. Merged results are deduped against the structured pass and tagged `source: "linkedin_fallback"`. Enrichment is sorted by location tier before its cap, so the geographies you care about are never crowded out by the blank-location tail.

## Beyond discovery — the folder/tracker layer

Discovery finds roles; keeping them straight is the other half of the job. These
stages read your real state — a two-sheet opportunities spreadsheet, an
application tracker, and one folder per company you've pursued — so a role never
resurfaces once you've applied to it or reviewed and declined it. They ship here
now (they used to be private):

**Folder/tracker dedup** (`dedup_check.py` + `read_jds.py`). Classifies every
discovered candidate against three sources — your application tracker, the
`Rejected opportunities` sheet, and the on-disk company folders — in confidence
order: raw URL, then ATS-native job ID (the same role on a different URL surface),
then company + normalized title (a repost under a fresh ID, or a careers URL vs.
its LinkedIn mirror). A definite match is dropped; a same-company/different-role
is surfaced with a "verify this is a different role" flag. This is the heavier
sibling of the `seen.json` dedup built into `discover.py`.

**Backlog purge** (`purge_opportunities.py`). Sweeps the `Opportunities` sheet:
rows you marked not-worth-applying move to the append-only `Rejected
opportunities` sheet with a date and reason (so they never resurface), rows
you've already applied to are dropped, and `S.No` is renumbered. Columns are
mapped by name, so the two sheets can drift without misfiling a reason.

**Folders-as-state promotion** (`promote_pending.py`). The folder's location is
the state. A role you're preparing sits in `Pending-applications/<Company>/`;
the moment you drop your resume into it, this graduates the folder to the applied
set, writes an application-tracker row (`Date applied` = today, status applied,
carrying `Title`/`Link` from the matching opportunities row), and flags that
opportunities row `Applied = Y`. Deterministic file + spreadsheet ops.

**Still agent-driven, not in this repo:** one stage stays in the
[tpm-job-search-kit](https://github.com/CtrlAltDeliver/tpm-job-search-kit)
`/apply` skill rather than as a script here, because it needs a live connector,
not deterministic logic — the **inbox sweep** that reconciles your email against
the tracker (auto-confirmations, rejections, recruiter/interview invites →
status changes). The skill runs that with your connected Gmail.

Together these turn a scattered, manual search into a system with one source of
truth: every role has a known state, and nothing falls through the cracks.

## Related repos

This engine is one part of a three-repo set:

| Repo | What it is | How it fits |
|---|---|---|
| **[tpm-job-search-kit](https://github.com/CtrlAltDeliver/tpm-job-search-kit)** | The starter kit — folder scaffold, trackers, and an `/apply` skill that wraps this engine into the full end-to-end routine described in ["Beyond discovery"](#beyond-discovery--the-foldertracker-layer) above. | Start there if you want the whole pipeline, not just discovery. This repo drops into its `Job-applications-TPM/` folder. |
| **[tpm-toolkit](https://github.com/CtrlAltDeliver/tpm-toolkit)** | The wider set of TPM slash-command skills — `score`, `tailor`, `companyresearch`, `referrals`, `interview-prep`. | The skills that act on the roles this engine finds. |

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
python3 tests/test_filters.py             # discovery-engine filters  (or: pytest)
python3 tests/test_dedup_check.py         # folder/tracker dedup classification
python3 tests/test_purge_opportunities.py # opportunities-sheet purge
python3 tests/test_promote_pending.py     # folder promotion + tracker write
python3 title_filter.py                   # the title ruleset's own 51 self-tests
```

## Project layout

```
apply-pipeline/
├── discover.py             # the engine: fan-out, adapters, filters, ranking
├── linkedin_fallback.py    # optional best-effort source for the non-ATS tail
├── title_filter.py         # include/exclude title ruleset + self-tests
├── config.py               # all tunable knobs (freshness, salary, location)
├── normalize.py            # title/company canonicalization for dedup
├── read_jds.py             # walk per-company folders → normalized titles
├── dedup_check.py          # classify candidates vs. trackers + folders
├── purge_opportunities.py  # sweep the opportunities sheet (needs openpyxl)
├── promote_pending.py      # graduate applied folders + write the tracker
├── ats-targets.yaml        # the companies to search
├── seen.example.json       # template for your private applied/declined list
└── tests/
    ├── test_filters.py
    ├── test_dedup_check.py
    ├── test_purge_opportunities.py
    └── test_promote_pending.py
```

## License

MIT — see [LICENSE](LICENSE).
