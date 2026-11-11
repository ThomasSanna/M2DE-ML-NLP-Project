#!/bin/bash
# Replaces the image's /opt/glpi/cron-worker.sh (glpi/glpi:11.0.8) with a 5 s interval
# instead of 60 s. GLPI never sends a webhook synchronously: it queues it, and the
# `queuedwebhook` task of this cron sends it. At 60 s, the bot started up to a minute
# after the ticket was created. A run of front/cron.php takes ~0.2 s, and scheduler.sh
# waits for one run to end before sleeping, so runs never overlap.
set -e -u -o pipefail

if [ "${GLPI_CRONTAB_ENABLED:-1}" = "1" ]; then
    echo "[INFO] GLPI cron is enabled. Starting worker tasks..."

    # Database waiting is done in scheduler.sh
    /opt/glpi/scheduler.sh --interval 5 --name "GLPI Cron" -- php /var/www/glpi/front/cron.php
else
    echo "[INFO] GLPI cron is disabled (GLPI_CRONTAB_ENABLED=0)."
    # Sleeping forever to keep supervisord happy
    tail -f /dev/null
fi
