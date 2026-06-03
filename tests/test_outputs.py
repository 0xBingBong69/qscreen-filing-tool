"""Tests for write_outputs — the offline artifact writer used after extraction."""
from __future__ import annotations

import types

import qscreen_ingest as e


def _filing():
    def li(c, v, pv):
        return {"account_code": c, "label_verbatim": c, "value": v,
                "comparatives": [{"period_label": "2022", "value": pv}]}
    f = e.empty_filing()
    f["metadata"].update(symbol="QNBK", company_name="QNB", sector="islamic_bank",
                         fiscal_year=2023, fiscal_period="FY", currency="QAR", unit_scale=1_000_000)
    f["statements"] = [{"type": "income_statement", "title": "IS", "period_label": "2023",
                        "verbatim_text": "x", "line_items": [
                            li("IS_NET_INCOME", 15502, 14347), li("IS_NET_INTEREST", 27800, 25600),
                            li("BS_TOTAL_ASSETS", 1_200_000, 1_150_000),
                            li("BS_TOTAL_EQUITY", 105_000, 100_000)]}]
    return f


def _args(**kw):
    base = dict(symbol="QNBK", year=2023, period="FY", sector="islamic_bank", export=None,
                analyze=False, with_analysis=False, report=False, price=None, shares=None, _profile=None)
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_nothing_written_by_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    written, artifacts = e.write_outputs(_filing(), _args())
    assert written == [] and artifacts is None
    assert not list(tmp_path.iterdir())


def test_all_exports_written(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    written, _ = e.write_outputs(_filing(), _args(export=["csv", "xlsx", "html"]))
    names = {p.split("_", 3)[-1] for p in written}
    assert names == {"filing.csv", "filing.xlsx", "statements.html"}
    for p in written:
        assert (tmp_path / p).exists() and (tmp_path / p).stat().st_size > 0


def test_report_written(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    written, _ = e.write_outputs(_filing(), _args(report=True, price=16.0, shares=9230.0))
    assert "QNBK_2023_FY_report.html" in written and "QNBK_2023_FY_report.md" in written
    assert (tmp_path / "QNBK_2023_FY_report.html").read_text(encoding="utf-8").startswith("<!doctype html>")


def test_analysis_written_and_returned(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    written, artifacts = e.write_outputs(_filing(), _args(analyze=True))
    assert (tmp_path / "QNBK_2023_FY_analysis.json").exists()
    assert isinstance(artifacts, dict) and artifacts.get("analysis")


def test_one_command_everything(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    written, _ = e.write_outputs(_filing(), _args(export=["csv", "xlsx", "html"], analyze=True,
                                                  report=True, price=16.0, shares=9230.0))
    # csv + xlsx + statements + analysis + report.html + report.md (valuation is optional)
    assert len(written) >= 6
    for p in written:
        assert (tmp_path / p).exists()
