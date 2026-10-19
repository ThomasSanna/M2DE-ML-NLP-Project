<?php

/**
 * Configuration reproductible de GLPI pour le PoC.
 *
 * Exécuté DANS le conteneur GLPI (via scripts/configure-glpi.sh), avec le noyau
 * GLPI chargé : les mots de passe (bind LDAP, SMTP) sont donc chiffrés par GLPI
 * lui-même (GLPIKey), exactement comme via l'interface.
 *
 * Idempotent : peut être relancé autant de fois que voulu. Une variable
 * d'environnement vide = réglage laissé tel quel (on n'écrase jamais avec du vide).
 */

require '/var/www/glpi/vendor/autoload.php';

$kernel = new \Glpi\Kernel\Kernel('production');
$kernel->boot();

global $DB;

$_SESSION['glpi_currenttime'] ??= date('Y-m-d H:i:s');
// Contexte « cron » CLI : sans utilisateur connecté, Entity::update() filtre
// sinon tous les champs faute de droits.
$_SESSION['glpicronuserrunning'] = 'poc-configure';

function env(string $name, ?string $default = null): ?string
{
    $value = getenv($name);
    return ($value === false || $value === '') ? $default : $value;
}

function step(string $message): void
{
    echo "  - {$message}\n";
}

/** N'écrit que les valeurs non nulles. */
function setCoreConfig(array $values): void
{
    Config::setConfigurationValues('core', array_filter($values, static fn($v) => $v !== null));
}

// ------------------------------------------------------------------ Général
echo "[1/5] Général\n";
setCoreConfig([
    // URL utilisée dans les liens des mails (http://localhost sans port = liens cassés)
    'url_base'     => env('GLPI_URL_BASE', 'http://localhost:8080'),
    'url_base_api' => env('GLPI_URL_BASE', 'http://localhost:8080') . '/apirest.php',
]);
step('url_base = ' . env('GLPI_URL_BASE', 'http://localhost:8080'));

// ---------------------------------------------------------------------- API
echo "[2/5] API REST\n";
setCoreConfig([
    'enable_api'                      => 1,
    'enable_api_login_credentials'    => 1,
    'enable_api_login_external_token' => 1,
]);
$DB->update('glpi_apiclients', [
    'is_active'        => 1,
    'ipv4_range_start' => ip2long(env('GLPI_API_IP_START', '172.16.0.0')),
    'ipv4_range_end'   => ip2long(env('GLPI_API_IP_END', '172.31.255.255')),
], ['id' => 1]);
step('API activée, client #1 : ' . env('GLPI_API_IP_START', '172.16.0.0') . ' → ' . env('GLPI_API_IP_END', '172.31.255.255'));

// --------------------------------------------------------------------- LDAP
echo "[3/5] Annuaire LDAP\n";
$ldapName  = 'Univ Corse LDAP';
$ldapInput = [
    'name'            => $ldapName,
    'host'            => env('LDAP_HOST', 'ldap'),     // nom du service Docker
    'port'            => (int) env('LDAP_PORT', '389'),
    'basedn'          => env('LDAP_BASE_DN', 'dc=example,dc=org'),
    'use_bind'        => 1,
    'rootdn'          => env('LDAP_BIND_DN', 'cn=admin,dc=example,dc=org'),
    'rootdn_passwd'   => env('LDAP_BIND_PASSWORD', env('LDAP_ADMIN_PASSWORD')),
    'condition'       => '(&(objectClass=inetOrgPerson)(uid=*))',
    'login_field'     => 'uid',
    'sync_field'      => 'entryuuid',
    'realname_field'  => 'sn',
    'firstname_field' => 'givenname',   // en minuscules, sinon le prénom reste vide
    'email1_field'    => 'mail',
    'is_default'      => 1,
    'is_active'       => 1,
];
$authldap = new AuthLDAP();
if ($authldap->getFromDBByCrit(['name' => $ldapName])) {
    $authldap->update(['id' => $authldap->getID()] + $ldapInput);
    step("source « {$ldapName} » mise à jour (id {$authldap->getID()})");
} else {
    $authldap->add($ldapInput);
    step("source « {$ldapName} » créée (id {$authldap->getID()})");
}
setCoreConfig([
    'is_users_auto_add'     => 1,   // import auto à la 1re connexion LDAP
    'use_noright_users_add' => 1,   // même sans habilitation préalable
]);
$ok = AuthLDAP::testLDAPConnection($authldap->getID());
step('test de connexion LDAP : ' . ($ok ? 'OK' : 'ÉCHEC'));

