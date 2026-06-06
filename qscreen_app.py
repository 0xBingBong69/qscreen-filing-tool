#!/usr/bin/env python3
"""
qscreen_app.py — local browser app for the QSE filing ingestor.

Run it on your laptop, open the page, drag in a PDF, fill four fields, click
Extract. It runs the SAME engine as qscreen_ingest.py (imported, not
re-implemented) and gives you a downloadable JSON report to upload to
qscreen.app. Nothing is auto-uploaded — you stay in control.

    pip install flask pdfplumber requests
    python3 qscreen_app.py
    # then open http://127.0.0.1:8765 in your browser

The OpenRouter key is read from the tool's .env (same as the CLI) or the
OPENROUTER_API_KEY env var. No agent, no command line per filing. Upload is
opt-in: a button appears only when the server has INGEST_TOKEN set, and even
then nothing leaves your machine until you click it.
"""
from __future__ import annotations

import io
import json
import os
import queue
import re
import sys
import tempfile
import threading
import traceback
from types import SimpleNamespace
from pathlib import Path

# Reuse the exact, tested engine — do NOT reimplement any of it here.
import qscreen_ingest as engine
import qscreen_analyze
import qscreen_dcf
import qscreen_report
import qscreen_portfolio
import qscreen_workbook
import qscreen_statements
import qscreen_periods
import qscreen_ui

try:
    from flask import Flask, request, Response, send_file, stream_with_context
except ImportError:
    sys.exit("Flask not installed. Run:  pip install flask pdfplumber requests")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024  # 64 MB upload cap


