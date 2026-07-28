#!/usr/bin/env sh
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
export ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
export ADMIN_PASSWORD="${ADMIN_PASSWORD:-Admin123!}"
export SECRET_KEY="${SECRET_KEY:-clave-local-cambiar}"
python app.py
