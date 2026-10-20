#!/usr/bin/env bash
# Apply the PoC GLPI configuration (API, LDAP, SMTP, notifications, satisfaction
# survey). Idempotent: rerun after every `docker compose down -v`.
# Usage: ./scripts/configure-glpi.sh   (stack started, GLPI installed)
set -euo pipefail
cd "$(dirname "$0")/.."

set -a
# shellcheck disable=SC1091
source .env
set +a

vars=(
  GLPI_URL_BASE GLPI_API_IP_START GLPI_API_IP_END
  LDAP_ADMIN_PASSWORD LDAP_BIND_DN LDAP_BIND_PASSWORD
  SMTP_HOST SMTP_PORT SMTP_USER SMTP_PASSWORD
  MAIL_FROM MAIL_FROM_NAME MAIL_ADMIN MAIL_ADMIN_NAME MAIL_SIGNATURE
  SURVEY_RATE SURVEY_COMMENT_THRESHOLD TICKET_AUTOCLOSE_DAYS
)
env_args=()
for v in "${vars[@]}"; do
  env_args+=(-e "$v=${!v:-}")
done

echo "Waiting for the GLPI installation..."
until docker compose exec -T glpi test -f /var/glpi/config/config_db.php 2>/dev/null; do
  sleep 5
done

docker compose exec -T -u www-data "${env_args[@]}" glpi php < glpi/init/configure.php
