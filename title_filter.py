"""Two-phase job-title filter.

A candidate title must match an INCLUDE pattern AND not match any EXCLUDE
pattern. Returns ``(matched: bool, reason: str)`` so the caller can log why
each role was kept or dropped.

The rules below target senior Technical Program Manager roles. They are just
regex lists — edit them for whatever role you're searching for.

Run ``python3 title_filter.py`` to execute the self-tests.
Run ``python3 title_filter.py "Some Title String"`` to check a single title.
"""

import re
import sys


INCLUDE_PATTERNS = [
    # Canonical TPM titles, any seniority modifier.
    r"\bTechnical\s+Program\s+Manager\b",

    # Senior-tier Program Manager. Bare "Program Manager" with no seniority
    # modifier does NOT match — that's coordinator territory.
    r"\b(?:Senior|Sr\.?|Staff|Lead)\s+Program\s+Manager\b",

    # "Sr. Project Manager, Engineering" — Project Manager only counts when
    # qualified by an engineering scope. Bare "Project Manager" is excluded.
    r"\b(?:Senior\s+|Sr\.?\s+|Staff\s+|Principal\s+|Lead\s+)?"
    r"Project\s+Manager[,\s]*(?:\(\s*Engineering\s*\)|Engineering\b)",

    # Technical Project Manager family — treated as TPM-equivalent. All other
    # filters (seniority cap, domain blocklist, AI veto) still apply.
    r"\b(?:Senior\s+|Sr\.?\s+|Staff\s+|Principal\s+|Lead\s+)?"
    r"Technical\s+Project\s+Manager\b",
]

EXCLUDE_PATTERNS = [
    # Engineering Program Manager — different role, hard veto.
    (r"\bEngineering\s+Program\s+Manager\b", "EPM (Engineering Program Manager)"),

    # Operational / non-TPM titles.
    (r"\bDelivery\s+Manager\b", "Delivery Manager"),
    (r"\bService\s+Delivery\b", "Service Delivery"),
    (r"\bProgram\s+Coordinator\b", "Program Coordinator"),
    (r"\bDirector[,\s]+Professional\s+Services\b", "Director of Professional Services"),
    (r"\bDirector\s+of\s+Professional\s+Services\b", "Director of Professional Services"),

    # Seniority too high — Principal is above Staff on most ladders.
    (r"\bPrincipal\b", "Principal-level (too senior)"),

    # Domain anti-keywords — non-engineering scopes that aren't TPM-equivalent
    # even at target companies. Keep this list reviewable; edit freely.
    (r"\bCustomer\s+Success\b", "Customer Success domain"),
    (r"\bSales\s+(?:Operations|Programs|Enablement|Strategy)\b", "Sales domain"),
    (r"\bGo[\s-]?To[\s-]?Market\b", "GTM domain"),
    (r"\bGTM\b", "GTM domain"),
    (r"\bMarketing\b", "Marketing domain"),
    (r"\bPeople\s+(?:Operations|Programs|Ops)\b", "People domain"),
    (r"\bTalent\s+(?:Acquisition|Programs)\b", "Talent/Recruiting domain"),
    (r"\bRecruiting\b", "Talent/Recruiting domain"),
    (r"\bWorkplace\b", "Workplace domain"),
    (r"\bFacilities\b", "Facilities domain"),
    (r"\bReal\s+Estate\b", "Real Estate domain"),
    (r"\bProcurement\b", "Procurement domain"),
    (r"\bLegal\b", "Legal domain"),
    (r"\bCommunications\b", "Communications domain"),
    (r"\bBrand\b", "Brand/Marketing domain"),

    # AI-heavy titles — filtered here as an example of a domain veto on the
    # TITLE (not the company). Delete this block if AI roles are in scope.
    (r"\bAI\b", "AI in title"),
    (r"\bML\b", "ML in title"),
    (r"\bMachine\s+Learning\b", "Machine Learning in title"),
    (r"\bGenAI\b", "GenAI in title"),
    (r"\bGenerative\s+AI\b", "Generative AI in title"),
    (r"\bLLM\b", "LLM in title"),
    (r"\bAgentic\b", "Agentic in title"),
    (r"\bMLOps\b", "MLOps in title"),
]


