<?php

/**
 * Constantes GLPI propres au PoC, montées dans /var/www/glpi/config/ (docker-compose.yml).
 *
 * L'allowlist par défaut de GLPI refuse toute URL avec un port (`http://api:8000/…`) :
 * le webhook vers l'API mémoire ne partirait jamais. Définir la constante REMPLACE la
 * liste par défaut : seules ces URL peuvent être appelées côté serveur (webhooks, flux
 * RSS, calendriers externes).
 */
define('GLPI_SERVERSIDE_URL_ALLOWLIST', [
    '~^http://api:8000/api/v1/~',   // API mémoire, réseau Docker poc_net
]);
