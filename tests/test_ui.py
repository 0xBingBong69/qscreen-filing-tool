"""UI/UX tests: shared design system, engine progress hook, the SSE extract
stream, the client-only dark toggle, and the CLI color/quiet/json helpers.

All offline — no network, no API key, no real PDF."""
from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest

import qscreen_ui
import qscreen_ingest as eng
import qscreen_app as app_mod
import qscreen_report
import qscreen_statements
import qscreen_portfolio


@pytest.fixture
def client():
    app_mod.app.config["TESTING"] = True
    return app_mod.app.test_client()


def _statement_filing():
    li = [{"account_code": c, "label_verbatim": c, "value": v,
           "comparatives": [{"period_label": "2022", "value": v}]}
          for c, v in [("IS_NET_INCOME", 15000), ("BS_TOTAL_EQUITY", 100000)]]
    return {"metadata": {"symbol": "QNBK", "fiscal_year": 2023, "fiscal_period": "FY", "currency": "QAR"},
            "audit": {"opinion_type": "unqualified"},
            "statements": [{"type": "income_statement", "title": "IS", "period_label": "2023",
                            "verbatim_text": "x", "line_items": li}], "segments": [], "notes": []}


# ── Shared design system ──────────────────────────────────────────────────────

def test_ui_css_has_tokens_and_components():
    css = qscreen_ui.css()
    assert css and ".pos{" in css and ".neg{" in css and "--pos" in css
    assert "prefers-color-scheme:dark" in css           # OS dark mode
    assert ".theme-dark" in css                         # manual override
    assert "@media print" in css                        # print stays light-on-white
    assert "prefers-reduced-motion" in css              # motion safety


@pytest.mark.parametrize("html", [
    qscreen_statements.render_statements_html(_statement_filing()),
    qscreen_portfolio.render_html(qscreen_portfolio.roll_up({"QNBK": [_statement_filing()]}, {})),
    qscreen_report.build_report("QNBK", [_statement_filing()], None)["html"],
])
def test_reports_inline_shared_tokens(html):
    assert html.startswith("<!doctype html>")
    assert "<html lang='en'>" in html                   # a11y: language declared
    assert "<style>" in html and "var(--" in html        # consume the shared tokens
    assert ".pos{color:var(--pos)}" in html              # shared palette inlined


# ── Engine progress hook (additive; default None = unchanged) ─────────────────

def _guided_args():
    return SimpleNamespace(guided=True, no_llm=True, pages_per_chunk=8, overlap=0,
                           year=2024, guided_notes=False)


def _pages(n):
    return [{"num": i, "text": f"Revenue {i*100}\nNet profit {i*10}"} for i in range(1, n + 1)]


def test_extract_filing_emits_ordered_progress_events(capsys):
    events = []
    eng.extract_filing(_pages(16), _guided_args(), events.append)
    extracting = [e for e in events if e["stage"] == "extracting"]
    n = len(extracting)
    assert n >= 2                                        # multi-page filing → several windows
    # One event per window, in order, then a single assembling event.
    assert [e["stage"] for e in events] == ["extracting"] * n + ["assembling"]
    assert [e["window"] for e in extracting] == list(range(1, n + 1))
    assert all(e.get("total") == n for e in extracting)
    # The human-facing prints must still happen alongside the events.
    assert f"window 1/{n}" in capsys.readouterr().out


def test_extract_filing_without_callback_is_unchanged():
    # Omitting progress_cb (every CLI/test call site) must just work.
    out = eng.extract_filing(_pages(8), _guided_args())
    assert isinstance(out, dict) and "statements" in out


# ── SSE extract stream ────────────────────────────────────────────────────────

def _frames(resp):
    return [json.loads(l[5:]) for l in resp.get_data(as_text=True).splitlines() if l.startswith("data:")]


def _post_stream(client):
    return client.post("/extract/stream",
                       data={"symbol": "QNBK", "subsector": "Commercial Bank", "year": "2024",
                             "period": "FY", "provider": "openai",
                             "pdf": (io.BytesIO(b"%PDF-1.4 fake"), "x.pdf")},
                       content_type="multipart/form-data")


