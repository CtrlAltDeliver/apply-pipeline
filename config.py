"""Tunable configuration for the discovery engine.

Everything a user would reasonably want to change lives here — freshness
window, salary floor, currency conversion, and the location-preference
ruleset. Edit these for your own search; the engine reads them at runtime.

The location ruleset shipped below encodes ONE example preference
(Canada-focused, Calgary-preferred, Ontario allowed above a salary floor).
Swap the regexes for your own geography — the classifier logic in
``discover.py`` is generic and does not hard-code any city.
"""

import re

# --- Freshness -------------------------------------------------------------
# Roles whose last-updated date is older than this are dropped.
DEFAULT_MAX_AGE_DAYS = 30

# --- HTTP ------------------------------------------------------------------
HTTP_TIMEOUT = 8
USER_AGENT = "Mozilla/5.0 (apply-pipeline)"
MAX_WORKERS = 12

# --- Salary ----------------------------------------------------------------
# Approximate FX used to normalize foreign salary bands into the home currency
# for the threshold check. Conservative by design.
USD_TO_CAD = 1.35
# Roles in the "conditional" location tier (see below) pass only if their
# listed salary clears this floor. Set to 0 to disable the salary gate.
CONDITIONAL_SALARY_FLOOR = 150_000

# --- Location ruleset ------------------------------------------------------
# The classifier assigns each role to a tier by matching its location string
# against these patterns, then the engine keeps/ranks tiers in RANK_ORDER.
#
# Tiers (edit the regexes, not the logic):
#   PREFERRED   — always pass, ranked first (e.g. your home city)
#   REMOTE_HOME — remote + a home-country signal → always pass
#   CONDITIONAL — pass only if salary clears CONDITIONAL_SALARY_FLOOR
#   HOME_OTHER  — other home-country cities; onsite here is rejected unless
#                 a remote signal is also present
#   FOREIGN     — explicit non-home signals → reject (unless a home signal
#                 also appears, i.e. a multi-location role hireable at home)

PREFERRED = re.compile(r"\bcalgary\b", re.IGNORECASE)

HOME_COUNTRY = re.compile(
    r"\bcanada\b|\b(?:alberta|ab)\b|\bremote[\s\-]*ca\b", re.IGNORECASE
)

REMOTE = re.compile(
    r"\bremote\b|\banywhere\b|\bworldwide\b|\bglobal\b", re.IGNORECASE
)

# Conditional tier — passes only with a qualifying salary.
# "London" deliberately omitted (ambiguous with London, UK).
CONDITIONAL = re.compile(
    r"\b(?:toronto|ottawa|waterloo|mississauga|hamilton|kitchener|"
    r"markham|brampton|vaughan|burlington|oakville|guelph|kingston|"
    r"windsor|thunder\s*bay|sudbury|ontario)\b",
    re.IGNORECASE,
)
# Standalone province abbreviation, careful not to match "On-site".
CONDITIONAL_ABBR = re.compile(r",\s*ON\b|\bON,\b", re.IGNORECASE)

HOME_OTHER = re.compile(
    r"\b(?:montreal|montr[eé]al|vancouver|edmonton|winnipeg|halifax|"
    r"quebec\s*city|qu[eé]bec)\b",
    re.IGNORECASE,
)

FOREIGN_COUNTRY = re.compile(
    r"\b(?:us|usa|u\.s\.|u\.s\.a\.|united\s+states)\b", re.IGNORECASE
)
FOREIGN_CITY = re.compile(
    r"\b(?:san francisco|new york|seattle|austin|chicago|atlanta|boston|"
    r"london|berlin|dublin|singapore|tokyo|sydney|mumbai|bangalore|"
    r"bengaluru|hyderabad|sao paulo|mexico city|tel aviv|amsterdam|paris|"
    r"madrid|barcelona|warsaw|krakow|budapest|prague|lisbon|cape town|"
    r"nairobi|lagos|buenos aires|santiago|bogota|lima)\b",
    re.IGNORECASE,
)

# Rank order for the output — lower sorts first.
RANK_ORDER = {"preferred": 0, "remote_home": 1, "conditional": 2, "maybe": 3}