def _safe_filename(s, fallback: str = "filing") -> str:
    """A download filename safe to drop into a Content-Disposition header — no
    quotes, path separators, or control chars (which a filing's symbol could carry)."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", str(s or "")).strip("._")
    return cleaned[:64] or fallback

# ── QSE taxonomy + per-stock knowledge ───────────────────────────────────────
# The sector → sub-sector tree and the symbol map now live in the qatar/ package
# (the single source of truth, with per-stock temporal profiles). Each sub-sector
# still maps to one of the engine's 5 EXTRACTION archetypes, which drive the LLM's
# parsing hint (conventional_bank / islamic_bank / insurance / industrial / other).
import qatar

QSE_TAXONOMY = qatar.QSE_TAXONOMY
SUBSECTOR_TO_EXTRACTION = qatar.SUBSECTOR_TO_EXTRACTION
SYMBOL_SUBSECTOR = qatar.SYMBOL_SUBSECTOR


def _subsector_options_html() -> str:
    out = []
    for group, subs in QSE_TAXONOMY.items():
        out.append(f'<optgroup label="{group}">')
        for sub, _cat in subs:
            out.append(f'<option value="{sub}">{sub}</option>')
        out.append("</optgroup>")
    return "\n".join(out)

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>QScreen Filing Ingestor</title>
<style>
  __UI_CSS__
  body { max-width: 760px; margin: 0 auto; padding: 24px 16px 64px; }
  .topbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
  h1 { font-size: 22px; margin: 0; } .sub { color: var(--fg-soft); margin: 4px 0 18px; }
  #themetoggle { background: var(--card); color: var(--fg); border: 1px solid var(--border); border-radius: 999px; padding: 7px 13px; font-size: 13px; font-weight: 600; white-space: nowrap; }
  label { display: block; margin: 14px 0 4px; font-weight: 600; }
  input, select { width: 100%; padding: 9px; border: 1px solid var(--border); border-radius: var(--radius-sm); font-size: 15px; background: var(--bg); color: var(--fg); }
  .row { display: flex; gap: 12px; flex-wrap: wrap; } .row > div { flex: 1; min-width: 150px; }
  .drop { position: relative; display: flex; flex-direction: column; align-items: center; gap: 4px; text-align: center; padding: 26px 16px; border: 2px dashed var(--border); border-radius: var(--radius); background: var(--card); color: var(--fg-soft); cursor: pointer; transition: border-color .15s, background .15s; }
  .drop:hover, .drop:focus-visible { border-color: var(--accent); }
  .drop.over { border-color: var(--accent); background: var(--accent-bg); color: var(--fg); }
  .drop strong { color: var(--fg); } .drop .big { font-size: 26px; line-height: 1; }
  .drop input[type=file] { position: absolute; width: 1px; height: 1px; opacity: 0; }
  button.go { margin-top: 20px; padding: 12px 20px; font-size: 16px; font-weight: 600; background: var(--ok); color: #fff; border: 0; border-radius: var(--radius); width: 100%; }
  button.go:disabled { background: var(--muted); cursor: wait; }
  #prog { width: 100%; height: 10px; margin-top: 14px; display: none; }
  #progmsg { color: var(--fg-soft); font-size: 13px; margin: 6px 0 0; min-height: 16px; }
  #out { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px; margin-top: 18px; display: none; }
  .banner { display: flex; gap: 9px; align-items: flex-start; font-weight: 600; padding: 11px 13px; border: 1px solid var(--border); border-left-width: 4px; border-radius: var(--radius-sm); }
  .banner.ok { border-left-color: var(--pos); } .banner.warn { border-left-color: var(--warn); } .banner.err { border-left-color: var(--err); }
  .banner .ic { font-size: 17px; line-height: 1.25; }
  .notes { margin: 8px 0 0; padding-left: 18px; color: var(--fg-soft); font-size: 13px; font-weight: 400; }
  .hint { color: var(--muted); font-size: 13px; margin: 6px 0 0; min-height: 16px; }
  a.dl { display: inline-block; margin: 8px 8px 0 0; padding: 10px 16px; background: var(--accent); color: #fff; border-radius: var(--radius); text-decoration: none; font-weight: 600; }
  a.up { background: var(--ok); } a.up.busy { background: var(--muted); pointer-events: none; }
  details.adv { margin-top: 14px; } summary { cursor: pointer; color: var(--accent); font-weight: 600; }
  .keyhint { background: var(--accent-bg); border: 1px solid var(--accent-border); border-radius: var(--radius); padding: 10px 12px; margin-top: 10px; font-size: 13px; line-height: 1.5; }
  .keyhint a { color: var(--accent); font-weight: 700; } .keyhint code { background: var(--accent-border); padding: 1px 5px; border-radius: 4px; }
  label.guided { display: flex; align-items: center; gap: 8px; margin-top: 10px; font-size: 13px; font-weight: 600; }
  label.guided input { width: auto; }
  .seg { margin-top: 18px; } .seg h3 { font-size: 16px; margin: 8px 0; } .seg h4 { font-size: 13px; color: var(--fg-soft); text-transform: capitalize; margin: 12px 0 4px; }
  table.seg, table.cmp { font-size: 13px; } table.cmp { margin-top: 8px; }
  table.cmp tr.target { background: var(--accent-bg); font-weight: 600; } table.cmp .r1 { color: var(--pos); font-weight: 700; }
  table.cmp sup { color: var(--muted); font-weight: 400; }
  .dcf label { display: inline-block; font-weight: 600; font-size: 12px; margin: 6px 8px 2px 0; }
  .dcf input { width: 78px; padding: 5px; font-size: 13px; }
  details.cmp button, .dcf button { background: var(--accent); color: #fff; border: 0; border-radius: var(--radius-sm); padding: 8px 14px; font-size: 14px; font-weight: 600; margin: 8px 6px 0 0; }
  .dcfval { font-size: 18px; font-weight: 700; } .grid td.base { background: var(--base-cell); font-weight: 700; }
  details.cmp { margin-top: 22px; border-top: 1px solid var(--border-soft); padding-top: 12px; } details.cmp summary { cursor: pointer; font-weight: 600; }
  label.inc { font-size: 12px; color: var(--fg-soft); margin-left: 10px; font-weight: 600; } label.inc input { vertical-align: middle; width: auto; }
  .outputs { margin: 14px 0 4px; padding-top: 10px; border-top: 1px solid var(--border-soft); }
  .olabel { display: block; font-weight: 600; color: var(--fg-soft); font-size: 13px; margin-bottom: 6px; }
</style>
<script>(function(){try{var t=localStorage.getItem('qscreen-theme');var c=document.documentElement.classList;
  if(t==='dark')c.add('theme-dark');else if(t==='light')c.add('theme-light');}catch(e){}})();</script>
</head><body>
<div class="topbar"><h1>QScreen Filing Ingestor</h1>
  <button type="button" id="themetoggle" aria-label="Toggle dark mode" aria-pressed="false">🌙 Dark</button></div>
<p class="sub">Drop a QSE financial-report PDF, fill the fields, click Extract. Then download the report and upload it to qscreen.app. Type a known symbol and the sub-sector auto-fills.</p>
<form id="f">
  <label for="pdf">Filing PDF</label>
  <div class="drop" id="drop" tabindex="0" role="button" aria-label="Choose or drop a PDF file">
    <span class="big" aria-hidden="true">📄</span>
    <span id="dropmsg"><strong>Drop a PDF here</strong> or click to browse</span>
    <input type="file" id="pdf" name="pdf" accept="application/pdf" required>
  </div>
  <div class="row">
    <div><label for="symbol">Symbol</label><input name="symbol" id="symbol" placeholder="QIBK" autocomplete="off" required></div>
    <div><label for="subsector">QSE Sector / Sub-sector</label>
      <select name="subsector" id="subsector" required>
        __SUBSECTOR_OPTIONS__
      </select>
    </div>
  </div>
  <p class="hint" id="hint" aria-live="polite"></p>
  <div class="row">
    <div><label for="year">Year</label><input name="year" id="year" type="number" placeholder="2024" required></div>
    <div><label for="period">Period</label>
      <select name="period" id="period">
        <option>FY</option><option>Q1</option><option>Q2</option><option>Q3</option>
        <option>Q4</option><option>H1</option><option>9M</option>
      </select>
    </div>
  </div>
  <details class="adv" open><summary>Provider / model — cloud key OR a local model on your laptop</summary>
    <div class="row">
      <div><label for="provider">AI Provider</label>
        <select name="provider" id="provider">
          <option value="">auto (use whichever key is set)</option>
          <optgroup label="Cloud (needs an API key)">
            <option value="minimax">MiniMax</option>
            <option value="openrouter">OpenRouter</option>
            <option value="kimi">Kimi (Moonshot)</option>
            <option value="openai">OpenAI</option>
            <option value="anthropic">Claude (Anthropic)</option>
          </optgroup>
          <optgroup label="Local — on your laptop, no API key">
            <option value="ollama">Ollama (local)</option>
            <option value="lmstudio">LM Studio (local)</option>
            <option value="llamacpp">llama.cpp (local)</option>
            <option value="jan">Jan (local)</option>
            <option value="gpt4all">GPT4All (local)</option>
            <option value="mlx">MLX — Apple (local)</option>
          </optgroup>
        </select>
      </div>
      <div><label for="model">Model <span class="muted">(blank = provider default)</span></label>
        <input name="model" id="model" placeholder="default" autocomplete="off"></div>
    </div>
    <p class="keyhint" id="provkey"></p>
    <div class="row">
      <div><label for="mode">Mode</label>
        <select name="mode" id="mode">
          <option value="auto">Auto (Basic for local, Pro for cloud)</option>
          <option value="basic">Basic — deterministic, great for tiny / local models</option>
          <option value="pro">Pro — model extracts everything (use a strong model)</option>
        </select>
      </div>
    </div>
    <label class="guided"><input type="checkbox" name="no_llm" id="no_llm" value="1">
      Run fully offline — read numbers from the PDF tables with <b>no model at all</b>
      <span class="muted">(Basic; needs no key)</span></label>
    <p class="keyhint" id="modehint"></p>
  </details>
  <button type="submit" id="go" class="go">Extract</button>
  <progress id="prog" max="100" value="0" aria-label="Extraction progress"></progress>
  <p id="progmsg" aria-live="polite"></p>
</form>
<section id="out" role="status" aria-live="polite"></section>

<details class="cmp"><summary>Compare / screen extracted filings</summary>
  <p class="muted">Reuse the filings you extracted this session and/or add saved
  <code>*_filing.json</code> files.
  <b>Compare</b> ranks them as peers (on the first file's company type);
  <b>Dashboard</b> screens the whole basket; <b>Excel workbook</b> combines several
  years of one company into a single multi-year transcript; <b>TTM</b> rolls interim
  (YTD) filings into a trailing-twelve-month view.</p>
  <label class="guided"><input type="checkbox" id="usesession" checked>
    <span id="sesslabel">No filings extracted yet this session</span></label>
  <label for="cmpfiles">…or add saved filing JSON files</label>
  <input type="file" id="cmpfiles" accept="application/json,.json" multiple>
  <button id="cmpgo" type="button">Compare</button>
  <button id="dashgo" type="button">Dashboard</button>
  <button id="wbgo" type="button">Excel workbook</button>
  <button id="ttmgo" type="button">TTM</button>
  <div id="cmpout"></div>
</details>
<script>
const SYMBOL_SUBSECTOR = __SYMBOL_MAP_JSON__;
const UPLOAD_ENABLED = __UPLOAD_ENABLED__;
const PROVIDER_INFO = __PROVIDER_INFO_JSON__;
const f = document.getElementById('f'), out = document.getElementById('out'), go = document.getElementById('go');
const provEl = document.getElementById('provider'), modelEl = document.getElementById('model'),
      provKey = document.getElementById('provkey'), modeEl = document.getElementById('mode'),
      noLlmEl = document.getElementById('no_llm'), modeHint = document.getElementById('modehint');
function updateProvider() {
  const info = PROVIDER_INFO[provEl.value];
  if (info && info.local) {
    modelEl.placeholder = info.model || 'default';
    provKey.innerHTML = '💻 <b>' + info.label + '</b> runs on your laptop — <b>no API key needed</b>. ' +
      (info.setup ? '<code>' + esc(info.setup) + '</code>. ' : '') +
      '<a href="' + info.url + '" target="_blank" rel="noopener">Download / docs &#8599;</a>. ' +
      'Make sure it is running, then click Extract.';
    if (modeEl && modeEl.value === 'auto') modeEl.value = 'basic';   // tiny models → Basic
  } else if (info) {
    modelEl.placeholder = info.model || 'default';
    provKey.innerHTML = '🔑 Need a key for <b>' + info.label + '</b>? ' +
      '<a href="' + info.url + '" target="_blank" rel="noopener">Click here to get one &#8599;</a>' +
      ', then add <code>' + info.env + '=your-key</code> to the <code>.env</code> file next to the app and restart it.';
  } else {
    modelEl.placeholder = 'default';
    provKey.innerHTML = '🔑 Use a cloud key (one <code>*_API_KEY</code> in <code>.env</code>) ' +
      'or pick a <b>local</b> model above to run fully offline with no key.';
  }
  updateMode();
}
function updateMode() {
  if (!modeHint) return;
  if (noLlmEl && noLlmEl.checked) {
    modeHint.innerHTML = '⚙️ <b>Fully offline.</b> Line items are read straight from the PDF tables — ' +
      'no model is called. Audit/notes are skipped. Works with no key and no model running.';
    return;
  }
  const m = modeEl ? modeEl.value : 'auto';
  if (m === 'pro') {
    modeHint.innerHTML = '🧠 <b>Pro.</b> The model extracts everything (richer notes & segments). ' +
      'Use a strong model — GPT‑4.5+/Claude Sonnet 4+/MiniMax‑M2.';
  } else if (m === 'basic') {
    modeHint.innerHTML = '🧭 <b>Basic.</b> Numbers are read from the PDF tables in code; the model only ' +
      'fills gaps and classifies the audit opinion. Great for a tiny / local model (e.g. Gemma 3 270M via MLX).';
  } else {
    modeHint.innerHTML = '🧭 <b>Auto.</b> Basic for local models, Pro for cloud models.';
  }
}
if (provEl) { provEl.addEventListener('change', updateProvider); }
if (modeEl) { modeEl.addEventListener('change', updateMode); }
if (noLlmEl) { noLlmEl.addEventListener('change', updateMode); }
updateProvider();

// ── dark / light theme toggle (persisted; applied pre-paint in <head>) ────────
const themeBtn = document.getElementById('themetoggle');
function currentTheme(){ const c = document.documentElement.classList;
  return c.contains('theme-dark') ? 'dark' : (c.contains('theme-light') ? 'light'
    : ((window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light')); }
function syncThemeBtn(){ const dark = currentTheme() === 'dark';
  themeBtn.textContent = dark ? '☀️ Light' : '🌙 Dark';
  themeBtn.setAttribute('aria-pressed', dark ? 'true' : 'false'); }
themeBtn.onclick = () => {
  const dark = currentTheme() === 'dark', c = document.documentElement.classList;
  c.remove('theme-dark', 'theme-light'); c.add(dark ? 'theme-light' : 'theme-dark');
  try { localStorage.setItem('qscreen-theme', dark ? 'light' : 'dark'); } catch (e) {}
  syncThemeBtn();
};
syncThemeBtn();

// ── drag & drop PDF (the bare file input is visually hidden inside .drop) ──────
const drop = document.getElementById('drop'), pdfIn = document.getElementById('pdf'),
      dropMsg = document.getElementById('dropmsg');
function showFile(){ const file = pdfIn.files && pdfIn.files[0];
  dropMsg.innerHTML = file ? ('<strong>' + esc(file.name) + '</strong> · ' + (file.size / 1048576).toFixed(1) + ' MB')
                           : '<strong>Drop a PDF here</strong> or click to browse'; }
drop.onclick = () => pdfIn.click();
drop.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pdfIn.click(); } };
['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('over'); }));
drop.addEventListener('dragleave', e => { if (!drop.contains(e.relatedTarget)) drop.classList.remove('over'); });
drop.addEventListener('drop', e => { e.preventDefault(); drop.classList.remove('over');
  if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) { pdfIn.files = e.dataTransfer.files; showFile(); } });
pdfIn.addEventListener('change', showFile);

// ── in-session memory: reuse just-extracted filings in Compare/Dashboard/TTM ──
let sessionFilings = [];
function rememberFiling(name, filing, analysis){
  sessionFilings.push({ name: name, filing: filing, analysis: analysis || null });
  updateSessionLabel();
}
function updateSessionLabel(){ const el = document.getElementById('sesslabel');
  if (el) el.textContent = sessionFilings.length
    ? ("Include this session's " + sessionFilings.length + " extracted filing(s)")
    : "No filings extracted yet this session"; }
function gatherFilings(picked){
  const useSess = document.getElementById('usesession');
  const sess = (useSess && useSess.checked) ? sessionFilings.map(s => s.filing) : [];
  return sess.concat(picked || []);
}
updateSessionLabel();

function fmtNum(x){ return (x==null)?'—':Number(x).toLocaleString(); }
function esc(s){ return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function fmtPct(x){ if(x==null) return '<span>—</span>'; const c=x<0?'neg':'pos'; return '<span class="'+c+'">'+(x*100).toFixed(0)+'%</span>'; }
function renderSegments(sa){
  if(!sa || !sa.dimensions || !Object.keys(sa.dimensions).length) return '';
  let h = '<div class="seg"><h3>Segment breakdown ('+esc(sa.reporting_currency||'')+')</h3>';
  for(const dim of Object.keys(sa.dimensions)){
    const d = sa.dimensions[dim];
    h += '<h4>by '+esc(dim.replace('_',' '))+'</h4><table class="seg"><tr><th>Segment</th>'
       + '<th>Revenue</th><th>YoY</th><th>Share</th><th>Net profit</th><th>YoY</th></tr>';
    for(const r of d.segments){
      const m=r.metrics||{}, y=r.yoy||{}, s=r.share||{};
      const fx = r.fx_exposed ? ' <span class="fx" title="'+esc(r.fx_note||'')+'">FX '+esc(r.currency||'')+'</span>' : '';
      const ev = (r.events&&r.events.length) ? ' <span class="ev" title="'+esc(r.events.join(' · '))+'">ⓘ</span>' : '';
      h += '<tr><td>'+esc(r.name)+fx+ev+'</td><td>'+fmtNum(m.revenue)+'</td><td>'+fmtPct(y.revenue)
         + '</td><td>'+fmtPct(s.revenue)+'</td><td>'+fmtNum(m.net_profit)+'</td><td>'+fmtPct(y.net_profit)+'</td></tr>';
    }
    h += '</table>';
  }
  return h + '</div>';
}
function fmtCmp(name, v){
  if(v==null) return '—';
  if(name==='liabilities_to_equity') return Number(v).toFixed(2)+'×';
  const pctSet = ['roe','roa','nim','cost_income','npl','car','ldr','net_margin','operating_margin','loss_ratio','combined_ratio'];
  if(pctSet.indexOf(name)>=0) return (v*100).toFixed(1)+'%';   // values are fractions
  return Number(v).toLocaleString();
}
function renderCompare(d){
  if(!d || !d.rows || !d.rows.length) return '<span class="warn">'+esc((d&&d.error)||'nothing to compare')+'</span>';
  const metrics = d.metrics.map(m=>m.name);
  let h = '<table class="cmp"><tr><th>Company</th>';
  for(const m of metrics) h += '<th>'+esc(m.replace(/_/g,' '))+'</th>';
  h += '</tr>';
  for(const r of d.rows){
    h += '<tr class="'+(r.is_target?'target':'')+'"><td title="'+esc(r.symbol)+'">'+esc(r.symbol)+(r.is_target?' ★':'')+'</td>';
    for(const m of metrics){ const rk=r.ranks[m];
      h += '<td class="'+(rk===1?'r1':'')+'">'+fmtCmp(m, r.ratios[m])+(rk?'<sup>#'+rk+'</sup>':'')+'</td>'; }
    h += '</tr>';
  }
  return h + '</table><p class="muted">★ = target · #n = rank among peers · green = best</p>';
}
async function pickedFilings(){
  const inp = document.getElementById('cmpfiles');
  return Promise.all([...(inp.files || [])].map(f => f.text().then(t => JSON.parse(t))));
}
async function runCompare(){
  const out = document.getElementById('cmpout');
  out.textContent = 'Comparing…';
  try {
    const filings = gatherFilings(await pickedFilings());
    if(filings.length < 2){ out.innerHTML='<span class="warn">Need at least two filings — extract some this session, or add JSON files.</span>'; return; }
    const r = await fetch('/compare', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({filings})});
    out.innerHTML = renderCompare(await r.json());
  } catch(e){ out.innerHTML = '<span class="err">'+esc(e)+'</span>'; }
}
async function runDashboard(){
  const out = document.getElementById('cmpout');
  out.textContent = 'Screening…';
  try {
    const filings = gatherFilings(await pickedFilings());
    if(!filings.length){ out.innerHTML='<span class="warn">No filings — extract some this session, or add JSON files.</span>'; return; }
    const r = await fetch('/portfolio', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({filings})});
    const d = await r.json(); if(!r.ok) throw new Error(d.error||'failed');
    const url = URL.createObjectURL(new Blob([d.html], {type:'text/html'}));
    const a = document.createElement('a'); a.href = url; a.download = 'watchlist.html'; a.click(); URL.revokeObjectURL(url);
    out.innerHTML = '<span class="muted">Downloaded watchlist.html — screened '+d.count+' stock(s).</span>';
  } catch(e){ out.innerHTML = '<span class="err">'+esc(e)+'</span>'; }
}
async function runTtm(){
  const out = document.getElementById('cmpout');
  out.textContent = 'Rolling up…';
  try {
    const filings = gatherFilings(await pickedFilings());
    if(!filings.length){ out.innerHTML='<span class="warn">No filings (one company, annual and/or interim) — extract some, or add JSON files.</span>'; return; }
    const r = await fetch('/ttm', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({filings})});
    const d = await r.json(); if(!r.ok) throw new Error(d.error||'failed');
    function tbl(obj){ const rows = Object.entries(obj||{}).sort(); if(!rows.length) return '<p class="muted">—</p>';
      return '<table><tr><th>Flow metric</th><th>Value</th></tr>' + rows.map(([c,v]) => '<tr><td>'+esc(c)+'</td><td>'+fmtNum(v)+'</td></tr>').join('') + '</table>'; }
    let h = '<div class="seg"><h3>TTM — as of '+esc(d.as_of||'?')+'</h3><p class="muted">'+esc(d.basis||'')+'</p>'+tbl(d.flows);
    if(d.standalone_quarter) h += '<h3>'+esc(d.standalone_quarter.label)+'</h3>'+tbl(d.standalone_quarter.flows);
    if((d.warnings||[]).length) h += '<p class="warn">'+d.warnings.map(esc).join('<br>')+'</p>';
    h += '<p class="muted">Periods: '+(d.periods||[]).map(esc).join(', ')+'</p></div>';
    out.innerHTML = h;
  } catch(e){ out.innerHTML = '<span class="err">'+esc(e)+'</span>'; }
}
async function runWorkbook(){
  const out = document.getElementById('cmpout');
  out.textContent = 'Building workbook…';
  try {
    const filings = gatherFilings(await pickedFilings());
    if(!filings.length){ out.innerHTML='<span class="warn">No filings (same company, multiple years) — extract some, or add JSON files.</span>'; return; }
    const r = await fetch('/workbook', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({filings})});
    if(!r.ok){ const d = await r.json().catch(()=>({})); throw new Error(d.error||'failed'); }
    const url = URL.createObjectURL(await r.blob());
    const a = document.createElement('a'); a.href = url; a.download = 'transcript.xlsx'; a.click(); URL.revokeObjectURL(url);
    out.innerHTML = '<span class="muted">Downloaded transcript.xlsx — '+filings.length+' filing(s).</span>';
  } catch(e){ out.innerHTML = '<span class="err">'+esc(e)+'</span>'; }
}
function renderDcfPanel(){
  return '<div class="seg dcf"><h3>Valuation (DCF) — adjustable</h3>'
    + '<div><label>Discount rate %</label><input id="d_r" type="number" step="0.5" value="10">'
    + '<label>Growth %</label><input id="d_g" type="number" step="0.5" placeholder="auto">'
    + '<label>Terminal %</label><input id="d_tg" type="number" step="0.25" value="2.5">'
    + '<label>Years</label><input id="d_yr" type="number" value="5">'
    + '<label>Shares</label><input id="d_sh" type="number" placeholder="optional">'
    + '<label>Price</label><input id="d_px" type="number" step="0.01" placeholder="optional"></div>'
    + '<button id="dcfgo">Run valuation</button><div id="dcfout"></div></div>';
}
function runDcf(){
  const num = (id)=>{ const v=document.getElementById(id).value; return v===''?null:Number(v); };
  const a = { discount_rate:(num('d_r')||10)/100, terminal_growth:(num('d_tg')||2.5)/100, years:num('d_yr')||5 };
  const g = num('d_g'); if(g!=null) a.growth = g/100;
  const body = { filing:lastFiling, symbol:lastSymbol, assumptions:a, price:num('d_px'), shares:num('d_sh') };
  const out = document.getElementById('dcfout'); out.textContent='Computing…';
  fetch('/dcf',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
    .then(r=>r.json()).then(d=>{ out.innerHTML = renderDcfResult(d); })
    .catch(e=>{ out.innerHTML='<span class="err">'+esc(e)+'</span>'; });
}
function renderDcfResult(d){
  if(!d || !d.valuation){ return '<span class="warn">'+esc((d&&d.warnings&&d.warnings.join('; '))||'no valuation')+'</span>'; }
  const v=d.valuation, ccy=esc(d.reporting_currency||'');
  const headline = (v.per_share!=null) ? (ccy+' '+v.per_share.toFixed(2)+' / share')
                                       : (ccy+' '+fmtNum(Math.round(v.equity_value))+' equity value');
  let h = '<p class="dcfval">'+headline+'</p>';
  h += '<p class="muted">model: '+esc(v.model)+' · terminal '+(v.terminal_pct*100).toFixed(0)+'% of value'
     + ((d.upside!=null) ? ' · upside <span class="'+(d.upside<0?'neg':'pos')+'">'+(d.upside*100).toFixed(0)+'%</span> vs '+d.price : '')+'</p>';
  const s=d.sensitivity;
  if(s){
    const bg=v.assumptions.growth, br=v.assumptions.discount_rate;
    h += '<h4>Sensitivity ('+((v.per_share!=null)?'per share':'equity')+') — growth → / discount ↓</h4><table class="seg grid"><tr><th></th>';
    for(const g of s.growth_values) h+='<th>'+(g*100).toFixed(1)+'%</th>';
    h+='</tr>';
    for(let i=0;i<s.rate_values.length;i++){ h+='<tr><th>'+(s.rate_values[i]*100).toFixed(1)+'%</th>';
      for(let j=0;j<s.growth_values.length;j++){ const cell=s.grid[i][j];
        const base = Math.abs(s.rate_values[i]-br)<1e-9 && Math.abs(s.growth_values[j]-bg)<1e-9;
        h+='<td class="'+(base?'base':'')+'">'+((cell==null)?'—':((v.per_share!=null)?Number(cell).toFixed(2):fmtNum(Math.round(cell))))+'</td>'; }
      h+='</tr>'; }
    h+='</table>';
  }
  return h;
}
const PCT_RATIOS = ['roe','roa','nim','cost_income','npl','car','coverage','ldr','net_margin','operating_margin','loss_ratio','expense_ratio','combined_ratio','dividend_payout'];
function fmtRatio(name, r){
  if(!r || r.value==null) return '—';
  const v=r.value, rep=(r.basis==='reported')?' <span class="rep" title="as reported by the company">®</span>':'';
  if(name==='fcf') return fmtNum(v)+rep;
  if(name==='liabilities_to_equity') return v.toFixed(2)+'×'+rep;
  return (v*100).toFixed(1)+'%'+rep;   // all ratio values are fractions
}
function renderAnalysis(an){
  if(!an || !an.ratios) return '';
  const yrs = Object.keys(an.ratios); if(!yrs.length) return '';
  const y = yrs[yrs.length-1], R = an.ratios[y];
  let h='<div class="seg"><h3>Key ratios — '+y+' ('+(an.archetype||'').replace(/_/g,' ')+') <span class="rep">® = as reported</span></h3>';
  h+='<table class="seg"><tr><th>Ratio</th><th>Value</th></tr>';
  for(const k of Object.keys(R)) h+='<tr><td>'+esc(k.replace(/_/g,' '))+'</td><td>'+fmtRatio(k,R[k])+'</td></tr>';
  h+='</table>';
  if(an.red_flags && an.red_flags.length){
    h+='<h4>Red flags</h4><ul class="flags">';
    for(const f of an.red_flags){ const cls=(f.severity==='alert')?'alert':'warn2';
      h+='<li class="'+cls+'">'+((f.severity==='alert')?'🚨':'⚠️')+' '+esc(f.message)+'</li>'; }
    h+='</ul>';
  }
  return h+'</div>';
}
const symbolEl = document.getElementById('symbol'), subEl = document.getElementById('subsector'), hintEl = document.getElementById('hint');
symbolEl.addEventListener('input', () => {
  const sym = symbolEl.value.trim().toUpperCase().replace(/\\.QA$/, '');
  const sub = SYMBOL_SUBSECTOR[sym];
  if (sub) {
    subEl.value = sub;
    hintEl.textContent = sym + ' → ' + sub + ' (auto-filled; change if wrong)';
  } else {
    hintEl.textContent = sym ? (sym + ' not in the known list — pick the sub-sector manually') : '';
  }
});
let lastBlob = null, lastName = 'filing.json', lastFiling = null, lastSymbol = '', lastAnalysis = null;

const STAGE_LABEL = { reading_pdf: 'Reading the PDF', extracting: 'Extracting statements',
                      assembling: 'Assembling the filing', validating: 'Checking the contract' };
function setProgress(pct, msg){
  const p = document.getElementById('prog'), m = document.getElementById('progmsg');
  if (p) { p.style.display = 'block'; if (pct == null) p.removeAttribute('value'); else p.value = pct; }
  if (m) m.textContent = msg || '';
}
function clearProgress(){ const p = document.getElementById('prog'), m = document.getElementById('progmsg');
  if (p) p.style.display = 'none'; if (m) m.textContent = ''; }
function onProgress(ev){
  if (ev.stage === 'extracting' && ev.total)
    setProgress(Math.min(92, Math.round(ev.window / ev.total * 92)),
      'Extracting — window ' + ev.window + ' of ' + ev.total + (ev.message ? (' · ' + ev.message) : ''));
  else if (ev.stage === 'reading_pdf') setProgress(4, STAGE_LABEL.reading_pdf + (ev.message ? (' · ' + ev.message) : ''));
  else if (ev.stage === 'assembling') setProgress(95, STAGE_LABEL.assembling);
  else if (ev.stage === 'validating') setProgress(98, STAGE_LABEL.validating);
  else if (STAGE_LABEL[ev.stage]) setProgress(null, STAGE_LABEL[ev.stage]);
}

// Stream the extraction as Server-Sent Events, advancing the progress bar from
// each event. Falls back to the blocking /extract JSON route if the browser
// can't read the streaming body. Throws {data:{error}} on failure.
async function streamExtract(fd){
  const res = await fetch('/extract/stream', { method: 'POST', body: fd });
  if (!res.ok) { const d = await res.json().catch(() => ({})); throw { data: d }; }
  const ctype = res.headers.get('content-type') || '';
  if (!res.body || ctype.indexOf('text/event-stream') < 0) {
    const r2 = await fetch('/extract', { method: 'POST', body: fd });
    const d2 = await r2.json(); if (!r2.ok) throw { data: d2 }; return d2;
  }
  const reader = res.body.getReader(), dec = new TextDecoder();
  let buf = '', result = null, errMsg = null;
  for (;;) {
    const chunk = await reader.read(); if (chunk.done) break;
    buf += dec.decode(chunk.value, { stream: true });
    let i;
    while ((i = buf.indexOf('\\n\\n')) >= 0) {
      const line = buf.slice(0, i).trim(); buf = buf.slice(i + 2);
      if (line.indexOf('data:') !== 0) continue;
      let ev; try { ev = JSON.parse(line.slice(5).trim()); } catch (e) { continue; }
      if (ev.stage === 'done') result = ev.result;
      else if (ev.stage === 'error') errMsg = ev.error;
      else onProgress(ev);
    }
  }
  if (errMsg) throw { data: { error: errMsg } };
  if (!result) throw { data: { error: 'extraction ended without a result' } };
  return result;
}

f.onsubmit = async (e) => {
  e.preventDefault();
  go.disabled = true; go.textContent = 'Extracting…'; f.setAttribute('aria-busy', 'true');
  out.style.display = 'none'; out.innerHTML = '';
  setProgress(2, 'Starting…');
  try {
    renderResult(await streamExtract(new FormData(f)));
  } catch (err) {
    const data = (err && err.data) || { error: String((err && err.message) || err) };
    out.style.display = 'block';
    out.innerHTML = '<div class="banner err"><span class="ic" aria-hidden="true">✕</span>'
      + '<div><div>Extraction failed</div><div class="notes">' + esc(data.error || 'unknown') + '</div></div></div>';
  } finally {
    clearProgress(); go.disabled = false; go.textContent = 'Extract'; f.removeAttribute('aria-busy');
  }
};

function renderResult(data){
  out.style.display = 'block';
  const clean = !data.problems.length;
  let html = '<div class="banner ' + (clean ? 'ok' : 'warn') + '"><span class="ic" aria-hidden="true">'
    + (clean ? '✓' : '⚠') + '</span><div><div>' + esc(data.summary) + '</div>';
  if (data.problems.length)
    html += '<ul class="notes">' + data.problems.map(esc).map(p => '<li>' + p + '</li>').join('') + '</ul>';
  html += '</div></div>';
  lastBlob = new Blob([JSON.stringify(data.filing, null, 2)], { type: 'application/json' });
  lastName = data.filename; lastFiling = data.filing;
  lastSymbol = (data.filing && data.filing.metadata && data.filing.metadata.symbol) || '';
  lastAnalysis = data.analysis || null;
  html += '<div class="outputs"><span class="olabel">Outputs — pick what you need:</span>';
  html += '<a class="dl" id="dl" href="#">⬇ qscreen JSON</a>';
  html += '<a class="dl" id="xlsx" href="#">⬇ Excel transcript</a>';
  html += '<a class="dl" id="csv" href="#">⬇ CSV</a>';
  html += '<a class="dl" id="stmt" href="#">📄 Statements (HTML)</a>';
  html += '<a class="dl" id="rep" href="#">📰 Analyst report</a>';
  if (UPLOAD_ENABLED && clean)
    html += '<a class="dl up" id="up" href="#">⬆ Upload to qscreen.app</a>'
         + '<label class="inc"><input type="checkbox" id="incan"> include analysis in upload</label>';
  html += '</div>';
  html += renderSegments((data.analysis || {}).segments);
  html += renderAnalysis(data.analysis);
  html += renderDcfPanel();
  out.innerHTML = html;
  rememberFiling(lastName, lastFiling, lastAnalysis);
  wireOutputs();
}

function wireOutputs(){
  document.getElementById('dl').onclick = (ev) => {
    ev.preventDefault();
    const url = URL.createObjectURL(lastBlob);
    const a = document.createElement('a'); a.href = url; a.download = lastName; a.click();
    URL.revokeObjectURL(url);
  };
  async function dlPost(path, fname){
    const r = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'},
                                 body: JSON.stringify({ filing: lastFiling })});
    if(!r.ok){ const d = await r.json().catch(()=>({})); throw new Error(d.error || ('HTTP '+r.status)); }
    const url = URL.createObjectURL(await r.blob());
    const a = document.createElement('a'); a.href = url; a.download = fname; a.click(); URL.revokeObjectURL(url);
  }
  const xl = document.getElementById('xlsx');
  if (xl) xl.onclick = async (ev) => { ev.preventDefault(); const t = xl.textContent; xl.textContent = '⬇ Building…';
    try { await dlPost('/workbook', (lastSymbol||'filing') + '_transcript.xlsx'); xl.textContent = t; }
    catch(e){ xl.textContent = '⬇ Excel failed'; } };
  const cv = document.getElementById('csv');
  if (cv) cv.onclick = async (ev) => { ev.preventDefault();
    try { await dlPost('/export.csv', (lastSymbol||'filing') + '_line_items.csv'); }
    catch(e){ cv.textContent = '⬇ CSV failed'; } };
  const dg = document.getElementById('dcfgo');
  if (dg) dg.onclick = (ev) => { ev.preventDefault(); runDcf(); };
  const st = document.getElementById('stmt');
  if (st) st.onclick = async (ev) => {
    ev.preventDefault(); const label = st.textContent; st.textContent = '📄 Building…';
    try {
      const r = await fetch('/statements', { method: 'POST', headers: {'Content-Type':'application/json'},
                                             body: JSON.stringify({ filing: lastFiling }) });
      const d = await r.json(); if (!r.ok) throw new Error(d.error || 'failed');
      const url = URL.createObjectURL(new Blob([d.html], {type:'text/html'}));
      const a = document.createElement('a'); a.href = url; a.download = (lastSymbol||'filing') + '_statements.html'; a.click();
      URL.revokeObjectURL(url); st.textContent = label;
    } catch (e) { st.textContent = '📄 Statements failed'; }
  };
  const rp = document.getElementById('rep');
  if (rp) rp.onclick = async (ev) => {
    ev.preventDefault(); const label = rp.textContent; rp.textContent = '📰 Building…';
    try {
      const r = await fetch('/report', { method: 'POST', headers: {'Content-Type':'application/json'},
                                         body: JSON.stringify({ filing: lastFiling, symbol: lastSymbol }) });
      const d = await r.json(); if (!r.ok) throw new Error(d.error || 'failed');
      const url = URL.createObjectURL(new Blob([d.html], {type:'text/html'}));
      const a = document.createElement('a'); a.href = url; a.download = (lastSymbol||'report') + '_report.html'; a.click();
      URL.revokeObjectURL(url); rp.textContent = label;
    } catch (e) { rp.textContent = '📰 Report failed'; }
  };
  const up = document.getElementById('up');
  if (up) up.onclick = async (ev) => {
    ev.preventDefault();
    up.classList.add('busy'); up.textContent = '⬆ Uploading…';
    const note = document.createElement('div');
    try {
      const inc = document.getElementById('incan');
      const r = await fetch('/upload', { method: 'POST', headers: {'Content-Type':'application/json'},
                                         body: JSON.stringify({ filing: lastFiling,
                                           with_analysis: !!(inc && inc.checked), analysis: lastAnalysis }) });
      const d = await r.json();
      if (r.ok) { up.textContent = '✅ Uploaded to qscreen.app'; }
      else {
        up.classList.remove('busy'); up.textContent = '⬆ Retry upload';
        note.className = 'err';
        note.textContent = 'Upload failed: ' + (d.error || 'unknown') +
          (d.problems ? '\\n - ' + d.problems.join('\\n - ') : '');
        out.appendChild(note);
      }
    } catch (err) {
      up.classList.remove('busy'); up.textContent = '⬆ Retry upload';
      note.className = 'err'; note.textContent = 'Upload failed: ' + err; out.appendChild(note);
    }
  };
}
const cmpBtn = document.getElementById('cmpgo');
if (cmpBtn) cmpBtn.onclick = runCompare;
const dashBtn = document.getElementById('dashgo');
if (dashBtn) dashBtn.onclick = runDashboard;
const wbBtn = document.getElementById('wbgo');
if (wbBtn) wbBtn.onclick = runWorkbook;
const ttmBtn = document.getElementById('ttmgo');
if (ttmBtn) ttmBtn.onclick = runTtm;
</script>
</body></html>"""


