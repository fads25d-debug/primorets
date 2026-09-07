#!/bin/sh
set -eu
cd "$(dirname "$0")"
exec .venv/bin/python -m primorets --data-dir ./data

