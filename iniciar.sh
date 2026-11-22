#!/usr/bin/env bash
cd "$(dirname "$0")"
LINKS=()
[ -d wheels ] && LINKS=(--find-links wheels)
CONS=()
[ -f constraints.txt ] && CONS=(-c constraints.txt)
if [ ! -d .venv ]; then
  python3 -m venv .venv || exit 1
  .venv/bin/pip install -r requirements.txt "${LINKS[@]}" "${CONS[@]}" || exit 1
fi
exec .venv/bin/python app.py "$@"