@app.route("/")
def index():
    upload_enabled = bool(os.getenv("INGEST_TOKEN"))
    provider_info = {name: {"label": cfg["label"], "model": cfg["default_model"],
                            "url": cfg["key_url"], "env": cfg["env"][0],
                            "local": bool(cfg.get("local")), "setup": cfg.get("setup", "")}
                     for name, cfg in engine.PROVIDERS.items()}
    html = (PAGE
            .replace("__UI_CSS__", qscreen_ui.css())
            .replace("__SUBSECTOR_OPTIONS__", _subsector_options_html())
            .replace("__SYMBOL_MAP_JSON__", json.dumps(SYMBOL_SUBSECTOR))
            .replace("__PROVIDER_INFO_JSON__", json.dumps(provider_info))
            .replace("__UPLOAD_ENABLED__", "true" if upload_enabled else "false"))
    return Response(html, mimetype="text/html")


class _BadRequest(Exception):
    """A 400-level input problem with a user-safe message (no internals)."""


def _parse_extract_request(req):
    """Validate the multipart form, build the CLI-equivalent args, resolve the
    provider/mode/profile, and save the upload to a private temp file.

    Runs inside the Flask request context (it touches ``request``). Raises
    ``_BadRequest`` for user errors and lets ``SystemExit`` (provider/key/model
    config errors) propagate — both map to a 400 at the route. Returns a plain
    dict so the heavy lifting in ``_run_extract`` can run on a worker thread.
    """
    up = req.files.get("pdf")
    if not up:
        raise _BadRequest("no PDF uploaded")
    symbol = (req.form.get("symbol") or "").strip().upper()
    subsector = (req.form.get("subsector") or "").strip()
    year = req.form.get("year")
    period = (req.form.get("period") or "FY").strip()
    if not (symbol and subsector and year):
        raise _BadRequest("symbol, sub-sector and year are required")
    try:
        year = int(year)
    except (TypeError, ValueError):
        raise _BadRequest("year must be an integer")
    # The rich QSE sub-sector is stored; the extraction category (1 of 5)
    # drives how the LLM reads the statements.
    sector = SUBSECTOR_TO_EXTRACTION.get(subsector, "other")
    provider = (req.form.get("provider") or "").strip() or None  # None → auto-detect
    model = (req.form.get("model") or "").strip() or None
    mode = (req.form.get("mode") or "auto").strip()   # auto | basic | pro
    no_llm = bool(req.form.get("no_llm"))             # fully-offline checkbox

    # Build the same args object the CLI uses; resolve_provider picks the
    # base URL / model / key (from the matching env var) and validates them.
    args = SimpleNamespace(
        symbol=symbol, sector=sector, year=int(year), period=period,
        provider=provider, base_url=None, model=model,
        max_tokens=16384, timeout=600, retries=4,
        pages_per_chunk=12, overlap=1, no_chunk=False,
        no_json_mode=False, llm_key=None,
        mode=mode, basic=False, pro=False, no_llm=no_llm,
        guided=False, no_guided=False, guided_notes=False,
    )
    engine.apply_mode(args)               # --mode/--no-llm → guided flags
    # Fully-offline (--no-llm) needs no provider at all; otherwise resolve it.
    try:
        cfg = engine.resolve_provider(args)   # raises SystemExit if no provider/key
    except SystemExit:
        if no_llm:
            cfg = engine.deterministic_cfg()
        else:
            raise
    args.guided = engine.resolve_guided(args, cfg)   # Basic vs Pro
    if no_llm:
        args.guided = True
    if args.guided:
        args.pages_per_chunk = engine.GUIDED_DEFAULT_PAGES
    args._profile = qatar.profile_for_year(symbol, int(year))  # company+year-aware prompting

    # Save the upload to a private temp file (not a predictable CWD path).
    fd, tmp_path = tempfile.mkstemp(suffix=".pdf", prefix="qscreen_upload_")
    os.close(fd)
    up.save(tmp_path)
    return {"args": args, "cfg": cfg, "tmp_path": tmp_path,
            "symbol": symbol, "sector": sector, "subsector": subsector,
            "year": int(year), "period": period,
            "source_filename": Path(up.filename or "").name}


