#!/usr/bin/env python3
"""qscreen_statements.py — a printable, human-readable financial-statements document.

Renders the *faithful* statements of one filing (income statement, balance sheet,
cash flow, …) as a clean HTML document you can read in a browser or print to PDF —
a third representation of the financials alongside the qscreen.app JSON (machine)
and the Excel transcript (data). Nothing is computed or invented; line items,
order, depth and comparatives come straight from the filing.

    from qscreen_statements import render_statements_html, save_statements_html
    save_statements_html(filing, "QNBK_2023_FY_statements.html")
"""
from __future__ import annotations

import html

import qscreen_ui

E = html.escape

_TITLE = {
    "income_statement": "Income Statement",
    "balance_sheet": "Statement of Financial Position",
    "cash_flow": "Statement of Cash Flows",
    "changes_in_equity": "Statement of Changes in Equity",
    "comprehensive_income": "Statement of Comprehensive Income",
}
_UNIT = {1: "actual units", 1000: "thousands", 1000000: "millions"}

# Page-specific rules only; palette, tables, .muted and .cat come from
# qscreen_ui.css() (prepended below). Keep the tr.sub / td.num selectors.
_CSS = """
body{font:14px/1.5 var(--font);max-width:840px;margin:24px auto;padding:0 20px}
h1{font-size:23px;margin:0 0 2px} h2{font-size:16px;margin:0 0 2px}
.sub{color:var(--fg-soft);margin:0 0 2px} .muted{font-size:12px;margin:2px 0}
header{border-bottom:2px solid var(--fg);padding-bottom:10px;margin-bottom:8px}
section{margin:26px 0}
.unit{color:var(--muted);font-size:11px;font-style:italic;margin:0 0 4px}
table{font-size:13px;margin:4px 0}
th{border-bottom:1px solid var(--border)}
tr.sub td{font-weight:700;border-top:1px solid var(--border)}
td.num{font-variant-numeric:tabular-nums;white-space:nowrap}
.note{margin:10px 0} .note p{margin:3px 0}
@media print{section{page-break-inside:avoid} h2{page-break-after:avoid} body{margin:0}}
"""


def _fmt(x) -> str:
    if x is None or x == "":
        return ""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return E(str(x))
    s = f"{abs(v):,.0f}" if abs(v) >= 100 else f"{abs(v):,.2f}"
    return f"({s})" if v < 0 else s


def _prior_labels(st: dict) -> list:
    out: list = []
    for li in st.get("line_items") or []:
        for c in (li.get("comparatives") or []):
            pl = c.get("period_label") if isinstance(c, dict) else None
            if pl and pl not in out:
                out.append(pl)
    return out


def _statement_section(st: dict, unit_note: str) -> str:
    title = _TITLE.get(st.get("type")) or st.get("title") or st.get("type") or "Statement"
    cur = st.get("period_label") or "Current"
    priors = _prior_labels(st)
    h = [f"<section><h2>{E(str(title))}</h2>"]
    if st.get("title") and st.get("title") != title:
        h.append(f"<p class='muted'>{E(st['title'])}</p>")
    h.append(f"<p class='unit'>Figures in {unit_note}</p><table>")
    h.append("<tr><th>Line item</th><th>" + E(str(cur)) + "</th>"
             + "".join(f"<th>{E(str(p))}</th>" for p in priors) + "</tr>")
    for li in st.get("line_items") or []:
        depth = int(li.get("depth") or 0)
        comp = {c.get("period_label"): c.get("value")
                for c in (li.get("comparatives") or []) if isinstance(c, dict)}
        cells = [f"<td class='num'>{_fmt(li.get('value'))}</td>"]
        cells += [f"<td class='num'>{_fmt(comp.get(p))}</td>" for p in priors]
        cls = " class='sub'" if li.get("is_subtotal") else ""
        label = E(str(li.get("label_verbatim") or ""))
        h.append(f"<tr{cls}><td style='padding-left:{depth * 16 + 8}px'>{label}</td>"
                 + "".join(cells) + "</tr>")
    h.append("</table></section>")
    return "".join(h)


def _segments_section(segs: list) -> str:
    mkeys = sorted({k for sg in segs for k in (sg.get("metrics") or {})})
    h = ["<section><h2>Segments</h2><table><tr><th>Dimension</th><th>Segment</th><th>Currency</th><th>Period</th>"]
    h += [f"<th>{E(str(k))}</th>" for k in mkeys] + ["</tr>"]
    for sg in segs:
        m = sg.get("metrics") or {}
        h.append("<tr><td>" + E(str(sg.get("dimension") or "")) + "</td><td>" + E(str(sg.get("name") or ""))
                 + "</td><td>" + E(str(sg.get("currency") or "")) + "</td><td>"
                 + E(str(sg.get("period_label") or "")) + "</td>"
                 + "".join(f"<td class='num'>{_fmt(m.get(k))}</td>" for k in mkeys) + "</tr>")
    return "".join(h) + "</table></section>"


def _notes_section(notes: list) -> str:
    h = ["<section><h2>Notes</h2>"]
    for nt in notes:
        head = ". ".join(p for p in [str(nt.get("number") or ""), E(str(nt.get("title") or ""))] if p)
        cat = f" <span class='cat'>{E(str(nt['category']))}</span>" if nt.get("category") else ""
        h.append(f"<div class='note'><b>{head}</b>{cat}<p>{E(str(nt.get('verbatim_text') or ''))}</p></div>")
    return "".join(h) + "</section>"


def render_statements_html(filing: dict) -> str:
    """A printable HTML document of the filing's financial statements."""
    meta, audit = filing.get("metadata") or {}, filing.get("audit") or {}
    name = meta.get("company_name") or meta.get("symbol") or "Financial statements"
    unit_note = _UNIT.get(meta.get("unit_scale"), "actual units")
    period = " ".join(str(p) for p in [meta.get("fiscal_year"), meta.get("fiscal_period")] if p)
    sub = " · ".join(str(p) for p in [meta.get("symbol"), period, meta.get("currency"),
                                      meta.get("reporting_framework"),
                                      "consolidated" if meta.get("consolidated") else None] if p)
    audit_line = ""
    if audit.get("auditor_name") or audit.get("opinion_type"):
        audit_line = "Auditor: " + " — ".join(str(p) for p in [audit.get("auditor_name"),
                                                                audit.get("opinion_type")] if p) + ". "
    out = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        f"<title>{E(str(name))} — financial statements</title>"
        f"<style>{qscreen_ui.css()}{_CSS}</style></head><body>",
        f"<header><h1>{E(str(name))}</h1><p class='sub'>{E(sub)}</p>",
        f"<p class='muted'>{E(audit_line)}Figures in {unit_note}"
        + (f". Source: {E(str(meta['source_file']))}" if meta.get("source_file") else "")
        + ". Faithful rendering — nothing computed.</p></header>",
    ]
    for st in filing.get("statements") or []:
        out.append(_statement_section(st, unit_note))
    if filing.get("segments"):
        out.append(_segments_section(filing["segments"]))
    if filing.get("notes"):
        out.append(_notes_section(filing["notes"]))
    out.append("</body></html>")
    return "".join(out)


def save_statements_html(filing: dict, path: str) -> str:
    from pathlib import Path
    Path(path).write_text(render_statements_html(filing), encoding="utf-8")
    return path
