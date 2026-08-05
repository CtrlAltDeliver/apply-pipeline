"""Tests for the folder/tracker dedup layer (dedup_check.py).

Runnable two ways:
    pytest
    python3 tests/test_dedup_check.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from normalize import normalize_company  # noqa: E402
from dedup_check import classify, extract_ats_id  # noqa: E402


# --- normalize_company: the drift cases this layer depends on ---------------

def test_corporate_suffix_drift_collapses():
    assert normalize_company("Scribd Inc") == "scribd"
    assert normalize_company("Scribd Inc") == normalize_company("Scribd")
    assert normalize_company("Jane App") == normalize_company("Jane Software") == "jane"
    assert normalize_company("Smart Technologies") == normalize_company("Smart") == "smart"
    assert normalize_company("Foo Technologies Inc") == "foo"  # stacked strip


def test_suffix_only_names_are_not_stripped_to_empty():
    assert normalize_company("Labs") == "labs"
    assert normalize_company("Co") == "co"


def test_distinct_companies_stay_distinct():
    assert normalize_company("Stripe") != normalize_company("Shopify")


def test_trailing_parenthetical_qualifier_stripped():
    assert normalize_company("Acme (Parent Group)") == "acme"
    assert normalize_company("Acme (Parent Group)") == normalize_company("Acme")
    # Stacked and punctuated qualifiers collapse; a leading paren is kept.
    assert normalize_company("Foo (Bar) (Baz)") == "foo"
    assert normalize_company("(Foo) Bar") == "foo bar"


def test_sibling_brands_under_same_parent_stay_distinct():
    # The main risk of stripping the qualifier: sibling brands must not merge.
    assert normalize_company("Alpha (Shared Parent)") != normalize_company("Beta (Shared Parent)")


# --- extract_ats_id ---------------------------------------------------------

def test_extract_ats_id_families():
    assert extract_ats_id("https://boards.greenhouse.io/acme/jobs/123") == ("greenhouse", "123")
    assert extract_ats_id("https://acme.com/careers?gh_jid=123") == ("greenhouse", "123")
    assert extract_ats_id("https://www.linkedin.com/jobs/view/4432451863") == ("linkedin", "4432451863")
    assert extract_ats_id("https://example.com/no-id") == (None, None)


def test_linkedin_and_greenhouse_ids_are_separate_namespaces():
    _, gh = extract_ats_id("https://boards.greenhouse.io/acme/jobs/123")
    fam, li = extract_ats_id("https://www.linkedin.com/jobs/view/123")
    assert gh == li == "123" and fam == "linkedin"  # same number, different family


# --- classify: tracker layers ----------------------------------------------

def test_suffix_drift_surfaces_as_company_match_not_fresh():
    applied = [{"company": "Scribd", "title": "Some Other Role",
                "normalized_title": "some other role",
                "url": "https://example.com/old", "status": "Auto rejection"}]
    got = classify([{"company": "Scribd Inc", "title": "Senior Technical Program Manager",
                     "url": "https://linkedin.com/jobs/view/999"}],
                   folder_data={}, applied_rows=applied)[0]
    assert got["verdict"] == "company_match"
    assert got["match_source"] == "tracker_company"


def test_fresh_company_stays_fresh():
    got = classify([{"company": "Totally New Co", "title": "TPM", "url": "https://x/y"}],
                   folder_data={}, applied_rows=[])[0]
    assert got["verdict"] == "fresh"


def test_company_plus_title_exact_match_across_suffix_drift():
    applied = [{"company": "Scribd", "title": "Senior Technical Program Manager",
                "normalized_title": "senior technical program manager",
                "url": "https://example.com/a", "status": ""}]
    got = classify([{"company": "Scribd Inc", "title": "Sr. Technical Program Manager",
                     "url": "https://example.com/b"}],
                   folder_data={}, applied_rows=applied)[0]
    assert got["verdict"] == "exact_match"
    assert got["match_source"] == "tracker_title"


def test_url_layer_beats_title_layer():
    applied = [{"company": "Acme", "title": "Senior TPM",
                "normalized_title": "senior technical program manager",
                "url": "https://boards.greenhouse.io/acme/jobs/5", "status": ""}]
    got = classify([{"company": "Acme", "title": "Senior TPM",
                     "url": "https://boards.greenhouse.io/acme/jobs/5"}],
                   folder_data={}, applied_rows=applied)[0]
    assert got["match_source"] == "tracker_url"


# --- classify: folder layer -------------------------------------------------

def test_folder_company_match_honors_normalized_company():
    folder = {"Smart Technologies": {"files": ["JD.docx"], "titles": ["program manager"]}}
    got = classify([{"company": "Smart", "title": "Delivery Lead", "url": "https://x/z"}],
                   folder_data=folder, applied_rows=[])[0]
    assert got["verdict"] == "company_match"
    assert got["match_source"] == "folder_company"


def test_folder_title_exact_match():
    folder = {"Acme": {"files": ["Senior Technical Program Manager.docx"],
                       "titles": ["senior technical program manager"]}}
    got = classify([{"company": "Acme", "title": "Sr. Technical Program Manager",
                     "url": "https://x/z"}],
                   folder_data=folder, applied_rows=[])[0]
    assert got["verdict"] == "exact_match"
    assert got["match_source"] == "folder_title"


# --- classify: rejected-opportunities layers --------------------------------

REJECTED = [
    {"company": "Acme (Parent Group)", "title": "Senior Technical Program Manager",
     "normalized_title": "senior technical program manager",
     "url": "https://ca.linkedin.com/jobs/view/4385107850/", "reason": "Worth Applying = N"},
]


def test_rejected_opps_url_match_surfaces_reason():
    got = classify([{"company": "Acme", "title": "Senior Technical Program Manager",
                     "url": "https://ca.linkedin.com/jobs/view/4385107850/"}],
                   folder_data={}, applied_rows=[], rejected_opps_rows=REJECTED)[0]
    assert got["verdict"] == "exact_match"
    assert got["match_source"] == "rejected_opps_url"
    assert got["matched_tracker_row"]["rejected_reason"] == "Worth Applying = N"


def test_rejected_opps_ats_id_match_across_url_surfaces():
    # Same LinkedIn job ID, different URL surface (no slash / www host).
    got = classify([{"company": "Acme", "title": "Senior Technical Program Manager",
                     "url": "https://www.linkedin.com/jobs/view/4385107850"}],
                   folder_data={}, applied_rows=[], rejected_opps_rows=REJECTED)[0]
    assert got["match_source"] == "rejected_opps_ats_id"


def test_rejected_opps_title_match_catches_repost_under_new_id():
    # A repost with a brand-new job ID: only the company+title layer can catch it,
    # and only because normalize_company collapses "Acme (Parent Group)" == "Acme".
    got = classify([{"company": "Acme", "title": "Senior Technical Program Manager",
                     "url": "https://ca.linkedin.com/jobs/view/9999999999/"}],
                   folder_data={}, applied_rows=[], rejected_opps_rows=REJECTED)[0]
    assert got["verdict"] == "exact_match"
    assert got["match_source"] == "rejected_opps_title"


def test_different_role_at_rejected_company_stays_fresh():
    got = classify([{"company": "Acme", "title": "Staff Product Designer",
                     "url": "https://ca.linkedin.com/jobs/view/5555555555/"}],
                   folder_data={}, applied_rows=[], rejected_opps_rows=REJECTED)[0]
    assert got["verdict"] == "fresh"


def test_tracker_wins_over_rejected_opps():
    applied = [{"company": "Acme", "title": "Senior Technical Program Manager",
                "normalized_title": "senior technical program manager",
                "url": "https://ca.linkedin.com/jobs/view/4385107850/",
                "status": "Applied - awaiting response"}]
    got = classify([{"company": "Acme", "title": "Senior Technical Program Manager",
                     "url": "https://ca.linkedin.com/jobs/view/4385107850/"}],
                   folder_data={}, applied_rows=applied, rejected_opps_rows=REJECTED)[0]
    assert got["match_source"] == "tracker_url"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = []
    for fn in fns:
        try:
            fn()
        except AssertionError as e:
            failures.append(f"{fn.__name__}: {e}")
    if failures:
        print("FAILED:")
        for f in failures:
            print("  -", f)
        sys.exit(1)
    print(f"all {len(fns)} dedup_check tests passed")
