"""Tests for the printable financial-statements document."""
from __future__ import annotations

import qscreen_statements as st


def _filing():
    def li(c, lab, v, pv, depth=0, sub=False):
        return {"account_code": c, "label_verbatim": lab, "value": v, "depth": depth,
                "is_subtotal": sub, "comparatives": [{"period_label": "2022", "value": pv}]}
    return {"metadata": {"symbol": "QNBK", "company_name": "Qatar National Bank",
                         "fiscal_year": 2023, "fiscal_period": "FY", "currency": "QAR",
                         "unit_scale": 1000000, "reporting_framework": "IFRS", "consolidated": True,
                         "source_file": "qnb.pdf"},
            "audit": {"auditor_name": "KPMG", "opinion_type": "unqualified"},
            "statements": [
                {"type": "income_statement", "title": "Income Statement", "period_label": "2023",
                 "line_items": [li("IS_NET_INTEREST", "Net interest income", 27800, 25600),
                                li("IS_NET_INCOME", "Profit for the year", 15502, 14347, sub=True)]},
                {"type": "balance_sheet", "title": "Financial Position", "period_label": "2023",
                 "line_items": [li("BS_LOANS", "Loans and advances", 830000, 810000, depth=1)]}],
            "segments": [{"dimension": "geography", "name": "Turkey", "currency": "TRY",
                          "period_label": "2023", "metrics": {"net_profit": -600}}],
            "notes": [{"number": "1", "title": "Basis of preparation",
                       "category": "accounting_policies", "verbatim_text": "Prepared under IFRS."}]}


def test_renders_full_document():
    h = st.render_statements_html(_filing())
    assert h.startswith("<!doctype html>") and h.rstrip().endswith("</html>")
    # header + every section present
    assert "Qatar National Bank" in h and "QNBK" in h and "KPMG" in h and "millions" in h
    assert "Income Statement" in h and "Statement of Financial Position" in h
    assert "Profit for the year" in h and "Segments" in h and "Notes" in h


def test_period_columns_and_number_formatting():
    h = st.render_statements_html(_filing())
    assert "<th>2023</th>" in h and "<th>2022</th>" in h     # current + comparative columns
    assert "27,800" in h and "25,600" in h                   # thousands separators
    assert "(600)" in h                                      # negative shown in parentheses


def test_subtotal_and_indent_markup():
    h = st.render_statements_html(_filing())
    assert "tr class='sub'" in h                             # subtotal row styled
    assert "padding-left:24px" in h                          # depth=1 line indented (1*16+8)


def test_html_escaping():
    f = _filing()
    f["metadata"]["company_name"] = "A & B <Bank>"
    h = st.render_statements_html(f)
    assert "A &amp; B &lt;Bank&gt;" in h and "<Bank>" not in h


def test_handles_thin_filing():
    thin = {"metadata": {"symbol": "X", "fiscal_year": 2023, "fiscal_period": "FY"},
            "statements": [{"type": "income_statement", "title": "IS", "period_label": "2023",
                            "line_items": []}]}
    h = st.render_statements_html(thin)                      # no crash, no segments/notes sections
    assert "Income Statement" in h and "Segments" not in h and "Notes" not in h


def test_save_statements_html(tmp_path):
    out = tmp_path / "s.html"
    st.save_statements_html(_filing(), str(out))
    assert out.read_text(encoding="utf-8").startswith("<!doctype html>")
