"""Tests for the .env loader (inline-comment handling) and the provider
self-diagnostic shown by --list-providers."""
from __future__ import annotations

import os

import pytest

import qscreen_ingest as e


_PROVIDER_ENV = ("MINIMAX_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY",
                 "ANTHROPIC_API_KEY", "MOONSHOT_API_KEY", "KIMI_API_KEY",
                 "OLLAMA_API_KEY", "LMSTUDIO_API_KEY", "LLAMACPP_API_KEY",
                 "JAN_API_KEY", "GPT4ALL_API_KEY", "MLX_API_KEY",
                 "QSCREEN_PROVIDER", "LLM_PROVIDER", "QSCREEN_MODEL", "LLM_API_KEY",
                 "QSCREEN_BASE_URL", "LLM_BASE_URL", "QSCREEN_GUIDED")


@pytest.fixture
def clean_env(monkeypatch):
    for k in _PROVIDER_ENV:
        monkeypatch.delenv(k, raising=False)


# ── _dotenv_value: inline comments / quotes ──────────────────────────────────

@pytest.mark.parametrize("raw,want", [
    ("sk-abc", "sk-abc"),
    ("sk-abc   # note", "sk-abc"),                 # the footgun: inline comment dropped
    ("  spaced  ", "spaced"),
    ('"a # b"', "a # b"),                           # quoted → '#' kept
    ("'x'", "x"),
    ("sk-a#b", "sk-a#b"),                           # '#' with no leading space is part of value
    ("# all comment", ""),                          # value that is only a comment
    ("", ""),
    ("sk-123 \t# tab-spaced note", "sk-123"),
])
def test_dotenv_value(raw, want):
    assert e._dotenv_value(raw) == want


# ── _parse_dotenv: the analyst's exact footgun + general cases ───────────────

def test_parse_dotenv_strips_inline_comment_like_template():
    text = (
        "# a comment line\n"
        "MINIMAX_API_KEY=sk-kimi-REDACTED          # minimax  get a key: https://platform.minimax.io/\n"
        "MOONSHOT_API_KEY=sk-moon-xyz   # kimi\n"
        "export OPENAI_API_KEY=sk-oai\n"
        "EMPTY=\n"
        'QUOTED="v # not-a-comment"\n'
    )
    env = e._parse_dotenv(text)
    assert env["MINIMAX_API_KEY"] == "sk-kimi-REDACTED"      # comment + trailing spaces gone
    assert env["MOONSHOT_API_KEY"] == "sk-moon-xyz"
    assert env["OPENAI_API_KEY"] == "sk-oai"                 # `export ` prefix handled
    assert env["EMPTY"] == ""
    assert env["QUOTED"] == "v # not-a-comment"


def test_parse_dotenv_strips_utf8_bom_on_first_key():
    # Windows editors (Notepad's "Save as UTF-8") prepend a BOM. Without
    # stripping it the first key parses as '\ufeffMOONSHOT_API_KEY' and the
    # provider is never detected — the silent "my .env keeps failing" report.
    text = "\ufeff" + "MOONSHOT_API_KEY=sk-moon-xyz   # kimi\n"  # leading UTF-8 BOM
    env = e._parse_dotenv(text)
    assert "MOONSHOT_API_KEY" in env              # plain key, no BOM glued on
    assert "\ufeffMOONSHOT_API_KEY" not in env
    assert env["MOONSHOT_API_KEY"] == "sk-moon-xyz"


def test_bom_prefixed_key_is_still_detected(clean_env, monkeypatch):
    # End-to-end: a BOM'd .env must still resolve to a provider, not "✗ None".
    for k, v in e._parse_dotenv("\ufeff" + "MOONSHOT_API_KEY=sk-moon-xyz\n").items():
        monkeypatch.setenv(k, v)
    assert e.detect_provider() == "kimi"
    assert e.provider_diagnostic().startswith("✓ Detected provider: kimi")


# ── provider_diagnostic ──────────────────────────────────────────────────────

def test_diagnostic_none_when_nothing_set(clean_env):
    assert e.provider_diagnostic().startswith("✗ No provider detected")