def _run_extract(parsed, progress_cb=None):
    """Run the engine on an already-parsed request and shape the JSON result.

    Pure compute — no Flask request context needed — so the streaming route can
    call it on a background thread, passing ``progress_cb`` to receive the
    engine's per-window events. ``progress_cb=None`` reproduces the blocking path.
    """
    args, cfg = parsed["args"], parsed["cfg"]
    symbol, year, period = parsed["symbol"], parsed["year"], parsed["period"]
    try:
        pages, sha = engine.pdf_to_pages(parsed["tmp_path"], progress_cb=progress_cb)
    finally:
        Path(parsed["tmp_path"]).unlink(missing_ok=True)

    filing = engine.extract_filing(pages, args, progress_cb)
    filing.setdefault("metadata", {}).update({
        "symbol": symbol, "sector": parsed["sector"], "sub_sector": parsed["subsector"],
        "fiscal_year": int(year),
        "fiscal_period": period, "source_file": parsed["source_filename"], "source_sha256": sha,
        "extracted_at": engine.datetime.now(engine.timezone.utc).isoformat(),
        "extractor": {"provider": cfg["name"], "model": cfg["model"]},
    })
    engine._emit(progress_cb, stage="validating", message="checking the filing contract")
    problems = engine.validate_filing(filing)
    try:                                       # analysis must never sink a good extraction
        analysis = qscreen_analyze.analyze(symbol, [filing], args._profile)
    except Exception as ex:
        analysis = {"warnings": [f"analysis failed: {ex}"], "ratios": {}, "trends": {},
                    "red_flags": [], "segments": {"dimensions": {}, "warnings": []}}
    nseg = len(filing.get("segments", []))
    nflags = len(analysis.get("red_flags", []))
    summary = (f"Extracted {len(filing.get('statements', []))} statements, "
               f"{nseg} segments, {len(filing.get('notes', []))} notes, "
               f"audit={filing.get('audit', {}).get('opinion_type')}, {nflags} red flag(s).")
    if problems:
        summary += f" ({len(problems)} note(s) below — review before uploading.)"
    else:
        summary += " Clean — ready to upload to qscreen.app."

    return {
        "summary": summary,
        "problems": problems,
        "filing": filing,
        "analysis": analysis,
        "filename": f"{symbol}_{year}_{period}_filing.json",
    }


