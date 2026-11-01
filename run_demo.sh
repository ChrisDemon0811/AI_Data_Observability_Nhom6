#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m observability.cli seed
.venv/bin/python -m uvicorn observability.api:app --host 127.0.0.1 --port 8000 &
API_PID=$!
.venv/bin/python -m observability.scheduler &
SCHEDULER_PID=$!
trap 'kill "$API_PID" "$SCHEDULER_PID" 2>/dev/null || true; wait "$API_PID" "$SCHEDULER_PID" 2>/dev/null || true' EXIT
echo "API: http://127.0.0.1:8000/docs | Dashboard: http://127.0.0.1:8501"
echo "Scheduler interval: ${SCHEDULER_INTERVAL_SECONDS:-300}s; logs/scheduler.log; Ctrl+C to stop all three."
sleep 3
.venv/bin/python -m streamlit run dashboard.py --server.address 127.0.0.1 --server.port 8501