// ------------------------------------------------------ Notifications / SMTP
echo "[4/5] Notifications e-mail\n";
setCoreConfig([
    'use_notifications'     => 1,
    'notifications_mailing' => 1,
    'admin_email'           => env('MAIL_ADMIN'),
    'admin_email_name'      => env('MAIL_ADMIN_NAME'),
    'from_email'            => env('MAIL_FROM'),
    'from_email_name'       => env('MAIL_FROM_NAME', 'GLPI Support'),
    'replyto_email'         => env('MAIL_FROM'),
    'replyto_email_name'    => env('MAIL_FROM_NAME', 'GLPI Support'),
    'noreply_email'         => env('MAIL_FROM'),
    'smtp_sender'           => env('MAIL_FROM'),
    'mailing_signature'     => env('MAIL_SIGNATURE'),
]);
if (env('SMTP_HOST') !== null) {
    setCoreConfig([
        'smtp_mode'              => 1,   // SMTP (TLS négocié automatiquement sur 587)
        'smtp_host'              => env('SMTP_HOST'),
        'smtp_port'              => (int) env('SMTP_PORT', '587'),
        'smtp_username'          => env('SMTP_USER'),
        'smtp_passwd'            => env('SMTP_PASSWORD'),   // null = mot de passe existant conservé
        'smtp_check_certificate' => 1,
        'smtp_max_retries'       => 5,
        'smtp_retry_time'        => 5,
    ]);
    step('SMTP : ' . env('SMTP_HOST') . ':' . env('SMTP_PORT', '587')
        . (env('SMTP_PASSWORD') === null ? ' (mot de passe inchangé)' : ' (mot de passe mis à jour)'));
} else {
    step('SMTP_HOST vide : configuration SMTP laissée telle quelle');
}

// ------------------------------------------- Feedback : enquête de satisfaction
echo "[5/5] Enquête de satisfaction (source du feedback w1)\n";
$entity = new Entity();
$entity->update([
    'id'                        => 0,
    'inquest_config'            => 1,   // enquête interne GLPI
    'inquest_rate'              => (int) env('SURVEY_RATE', '100'),   // % de tickets clos enquêtés
    'inquest_delay'             => 0,   // enquête créée dès la clôture
    'inquest_duration'          => 0,   // pas d'expiration
    'inquest_max_rate'          => 5,   // note de 0 à 5
    'inquest_default_rate'      => 3,
    // commentaire obligatoire si note <= seuil : alimente la file « à réviser »
    'inquest_mandatory_comment' => (int) env('SURVEY_COMMENT_THRESHOLD', '2'),
    // tickets résolus non approuvés clos automatiquement → l'enquête part quand même
    'autoclose_delay'           => (int) env('TICKET_AUTOCLOSE_DAYS', '7'),
]);
step('enquête : ' . env('SURVEY_RATE', '100') . ' % des tickets clos, note 0–5, commentaire obligatoire si ≤ '
    . env('SURVEY_COMMENT_THRESHOLD', '2') . ', clôture auto après ' . env('TICKET_AUTOCLOSE_DAYS', '7') . ' j');

$DB->update('glpi_notifications', ['is_active' => 1], [
    'itemtype' => 'Ticket',
    'event'    => ['new', 'add_followup', 'solved', 'closed', 'satisfaction', 'replysatisfaction'],
]);
step('notifications ticket actives : nouveau, suivi, résolu, clos, enquête, réponse à l\'enquête');

// Tâches exécutées par le cron CLI du conteneur (mode 2) au lieu du mode
// « interne » qui n'avance que lorsqu'un utilisateur navigue dans GLPI.
$crons = [
    ['QueuedNotification', 'queuednotification', 60],
    ['Ticket', 'closeticket', 3600],
    ['Ticket', 'createinquest', 3600],
];
foreach ($crons as [$itemtype, $name, $frequency]) {
    $DB->update('glpi_crontasks', [
        'state'     => CronTask::STATE_WAITING,
        'mode'      => CronTask::MODE_EXTERNAL,
        'frequency' => $frequency,
    ], ['itemtype' => $itemtype, 'name' => $name]);
}
step('cron CLI : envoi des mails (1 min), clôture auto et création d\'enquêtes (1 h)');

echo "Configuration GLPI terminée.\n";