@app.route("/extract", methods=["POST"])
def extract():
    """Blocking extract → JSON. The no-JS fallback; identical payload to the
    terminal 'done' event of /extract/stream."""
    try:
        parsed = _parse_extract_request(request)
        return _run_extract(parsed)
    except _BadRequest as e:
        return {"error": str(e)}, 400
    except SystemExit as e:                       # provider/key/model config errors
        return {"error": str(e)}, 400
    except Exception as e:
        # Log the full traceback server-side; do NOT leak it to the client (paths,
        # library internals, and any input echoed in the message).
        traceback.print_exc()
        return {"error": f"{type(e).__name__}: {e}"}, 500


@app.route("/extract/stream", methods=["POST"])
def extract_stream():
    """Same extract, streamed as Server-Sent Events so the browser can show a
    live progress bar. Parse + save the upload here (request context is live),
    then run the engine on a worker thread that feeds a thread-safe queue; the
    generator relays each event and a terminal 'done' (carrying the same payload
    /extract returns) or 'error' event."""
    try:
        parsed = _parse_extract_request(request)
    except _BadRequest as e:
        return {"error": str(e)}, 400
    except SystemExit as e:
        return {"error": str(e)}, 400
    except Exception as e:
        traceback.print_exc()
        return {"error": f"{type(e).__name__}: {e}"}, 500

    q: queue.Queue = queue.Queue()

    def worker():
        try:
            result = _run_extract(parsed, progress_cb=q.put)
            q.put({"stage": "done", "result": result})
        except SystemExit as e:
            q.put({"stage": "error", "error": str(e)})
        except Exception as e:
            traceback.print_exc()                 # server-side only
            q.put({"stage": "error", "error": f"{type(e).__name__}: {e}"})
        finally:
            q.put(None)                           # sentinel: stream complete

    def gen():
        threading.Thread(target=worker, daemon=True).start()
        while True:
            ev = q.get()
            if ev is None:
                break
            yield f"data: {json.dumps(ev)}\n\n"

    return Response(stream_with_context(gen()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                             "Connection": "keep-alive"})


