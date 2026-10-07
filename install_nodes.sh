#!/usr/bin/env bash
cd "$(dirname "$0")"
PY=python3; [ -x .venv/bin/python ] && PY=.venv/bin/python
exec "$PY" -m clefflow.install_nodes "$@"
