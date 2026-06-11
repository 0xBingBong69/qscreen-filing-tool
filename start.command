#!/bin/bash
# ════════════════════════════════════════════════════════════════════════════
#  QScreen Filing Tool — double-click launcher (macOS / Linux)
#
#  Just double-click this file. It sets everything up the first time, then
#  starts the app and opens your web browser. No terminal knowledge needed.
#  To stop the tool later, close the window that opens (or press Ctrl+C).
# ════════════════════════════════════════════════════════════════════════════
cd "$(dirname "$0")" || exit 1

# 1) Find Python
PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo
  echo "  Python is not installed yet."
  echo "  Get it free from  https://www.python.org/downloads/  then double-click this again."
  echo
  read -r -p "  Press Enter to close…" _
  exit 1
fi

# 2) First-time setup: a private environment + the pieces the tool needs
if [ ! -x ".venv/bin/python" ]; then
  echo
  echo "  First-time setup — installing what the tool needs."
  echo "  This can take a few minutes (it downloads the offline reader). Please wait…"
  echo
  "$PY" -m venv .venv || { echo "  Could not create the environment."; read -r -p "  Press Enter to close…" _; exit 1; }
  ./.venv/bin/python -m pip install --upgrade pip >/dev/null 2>&1
  if ! ./.venv/bin/python -m pip install -r requirements.txt; then
    echo "  Install failed — please check your internet connection and try again."
    read -r -p "  Press Enter to close…" _
    exit 1
  fi
fi

# 3) Start the app (it opens your browser at http://127.0.0.1:8765)
echo
echo "  Starting QScreen… your web browser will open in a moment."
echo "  Keep this window open while you use the tool; close it to stop."
echo
exec ./.venv/bin/python qscreen_app.py
