#!/usr/bin/env python3
"""qscreen_ui.py — the one place the tool's visual identity lives.

A dependency-free leaf module (imports nothing from the app, engine, or report
modules, so it can never create an import cycle). It exposes plain CSS strings
that every surface INLINES into its own ``<style>`` block — the web app's page
and each self-contained, single-file HTML report. Inlining (rather than a linked
stylesheet) keeps the generated reports portable: one file you can email or open
offline with no missing assets.

    import qscreen_ui
    html = f"<style>{qscreen_ui.css()}{my_page_specific_css}</style>"

Theming. Colours, spacing, fonts and radii are CSS custom properties on
``:root`` so a single token flips the whole tool. Dark mode follows the OS via
``prefers-color-scheme`` and can also be forced with a ``theme-dark`` /
``theme-light`` class on ``<html>`` (the class wins over the OS preference) —
that's how the web app's toggle works, with zero server involvement.
"""
from __future__ import annotations

# ── Design tokens ─────────────────────────────────────────────────────────────
# Light is the default (printed/shared reports read best on white). The dark
# values are reused verbatim for both the OS-preference media query and the
# manual .theme-dark override, so there is a single source of truth for each.

_LIGHT = """
  --fg:#1a1a1a; --fg-soft:#555; --muted:#888;
  --bg:#fff; --card:#f6f6f6; --border:#e0e0e0; --border-soft:#eee;
  --pos:#0a7; --neg:#c33; --warn:#c80; --warn-strong:#b06b00;
  --ok:#0b6; --ok-text:#0a7; --err:#c33;
  --accent:#06c; --accent-2:#36c; --accent-bg:#eef6ff; --accent-border:#cfe3ff;
  --fx-bg:#fde8c8; --fx-fg:#a05a00; --base-cell:#fff3cd;
  --font:-apple-system,system-ui,Segoe UI,Roboto,sans-serif;
  --radius:8px; --radius-sm:4px; --space:8px; --shadow:0 1px 3px rgba(0,0,0,.08);
"""

_DARK = """
  --fg:#e7e8ea; --fg-soft:#b6b9be; --muted:#9aa0a6;
  --bg:#14161a; --card:#1d2026; --border:#2d323b; --border-soft:#262a31;
  --pos:#1ec79a; --neg:#f06a6a; --warn:#e6b252; --warn-strong:#e6b252;
  --ok:#16a06b; --ok-text:#1ec79a; --err:#f06a6a;
  --accent:#5aa0ff; --accent-2:#7ab3ff; --accent-bg:#1b2738; --accent-border:#2a3a52;
  --fx-bg:#3a2c1a; --fx-fg:#e6bd80; --base-cell:#3a3320;
  --shadow:0 1px 3px rgba(0,0,0,.45);
"""

TOKENS = (
    f":root{{{_LIGHT}}}\n"
    # OS dark preference — unless the user explicitly forced light.
    f"@media (prefers-color-scheme:dark){{:root:not(.theme-light){{{_DARK}}}}}\n"
    # Manual override always wins over the OS preference.
    f":root.theme-dark{{{_DARK}}}\n"
    # Always print on white with dark ink — a dark-themed report must not come
    # out as light text on white paper. Listed last and matched at equal/greater
    # specificity so it beats both dark rules above during printing.
    f"@media print{{:root,:root.theme-dark,:root:not(.theme-light){{{_LIGHT}}}}}\n"
)

# ── Shared component styles ───────────────────────────────────────────────────
# Class names here are part of the contract — the page JS and the report
# renderers (and a couple of tests) reference .pos/.neg/.muted/.tag/.fx/.rep/etc.
# Tokenise their *values*; never rename the selectors. Page-specific rules live
# in each consumer's own CSS, which is appended AFTER this block so it can refine
# (but rarely needs to override) anything below.

COMPONENTS = """
*{box-sizing:border-box}
body{font:15px/1.5 var(--font);color:var(--fg);background:var(--bg)}
a{color:var(--accent)}
h1,h2,h3{color:var(--fg)}
table{width:100%;border-collapse:collapse}
th,td{border-bottom:1px solid var(--border-soft);padding:5px 8px;text-align:right}
th:first-child,td:first-child{text-align:left}
th{color:var(--muted);font-weight:600}
.muted{color:var(--muted)}
.pos{color:var(--pos)} .neg{color:var(--neg)}
.ok{color:var(--ok-text)} .warn{color:var(--warn)} .err{color:var(--err)}
.rep{color:var(--pos);font-size:11px;cursor:help}
.ev{color:var(--accent);cursor:help}
.tag{display:inline-block;background:var(--accent-bg);border:1px solid var(--accent-border);border-radius:6px;padding:1px 7px;font-size:11px;margin:0 4px 4px 0}
.cat{display:inline-block;background:var(--accent-bg);border:1px solid var(--accent-border);border-radius:6px;padding:0 6px;font-size:11px;color:var(--accent-2)}
.fx{background:var(--fx-bg);color:var(--fx-fg);border-radius:var(--radius-sm);padding:0 5px;font-size:11px;font-weight:700}
ul.flags{list-style:none;padding:0;margin:6px 0}
ul.flags li{padding:3px 0}
li.alert{color:var(--neg);font-weight:600} li.warn,li.warn2{color:var(--warn-strong)}
button{font:inherit;cursor:pointer}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:2px}
@media (prefers-reduced-motion:reduce){*{animation-duration:.001ms!important;animation-iteration-count:1!important;transition-duration:.001ms!important;scroll-behavior:auto!important}}
"""


def css() -> str:
    """The full shared stylesheet — design tokens plus shared components.

    Prepend this to a page's own CSS inside a single ``<style>`` element.
    """
    return TOKENS + COMPONENTS
