#!/usr/bin/env bash
# راه‌اندازی ایجنت سئو: محیط مجازی می‌سازد، وابستگی‌ها را نصب می‌کند و داشبورد را بالا می‌آورد.
set -euo pipefail
cd "$(dirname "$0")/services/engine"

if [ ! -d .venv ]; then
  echo "ساخت محیط مجازی…"
  python3 -m venv .venv
  .venv/bin/pip install --quiet --upgrade pip
  .venv/bin/pip install --quiet -r requirements.txt
fi

exec .venv/bin/python -m seoagent "$@"