@app.route("/workbook", methods=["POST"])
def workbook_route():
    """Excel financial-transcript workbook for a filing (or several, for more
    years). Body: {filing|filings}. Returns the .xlsx bytes as a download."""
    payload = request.get_json(silent=True) or {}
    filings = payload.get("filings")
    if filings is None and isinstance(payload.get("filing"), dict):
        filings = [payload["filing"]]
    if not isinstance(filings, list) or not filings:
        return {"error": "missing 'filings' (list) or 'filing' (object)"}, 400
    try:
        data = qscreen_workbook.workbook_bytes(filings[-1], filings)
    except Exception as e:
        return {"error": str(e)}, 400
    sym = _safe_filename((filings[-1].get("metadata") or {}).get("symbol"))
    return Response(data, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{sym}_transcript.xlsx"'})


@app.route("/ttm", methods=["POST"])
def ttm_route():
    """Period-aware TTM / quarterly roll-up for one company. Body: {filings|filing}."""
    payload = request.get_json(silent=True) or {}
    filings = payload.get("filings")
    if filings is None and isinstance(payload.get("filing"), dict):
        filings = [payload["filing"]]
    if not isinstance(filings, list) or not filings:
        return {"error": "missing 'filings' (list) or 'filing' (object)"}, 400
    try:
        return qscreen_periods.build_ttm(filings)
    except Exception as e:
        return {"error": str(e)}, 400


@app.route("/statements", methods=["POST"])
def statements_route():
    """Printable HTML statements document for a filing. Body: {filing}. Returns {html}."""
    payload = request.get_json(silent=True) or {}
    filing = payload.get("filing")
    if not isinstance(filing, dict):
        return {"error": "missing 'filing' object"}, 400
    try:
        return {"html": qscreen_statements.render_statements_html(filing)}
    except Exception as e:
        return {"error": str(e)}, 400


@app.route("/export.csv", methods=["POST"])
def export_csv_route():
    """Flat line-items CSV for a filing. Body: {filing}. Returns text/csv."""
    import csv
    import io as _io
    payload = request.get_json(silent=True) or {}
    filing = payload.get("filing")
    if not isinstance(filing, dict):
        return {"error": "missing 'filing' object"}, 400
    buf = _io.StringIO()
    w = csv.DictWriter(buf, fieldnames=engine.EXPORT_COLUMNS)
    w.writeheader()
    w.writerows(engine.flatten_line_items(filing))
    sym = _safe_filename((filing.get("metadata") or {}).get("symbol"))
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{sym}_line_items.csv"'})


@app.route("/analyze", methods=["POST"])
def analyze_route():
    """Full analysis (ratios/trends/red-flags/segments) for one or more filing
    JSONs of the same stock. Accepts {filings:[...]} or {filing:{...}}."""
    payload = request.get_json(silent=True) or {}
    filings = payload.get("filings")
    if filings is None and isinstance(payload.get("filing"), dict):
        filings = [payload["filing"]]
    if not isinstance(filings, list) or not filings:
        return {"error": "missing 'filings' (list) or 'filing' (object)"}, 400
    meta = (filings[-1].get("metadata") or {})
    symbol = payload.get("symbol") or meta.get("symbol") or ""
    if not symbol:
        return {"error": "could not determine symbol"}, 400
    profile = qatar.profile_for_year(symbol, meta.get("fiscal_year"))
    try:
        return qscreen_analyze.analyze(symbol, filings, profile)
    except Exception as e:
        return {"error": str(e)}, 400


@app.route("/portfolio", methods=["POST"])
def portfolio_route():
    """Screen & rank a basket. Body: {filings:[...]} (grouped by symbol). Returns
    the ranked board plus a ready-to-download HTML dashboard."""
    payload = request.get_json(silent=True) or {}
    filings = payload.get("filings")
    if not isinstance(filings, list) or not filings:
        return {"error": "missing 'filings' (list)"}, 400
    groups = qscreen_analyze.group_by_symbol(filings)
    if not groups:
        return {"error": "no filings carry a metadata.symbol"}, 400
    profiles = {s: qatar.profile_for_year(s, (fs[0].get("metadata") or {}).get("fiscal_year"))
                for s, fs in groups.items()}
    try:
        board = qscreen_portfolio.roll_up(groups, profiles)
        return {"count": board["count"], "rows": board["rows"],
                "html": qscreen_portfolio.render_html(board)}
    except Exception as e:
        return {"error": str(e)}, 400


@app.route("/report", methods=["POST"])
def report_route():
    """Build the one-page analyst report (HTML + Markdown) for a filing/series."""
    payload = request.get_json(silent=True) or {}
    filings = payload.get("filings")
    if filings is None and isinstance(payload.get("filing"), dict):
        filings = [payload["filing"]]
    if not isinstance(filings, list) or not filings:
        return {"error": "missing 'filings' (list) or 'filing' (object)"}, 400
    meta = (filings[-1].get("metadata") or {})
    symbol = payload.get("symbol") or meta.get("symbol") or ""
    if not symbol:
        return {"error": "could not determine symbol"}, 400
    profile = qatar.profile_for_year(symbol, meta.get("fiscal_year"))
    try:
        rep = qscreen_report.build_report(symbol, filings, profile,
                                          assumptions=payload.get("assumptions") or {},
                                          price=payload.get("price"), shares=payload.get("shares"))
        return {"symbol": rep["symbol"], "html": rep["html"], "markdown": rep["markdown"]}
    except Exception as e:
        return {"error": str(e)}, 400


@app.route("/compare", methods=["POST"])
def compare_route():
    """Rank a stock against peers. Body: {filings:[...]} (grouped by symbol) or
    {filings_by_symbol:{SYM:[...]}}, optional {target}."""
    payload = request.get_json(silent=True) or {}
    fbs = payload.get("filings_by_symbol")
    if fbs is None:
        filings = payload.get("filings")
        if not isinstance(filings, list) or not filings:
            return {"error": "missing 'filings' (list) or 'filings_by_symbol' (object)"}, 400
        fbs = qscreen_analyze.group_by_symbol(filings)
    if not fbs:
        return {"error": "no filings carry a metadata.symbol"}, 400
    target = (payload.get("target") or next(iter(fbs))).upper()
    profiles = {s: qatar.profile_for_year(s, (fs[0].get("metadata") or {}).get("fiscal_year"))
                for s, fs in fbs.items()}
    try:
        return qscreen_analyze.compare(target, fbs, profiles)
    except Exception as e:
        return {"error": str(e)}, 400


@app.route("/dcf", methods=["POST"])
def dcf_route():
    """Run the valuation simulator for a filing/series with adjustable
    assumptions. Body: {filing|filings, symbol?, assumptions{}, price?, shares?}."""
    payload = request.get_json(silent=True) or {}
    filings = payload.get("filings")
    if filings is None and isinstance(payload.get("filing"), dict):
        filings = [payload["filing"]]
    if not isinstance(filings, list) or not filings:
        return {"error": "missing 'filings' (list) or 'filing' (object)"}, 400
    meta = (filings[-1].get("metadata") or {})
    symbol = payload.get("symbol") or meta.get("symbol") or ""
    if not symbol:
        return {"error": "could not determine symbol"}, 400
    profile = qatar.profile_for_year(symbol, meta.get("fiscal_year"))
    try:
        return qscreen_dcf.value(symbol, filings, profile, payload.get("assumptions") or {},
                                 price=payload.get("price"), shares=payload.get("shares"))
    except Exception as e:
        return {"error": str(e)}, 400


@app.route("/segments", methods=["POST"])
def segments():
    """Re-run the segment breakdown for a filing JSON (uses the Qatar profile
    for FX/event annotations when the symbol+year resolve)."""
    payload = request.get_json(silent=True) or {}
    filing = payload.get("filing")
    if not isinstance(filing, dict):
        return {"error": "missing 'filing' object"}, 400
    meta = filing.get("metadata") or {}
    profile = qatar.profile_for_year(meta.get("symbol") or payload.get("symbol") or "",
                                     meta.get("fiscal_year") or payload.get("year"))
    try:
        return qscreen_analyze.analyze_segments(filing, profile)
    except Exception as e:
        return {"error": str(e)}, 400


@app.route("/upload", methods=["POST"])
def upload():
    """Opt-in upload of an already-extracted filing to qscreen.app.

    Only enabled when the server has INGEST_TOKEN set; the extract step never
    uploads on its own — the user clicks Upload explicitly. A non-conforming
    filing is rejected here too, mirroring the CLI's safety gate.
    """
    token = os.getenv("INGEST_TOKEN")
    if not token:
        return {"error": "No INGEST_TOKEN configured on the server; cannot upload."}, 400
    payload = request.get_json(silent=True) or {}
    filing = payload.get("filing")
    if not isinstance(filing, dict):
        return {"error": "missing 'filing' object"}, 400
    problems = engine.validate_filing(filing)
    if problems:
        return {"error": "filing is non-conforming; not uploading", "problems": problems}, 400
    args = SimpleNamespace(
        api_url=os.getenv("QSCREEN_API_URL", "http://localhost:3004"), token=token)
    # Both outputs: optionally fold the derived analysis into the upload (additive).
    analysis = payload.get("analysis") if payload.get("with_analysis") else None
    try:
        resp = (engine.upload_filing(filing, args, analysis) if analysis is not None
                else engine.upload_filing(filing, args))
        return {"ok": True, "response": resp}
    except Exception as e:
        return {"error": str(e)}, 502


def main() -> None:
    host = os.getenv("QSCREEN_APP_HOST", "127.0.0.1")
    port = int(os.getenv("QSCREEN_APP_PORT", "8765"))
    print(f"\n  QScreen Filing Ingestor — open  http://{host}:{port}  in your browser\n")
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"  ⚠️  Binding to {host} exposes this tool (and any INGEST_TOKEN) on your "
              "network. It has no authentication — only do this on a trusted network.\n")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
