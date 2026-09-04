#!/usr/bin/env bash
# One-command local run: installs deps, seeds the DB, starts the server.
set -e
cd "$(dirname "$0")"
python3 -m venv .venv 2>/dev/null || true
source .venv/bin/activate
pip install -q -r requirements.txt
cd backend
if [ ! -f agentguard.db ]; then
  python seed.py
fi
echo ""
echo "AgentGuard starting at http://localhost:8000"
echo "Dashboard:            http://localhost:8000/"
echo "API docs:             http://localhost:8000/docs"
echo ""
uvicorn main:app --reload --port 8000
