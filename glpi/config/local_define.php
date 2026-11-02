<?php

/**
 * PoC-specific GLPI constants, mounted into /var/www/glpi/config/ (docker-compose.yml).
 *
 * GLPI's default allowlist rejects any URL with a port (`http://api:8000/...`): the
 * webhook to the memory API would never be sent. Defining the constant REPLACES the
 * default list: only these URLs can be called server-side (webhooks, RSS feeds,
 * external calendars).
 */
define('GLPI_SERVERSIDE_URL_ALLOWLIST', [
    '~^http://api:8000/api/v1/~',   // memory API, Docker network poc_net
]);