def test_extract_stream_emits_events_and_done(client, monkeypatch):
    monkeypatch.setattr(app_mod.engine, "resolve_provider",
                        lambda args: {"name": "openai", "model": "gpt-4o", "local": False,
                                      "base_url": "x", "kind": "openai", "key": "k"})
    monkeypatch.setattr(app_mod.engine, "pdf_to_pages",
                        lambda path, progress_cb=None: ([{"num": 1, "text": "x"}], "sha"))

    def fake_extract(pages, args, progress_cb=None):
        app_mod.engine._emit(progress_cb, stage="extracting", window=1, total=1, message="only")
        return _statement_filing()
    monkeypatch.setattr(app_mod.engine, "extract_filing", fake_extract)

    r = _post_stream(client)
    assert r.status_code == 200 and "text/event-stream" in r.content_type
    evs = _frames(r)
    assert any(e["stage"] == "extracting" for e in evs)
    assert any(e["stage"] == "validating" for e in evs)
    done = [e for e in evs if e["stage"] == "done"]
    assert done and "filing" in done[0]["result"] and "summary" in done[0]["result"]


def test_extract_stream_error_event_hides_traceback(client, monkeypatch):
    monkeypatch.setattr(app_mod.engine, "resolve_provider",
                        lambda args: {"name": "openai", "model": "gpt-4o", "local": False,
                                      "base_url": "x", "kind": "openai", "key": "k"})
    monkeypatch.setattr(app_mod.engine, "pdf_to_pages",
                        lambda path, progress_cb=None: ([{"num": 1, "text": "x"}], "sha"))

    def boom(pages, args, progress_cb=None):
        raise ValueError("parse failed near /home/secret line 42")
    monkeypatch.setattr(app_mod.engine, "extract_filing", boom)

    evs = _frames(_post_stream(client))
    err = [e for e in evs if e["stage"] == "error"]
    assert err and "ValueError" in err[0]["error"]
    assert all("Traceback" not in e.get("error", "") and 'File "' not in e.get("error", "") for e in evs)


# ── Dark-mode toggle is purely client-side ────────────────────────────────────

def test_dark_toggle_is_client_only(client):
    a = client.get("/").get_data(as_text=True)
    b = client.get("/").get_data(as_text=True)
    assert a == b                                        # no server-side theme state
    assert "qscreen-theme" in a                          # persistence key present
    assert "theme-dark" in a and 'id="themetoggle"' in a  # toggle wired client-side


# ── CLI color / quiet / json helpers ──────────────────────────────────────────

def test_color_is_plain_without_a_tty():
    eng._color.off = False
    assert eng._color("x", "red") == "x"                 # pytest stdout isn't a TTY


def test_color_emits_ansi_on_a_tty(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(eng.sys, "stdout", SimpleNamespace(isatty=lambda: True))
    eng._color.off = False
    assert eng._color("x", "red") == "\033[31mx\033[0m"
    eng._color.off = True
    assert eng._color("x", "red") == "x"                 # --no-color wins
    eng._color.off = False


def test_no_color_env_disables_color(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr(eng.sys, "stdout", SimpleNamespace(isatty=lambda: True))
    eng._color.off = False
    assert eng._color("x", "green") == "x"


def test_say_respects_quiet(capsys):
    eng._say.quiet = True
    eng._say("hidden")
    assert capsys.readouterr().out == ""
    eng._say.quiet = False
    eng._say("shown")
    assert "shown" in capsys.readouterr().out


def test_cli_progress_json_writes_jsonl(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(eng.sys, "stderr", buf)
    cb = eng._cli_progress(SimpleNamespace(json_status=True, quiet=False))
    cb({"stage": "extracting", "window": 2, "total": 5})
    assert json.loads(buf.getvalue().strip())["window"] == 2


def test_cli_progress_quiet_rewrites_one_line(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(eng.sys, "stderr", buf)
    cb = eng._cli_progress(SimpleNamespace(json_status=False, quiet=True))
    cb({"stage": "extracting", "window": 3, "total": 8})
    assert buf.getvalue().startswith("\r")               # carriage-return, in-place update


def test_cli_progress_default_is_none():
    assert eng._cli_progress(SimpleNamespace(json_status=False, quiet=False)) is None
