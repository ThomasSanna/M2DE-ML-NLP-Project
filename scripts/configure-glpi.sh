#!/usr/bin/env bash
# Applique la configuration GLPI du PoC (API, LDAP, SMTP, notifications, enquête
# de satisfaction, utilisateur bot, webhook) et crée l'utilisateur SQL en lecture seule. Idempotent : à relancer après chaque `docker compose down -v`.
# Usage : ./scripts/configure-glpi.sh   (stack démarrée ; ENV_FILE=… pour un autre .env)
set -euo pipefail
cd "$(dirname "$0")/.."

set -a
# shellcheck disable=SC1091
source "${ENV_FILE:-.env}"
set +a

vars=(
  GLPI_URL_BASE GLPI_API_IP_START GLPI_API_IP_END
  LDAP_ADMIN_PASSWORD LDAP_BIND_DN LDAP_BIND_PASSWORD
  SMTP_HOST SMTP_PORT SMTP_USER SMTP_PASSWORD
  MAIL_FROM MAIL_FROM_NAME MAIL_ADMIN MAIL_ADMIN_NAME MAIL_SIGNATURE
  SURVEY_RATE SURVEY_COMMENT_THRESHOLD TICKET_AUTOCLOSE_DAYS
  GLPI_APP_TOKEN GLPI_BOT_USER GLPI_USER_TOKEN GLPI_WEBHOOK_SECRET GLPI_WEBHOOK_URL
)
env_args=()
for v in "${vars[@]}"; do
  env_args+=(-e "$v=${!v:-}")
done

echo "Attente de l'installation de GLPI..."
# config_db.php est écrit dès le début de l'installation : on attend plutôt Apache,
# que l'entrypoint ne lance qu'une fois l'installation (ou la mise à jour) terminée.
until docker compose exec -T glpi pgrep -x apache2 >/dev/null 2>&1; do
  sleep 5
done

docker compose exec -T -u www-data "${env_args[@]}" glpi php < glpi/init/configure.php

# ------------------------------------------- Utilisateur SQL en lecture seule
# Pour lire en masse l'historique et le feedback (constitution de la mémoire).
# Les écritures passent toujours par l'API REST (notifications, historique, droits).
if [ -n "${GLPI_DB_RO_PASSWORD:-}" ]; then
  ro_user="${GLPI_DB_RO_USER:-memoire_ro}"
  ro_pass="${GLPI_DB_RO_PASSWORD//\'/\'\'}"
  sql="CREATE USER IF NOT EXISTS '${ro_user}'@'%' IDENTIFIED BY '${ro_pass}';"
  sql+=" ALTER USER '${ro_user}'@'%' IDENTIFIED BY '${ro_pass}';"
  for table in glpi_tickets glpi_itilfollowups glpi_itilsolutions glpi_ticketsatisfactions \
               glpi_itilcategories glpi_tickets_users glpi_users; do
    sql+=" GRANT SELECT ON glpi.${table} TO '${ro_user}'@'%';"
  done
  docker compose exec -T -e MYSQL_PWD="$MARIADB_ROOT_PASSWORD" db mariadb --skip-ssl -uroot -e "$sql"
  echo "  - utilisateur SQL « ${ro_user} » : SELECT sur 7 tables GLPI (db:3306)"
else
  echo "  - GLPI_DB_RO_PASSWORD vide : utilisateur SQL en lecture seule non créé"
fi