_includes = [re.compile(p, re.IGNORECASE) for p in INCLUDE_PATTERNS]
_excludes = [(re.compile(p, re.IGNORECASE), reason) for p, reason in EXCLUDE_PATTERNS]


def matches(title: str):
    """Return (True, "included: ...") or (False, "excluded/no-match: ...")."""
    for rx, reason in _excludes:
        if rx.search(title):
            return False, f"excluded: {reason}"
    for rx in _includes:
        m = rx.search(title)
        if m:
            return True, f"included: matched '{m.group(0)}'"
    return False, "no include pattern matched"


# -----------------------------------------------------------------------------
# Self-tests — run via `python3 title_filter.py`
# -----------------------------------------------------------------------------

POSITIVE_CASES = [
    "Technical Program Manager",
    "Senior Technical Program Manager",
    "Sr. Technical Program Manager",
    "Sr Technical Program Manager",
    "Staff Technical Program Manager",
    "Lead Technical Program Manager",
    "Senior Program Manager, Technical",
    "Senior Program Manager (Technical)",
    "Senior Program Manager",
    "Sr. Program Manager",
    "Sr Program Manager",
    "Staff Program Manager",
    "Lead Program Manager",
    "Sr. Project Manager, Engineering",
    "Sr Project Manager (Engineering)",
    "Senior Project Manager, Engineering Operations",
    "Technical Project Manager",
    "Senior Technical Project Manager",
    "Sr. Technical Project Manager",
    "Sr Technical Project Manager",
    "Staff Technical Project Manager",
    "Lead Technical Project Manager",
]

NEGATIVE_CASES = [
    "Engineering Program Manager",                              # EPM veto
    "Senior Engineering Program Manager",                       # EPM veto
    "Program Manager",                                          # bare, no seniority
    "Project Manager",                                          # no eng qualifier
    "Senior Project Manager",                                   # no eng qualifier
    "Delivery Manager",
    "Service Delivery Manager",
    "Program Coordinator",
    "Director, Professional Services",
    "Director of Professional Services & Technical Success",
    "Principal Technical Program Manager",                      # Principal too senior
    "Principal Program Manager",
    "Principal TPM, Infrastructure",
    "Principal Technical Project Manager",
    "Senior Program Manager, Customer Success Programs",        # domain veto
    "Senior Program Manager, Sales Operations",
    "Senior Program Manager, Marketing",
    "Sr Program Manager, GTM Strategy",
    "Staff Program Manager, People Operations",
    "Technical Program Manager, Talent Acquisition",
    "Senior Program Manager, Workplace",
    "Senior Program Manager, Legal Operations",
    "Sr TPM, Brand Marketing",
    "Sr Technical Program Manager – AI Delivery & Operations",  # AI-heavy
    "Senior Technical Program Manager, Generative AI",
    "Senior TPM, Agentic Personalization",
    "Senior Technical Program Manager, ML Platform",
    "TPM, MLOps",
    "Engineering Manager",                                      # not TPM
]


def _run_self_tests():
    fails = 0
    for t in POSITIVE_CASES:
        ok, reason = matches(t)
        marker = "PASS" if ok else "FAIL"
        if not ok:
            fails += 1
        print(f"[+] {marker}: {t!r} -> {reason}")
    for t in NEGATIVE_CASES:
        ok, reason = matches(t)
        marker = "PASS" if not ok else "FAIL"
        if ok:
            fails += 1
        print(f"[-] {marker}: {t!r} -> {reason}")
    print()
    total = len(POSITIVE_CASES) + len(NEGATIVE_CASES)
    print(f"{total - fails} / {total} passed")
    return fails == 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        title = " ".join(sys.argv[1:])
        ok, reason = matches(title)
        print(f"{'MATCH' if ok else 'SKIP '}: {title!r}")
        print(f"        {reason}")
    else:
        ok_all = _run_self_tests()
        sys.exit(0 if ok_all else 1)
