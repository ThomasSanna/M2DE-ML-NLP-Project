#!/usr/bin/env bash
# Exporte le feedback des enquêtes de satisfaction GLPI en TSV (stdout).
# Usage : ./scripts/export-feedback.sh > feedback.tsv
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source .env
docker compose exec -T -e MYSQL_PWD="$MARIADB_PASSWORD" db \
  mariadb --batch -uglpi glpi < glpi/sql/feedback.sql
