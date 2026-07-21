#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
python -m pip install -e ".[matting]"
if [ ! -d node_modules ]; then
  npm install
fi
set -a
[ -f .env ] && source .env
set +a
python -m clinic_broll.cli serve
