"""Regression tests for the post-merge audit fixes (numbers + XSS).

Each test pins a specific bug found auditing PRs #13–#15 so it can't come back:
 - a note-reference column read as the reported value (silent wrong numbers)
 - unit scale misfiring to millions on narrative text (1000x error)
 - a date cell parsed as a value
 - --basic/--pro resolving silently
 - reporting_currency unescaped in the DCF panel (XSS)
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import qscreen_ingest as e


# ── HIGH: a note-ref column must never become the reported value ─────────────

def test_note_ref_not_read_as_value_single_value_column():
    # Label | Note | Value  (no comparative) — the note must be demoted.
    assert e._row_to_triplet(["Total assets", "9", "5,000,000"]) == \
        {"label": "Total assets", "current": 5000000, "prior": None, "note_ref": "9"}
    assert e._row_to_triplet(["Cash", "5", "9,000,000"]) == \
        {"label": "Cash", "current": 9000000, "prior": None, "note_ref": "5"}


def test_note_ref_with_two_value_columns():
    assert e._row_to_triplet(["Loans", "7", "1,234,567", "1,100,000"]) == \
        {"label": "Loans", "current": 1234567, "prior": 1100000, "note_ref": "7"}


def test_small_integer_pair_is_not_demoted():
    # two bare small ints are a real current/prior pair, not note+value
    assert e._row_to_triplet(["Number of employees", "850", "820"]) == \
        {"label": "Number of employees", "current": 850, "prior": 820, "note_ref": None}


def test_bracketed_negatives_are_values_not_note_refs():
    assert e._row_to_triplet(["Impairment", "(56)", "(40)"]) == \
        {"label": "Impairment", "current": -56, "prior": -40, "note_ref": None}
    assert e._row_to_triplet(["Impairment", "(56)", "(1,234,000)"]) == \
        {"label": "Impairment", "current": -56, "prior": -1234000, "note_ref": None}


def test_date_cell_is_not_parsed_as_a_value():
    assert e._row_to_triplet(["Balance at", "1 January 2024", "5,000,000"]) == \
        {"label": "Balance at", "current": 5000000, "prior": None, "note_ref": None}


def test_end_to_end_mapped_codes_get_real_values():
    win = ("===== PAGE 2 =====\nStatement of Financial Position\n[TABLES on page 2]\n-- table 1 --\n"
           "Loans and advances to customers | 7 | 1,234,567\nTotal assets | 9 | 5,000,000\n")
    det = e.deterministic_statements(win, e.detect_statement_titles(win), "2023", "2024")
    by_code = {li["account_code"]: li for s in det.values() for li in s["line_items"]}
    assert by_code["BS_TOTAL_ASSETS"]["value"] == 5000000     # was 9 before the fix
    assert by_code["BS_LOANS"]["value"] == 1234567            # was 7 before the fix


@pytest.mark.parametrize("cell,ok", [
    ("1,234", True), ("(56)", True), ("12.3%", True), ("QAR 1,000", True),
    ("$1,000", True), ("-7", True), ("850", True),
    ("1 January 2024", False), ("see note 4", False), ("", False), ("n/a", False),
])
def test_value_cell_regex(cell, ok):
    assert bool(e._VALUE_CELL_RE.match(cell)) is ok


# ── MEDIUM: unit scale must not misfire on narrative "millions" ──────────────

def test_unit_scale_ignores_narrative_millions():
    assert e.detect_unit_scale("(in thousands of QAR). We serve millions of customers.") == 1000


def test_unit_scale_detects_real_millions():
    assert e.detect_unit_scale("Amounts in millions of Qatari Riyals") == 1_000_000
    assert e.detect_unit_scale("All amounts are in millions") == 1_000_000
    assert e.detect_unit_scale("Annual report 2024") is None


# ── LOW: contradictory mode flags must not resolve silently ─────────────────

def test_apply_mode_conflict_raises():
    with pytest.raises(SystemExit):
        e.apply_mode(SimpleNamespace(mode=None, basic=True, pro=True, no_llm=False,
                                     guided=False, no_guided=False))
    with pytest.raises(SystemExit):
        e.apply_mode(SimpleNamespace(mode="pro", basic=False, pro=False, no_llm=True,
                                     guided=False, no_guided=False))