def test_diagnostic_kimi_key_in_moonshot(clean_env, monkeypatch):
    monkeypatch.setenv("MOONSHOT_API_KEY", "sk-moon")
    out = e.provider_diagnostic()
    assert out.startswith("✓ Detected provider: kimi") and "MOONSHOT_API_KEY" in out


def test_diagnostic_local_runtime(clean_env, monkeypatch):
    monkeypatch.setenv("QSCREEN_PROVIDER", "ollama")
    assert e.provider_diagnostic() == "✓ Detected local runtime: ollama (no API key needed)."


def test_diagnostic_selected_but_no_key(clean_env, monkeypatch):
    monkeypatch.setenv("QSCREEN_PROVIDER", "openai")
    out = e.provider_diagnostic()
    assert out.startswith("⚠ Provider 'openai' is selected") and "OPENAI_API_KEY" in out


def test_diagnostic_surfaces_wrong_variable(clean_env, monkeypatch):
    # The actual bug report: a Kimi key left in MINIMAX_API_KEY is detected as
    # 'minimax' — so the diagnostic shows the mismatch instead of silent failure.
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-kimi-REDACTED")
    out = e.provider_diagnostic()
    assert "minimax" in out and "kimi" not in out


# ── set_dotenv_value: write a key into .env, applied live (Settings panel) ───

@pytest.fixture
def restore_environ():
    # set_dotenv_value writes os.environ directly (so a key takes effect without
    # a restart); snapshot + restore so those writes don't leak across tests.
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


def test_set_dotenv_value_creates_and_roundtrips(tmp_path, restore_environ):
    env = tmp_path / ".env"
    e.set_dotenv_value("MINIMAX_API_KEY", "sk-new-123", path=env)
    assert e._parse_dotenv(env.read_text())["MINIMAX_API_KEY"] == "sk-new-123"
    assert os.environ["MINIMAX_API_KEY"] == "sk-new-123"     # live, no restart needed
    assert (env.stat().st_mode & 0o777) == 0o600             # secrets → private file


def test_set_dotenv_value_updates_in_place_and_preserves_rest(tmp_path, restore_environ):
    env = tmp_path / ".env"
    env.write_text("# header\nOPENAI_API_KEY=          # openai note\nINGEST_TOKEN=keepme\n")
    e.set_dotenv_value("OPENAI_API_KEY", "sk-real", path=env)
    text = env.read_text()
    parsed = e._parse_dotenv(text)
    assert parsed["OPENAI_API_KEY"] == "sk-real"             # updated in place …
    assert parsed["INGEST_TOKEN"] == "keepme"                # … other keys untouched …
    assert "# header" in text                                # … comments preserved
    assert sum(ln.startswith("OPENAI_API_KEY=") for ln in text.splitlines()) == 1  # not duplicated


def test_set_dotenv_value_appends_when_absent(tmp_path, restore_environ):
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=sk-a\n")
    e.set_dotenv_value("MINIMAX_API_KEY", "sk-b", path=env)
    assert e._parse_dotenv(env.read_text()) == {"OPENAI_API_KEY": "sk-a", "MINIMAX_API_KEY": "sk-b"}


def test_set_dotenv_value_handles_bom_and_export(tmp_path, restore_environ):
    env = tmp_path / ".env"
    env.write_text("\ufeff" + "export OPENAI_API_KEY=old\n", encoding="utf-8")
    e.set_dotenv_value("OPENAI_API_KEY", "new", path=env)
    text = env.read_text()
    assert e._parse_dotenv(text)["OPENAI_API_KEY"] == "new"
    assert sum("OPENAI_API_KEY" in ln for ln in text.splitlines()) == 1


@pytest.mark.parametrize("key", ["bad key", "lower_case", "1LEADING", "WITH-DASH", ""])
def test_set_dotenv_value_rejects_bad_keys(tmp_path, key):
    with pytest.raises(ValueError):
        e.set_dotenv_value(key, "x", path=tmp_path / ".env")


def test_set_dotenv_value_rejects_newline_value(tmp_path):
    # A newline in the value could otherwise inject a second .env line.
    with pytest.raises(ValueError):
        e.set_dotenv_value("OPENAI_API_KEY", "sk\nINJECTED=evil", path=tmp_path / ".env")
