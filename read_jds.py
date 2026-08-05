#!/usr/bin/env python3
"""Walk per-company application folders and emit normalized role titles.

Part of the folder/tracker layer of the pipeline. Where the discovery engine
finds roles, this reads the state you already have on disk: one folder per
company you've pursued, each holding that role's JD (and later your resume and
cover letter). `dedup_check.py` uses this to drop roles you've already applied
to.

Scope: top-level company folders under the given root. Skips the
`Pending-applications/` staging area, the `Rejected/` archive, hidden folders,
and `__pycache__`. Resume and cover-letter files are ignored — only JD `.docx`
files contribute a title.

Output: JSON to stdout.
  {
    "<Company>": {"files": ["<name>.docx", ...], "titles": ["<normalized>", ...]},
    ...
  }

Run:
  python3 read_jds.py                 # folders in the current directory
  python3 read_jds.py --root /path    # folders under /path
  python3 read_jds.py --pretty
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import zipfile

# Canonical normalizers — one source of truth, shared with discover.py's dedup.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from normalize import normalize_company, normalize_title  # noqa: E402

_WS_RX = re.compile(r"\s+")

# Filename-side noise: a trailing " JD ..." suffix people add when they save the
# JD-only doc under a descriptive name (e.g. "Senior TPM JD Acme.docx").
_FILENAME_JD_RX = re.compile(r"\s+jd\b.*$", re.IGNORECASE)


def normalize_filename_title(fname: str) -> str:
    """Normalize a JD filename to a comparable title: strip the .docx extension
    and any trailing ' JD ...' suffix, then apply the canonical title rules."""
    s = (fname or "").lower().strip()
    if s.endswith(".docx"):
        s = s[: -len(".docx")]
    s = _FILENAME_JD_RX.sub("", s)
    return normalize_title(s)


def extract_docx_text(path: str, max_chars: int = 2000) -> str:
    """Pull the plain text out of a .docx (a zip of XML) without any dependency."""
    try:
        with zipfile.ZipFile(path) as z:
            with z.open("word/document.xml") as f:
                xml = f.read().decode("utf-8", "ignore")
    except (zipfile.BadZipFile, KeyError, FileNotFoundError, OSError):
        return ""
    text = re.sub(r"<[^>]+>", " ", xml)
    return _WS_RX.sub(" ", text).strip()[:max_chars]


# Best-effort title pulled from a JD body. Conservative — matches only the
# canonical TPM shape (optional seniority + optional ", Specialty"), so it never
# invents a title from arbitrary prose. Adjust the pattern for a different role.
_TPM_TITLE_RX = re.compile(
    r"\b((?:Sr\.?\s+|Senior\s+|Staff\s+|Lead\s+|Principal\s+)?"
    r"Technical\s+Program\s+Manager"
    r"(?:,\s*[A-Z][\w &/-]{2,40})?)\b"
)


def guess_body_title(text: str) -> str | None:
    m = _TPM_TITLE_RX.search(text)
    return m.group(1).strip() if m else None


SKIP_DIRS = {"Pending-applications", "Rejected", "__pycache__", "tests"}
SKIP_FILE_PREFIXES = ("~$", ".")
RESUME_RX = re.compile(r"resume", re.IGNORECASE)
COVER_RX = re.compile(r"cover\s*letter", re.IGNORECASE)


def collect_jds(root: str) -> dict:
    out: dict[str, dict] = {}
    if not os.path.isdir(root):
        print(f"read_jds: root not a directory: {root}", file=sys.stderr)
        return out
    for entry in sorted(os.listdir(root)):
        if entry.startswith(SKIP_FILE_PREFIXES) or entry in SKIP_DIRS:
            continue
        company_dir = os.path.join(root, entry)
        if not os.path.isdir(company_dir):
            continue
        files: list[str] = []
        titles: set[str] = set()
        for fname in sorted(os.listdir(company_dir)):
            if not fname.lower().endswith(".docx"):
                continue
            if fname.startswith(SKIP_FILE_PREFIXES):
                continue
            if RESUME_RX.search(fname) or COVER_RX.search(fname):
                continue
            files.append(fname)
            titles.add(normalize_filename_title(fname))  # source 1: the filename
            body_title = guess_body_title(
                extract_docx_text(os.path.join(company_dir, fname))
            )
            if body_title:  # source 2: best-effort body parse
                titles.add(normalize_title(body_title))
        if files:
            out[entry] = {"files": files, "titles": sorted(titles)}
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--root",
        default=os.path.dirname(os.path.abspath(__file__)),
        help="Directory containing per-company folders (default: this script's directory).",
    )
    p.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    args = p.parse_args()
    result = collect_jds(os.path.abspath(args.root))
    print(json.dumps(result, indent=2 if args.pretty else None, ensure_ascii=False))


if __name__ == "__main__":
    main()
