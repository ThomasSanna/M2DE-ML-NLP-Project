<?php

/**
 * Reproducible GLPI configuration for the PoC.
 *
 * Run INSIDE the GLPI container (via scripts/configure-glpi.sh), with the GLPI
 * kernel loaded: passwords (LDAP bind, SMTP) are therefore encrypted by GLPI
 * itself (GLPIKey), exactly as through the interface.
 *
 * Idempotent: can be rerun as many times as needed. An empty environment
 * variable = setting left as is (an existing value is never overwritten with an empty one).
 */

require '/var/www/glpi/vendor/autoload.php';

$kernel = new \Glpi\Kernel\Kernel('production');
$kernel->boot();

// GLPI's error handler prints exceptions but leaves the exit code at 0: force a
// visible failure for the calling script.
set_exception_handler(static function (Throwable $e): void {
    fwrite(STDERR, 'ERROR: ' . $e->getMessage() . "\n");
    exit(1);
});

global $DB;

$_SESSION['glpi_currenttime'] ??= date('Y-m-d H:i:s');
// CLI "cron" context: without a logged-in user, Entity::update() otherwise
// filters out every field for lack of rights.
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

/** Only writes non-null values. */
function setCoreConfig(array $values): void
{
    Config::setConfigurationValues('core', array_filter($values, static fn($v) => $v !== null));
}

// ------------------------------------------------------------------ General
echo "[1/7] General\n";
setCoreConfig([
    // URL used in the links of the mails (http://localhost without a port = broken links)
    'url_base'     => env('GLPI_URL_BASE', 'http://localhost:8080'),
    'url_base_api' => env('GLPI_URL_BASE', 'http://localhost:8080') . '/apirest.php',
]);
step('url_base = ' . env('GLPI_URL_BASE', 'http://localhost:8080'));

// ---------------------------------------------------------------------- API
echo "[2/7] REST API\n";
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
step('API enabled, client #1: ' . env('GLPI_API_IP_START', '172.16.0.0') . ' → ' . env('GLPI_API_IP_END', '172.31.255.255'));
if (env('GLPI_APP_TOKEN') !== null) {
    // APIClient::prepareInputForUpdate encrypts the token (GLPIKey)
    (new APIClient())->update(['id' => 1, 'app_token' => env('GLPI_APP_TOKEN')]);
    step('App-Token of client #1 set (GLPI_APP_TOKEN)');
}

// --------------------------------------------------------------------- LDAP
echo "[3/7] LDAP directory\n";
$ldapName  = 'Univ Corse LDAP';
$ldapInput = [
    'name'            => $ldapName,
    'host'            => env('LDAP_HOST', 'ldap'),     // Docker service name
    'port'            => (int) env('LDAP_PORT', '389'),
    'basedn'          => env('LDAP_BASE_DN', 'dc=example,dc=org'),
    'use_bind'        => 1,
    'rootdn'          => env('LDAP_BIND_DN', 'cn=admin,dc=example,dc=org'),
    'rootdn_passwd'   => env('LDAP_BIND_PASSWORD', env('LDAP_ADMIN_PASSWORD')),
    'condition'       => '(&(objectClass=inetOrgPerson)(uid=*))',
    'login_field'     => 'uid',
    'sync_field'      => 'entryuuid',
    'realname_field'  => 'sn',
    'firstname_field' => 'givenname',   // lowercase, otherwise the first name stays empty
    'email1_field'    => 'mail',
    'is_default'      => 1,
    'is_active'       => 1,
];
$authldap = new AuthLDAP();
if ($authldap->getFromDBByCrit(['name' => $ldapName])) {
    $authldap->update(['id' => $authldap->getID()] + $ldapInput);
    step("source \"{$ldapName}\" updated (id {$authldap->getID()})");
} else {
    $authldap->add($ldapInput);
    step("source \"{$ldapName}\" created (id {$authldap->getID()})");
}
setCoreConfig([
    'is_users_auto_add'     => 1,   // automatic import on first LDAP login
    'use_noright_users_add' => 1,   // even without prior authorization
]);
$ok = AuthLDAP::testLDAPConnection($authldap->getID());
step('LDAP connection test: ' . ($ok ? 'OK' : 'FAILED'));

// ------------------------------------------------------ Notifications / SMTP
echo "[4/7] E-mail notifications\n";
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
        'smtp_mode'              => 1,   // SMTP (TLS negotiated automatically on 587)
        'smtp_host'              => env('SMTP_HOST'),
        'smtp_port'              => (int) env('SMTP_PORT', '587'),
        'smtp_username'          => env('SMTP_USER'),
        'smtp_passwd'            => env('SMTP_PASSWORD'),   // null = existing password kept
        'smtp_check_certificate' => 1,
        'smtp_max_retries'       => 5,
        'smtp_retry_time'        => 5,
    ]);
    step('SMTP: ' . env('SMTP_HOST') . ':' . env('SMTP_PORT', '587')
        . (env('SMTP_PASSWORD') === null ? ' (password unchanged)' : ' (password updated)'));
} else {
    step('SMTP_HOST empty: SMTP configuration left as is');
}

// ------------------------------------------- Feedback: satisfaction survey
echo "[5/7] Satisfaction survey (source of the w1 feedback)\n";
$entity = new Entity();
$entity->update([
    'id'                        => 0,
    'inquest_config'            => 1,   // GLPI internal survey
    'inquest_rate'              => (int) env('SURVEY_RATE', '100'),   // % of closed tickets surveyed
    'inquest_delay'             => 0,   // survey created as soon as the ticket is closed
    'inquest_duration'          => 0,   // no expiration
    'inquest_max_rate'          => 5,   // score from 0 to 5
    'inquest_default_rate'      => 3,
    // comment mandatory if score <= threshold: feeds the "to review" queue
    'inquest_mandatory_comment' => (int) env('SURVEY_COMMENT_THRESHOLD', '2'),
    // solved tickets left unapproved are closed automatically → the survey is still sent
    'autoclose_delay'           => (int) env('TICKET_AUTOCLOSE_DAYS', '7'),
]);
step('survey: ' . env('SURVEY_RATE', '100') . ' % of closed tickets, score 0–5, comment mandatory if ≤ '
    . env('SURVEY_COMMENT_THRESHOLD', '2') . ', auto-close after ' . env('TICKET_AUTOCLOSE_DAYS', '7') . ' d');

$DB->update('glpi_notifications', ['is_active' => 1], [
    'itemtype' => 'Ticket',
    'event'    => ['new', 'add_followup', 'solved', 'closed', 'satisfaction', 'replysatisfaction'],
]);
step('ticket notifications enabled: new, follow-up, solved, closed, survey, survey reply');

// Tasks run by the container's CLI cron (mode 2) instead of the "internal"
// mode, which only advances when a user browses GLPI.
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
step('CLI cron: mail sending (1 min), auto-close and survey creation (1 h)');
// If the machine clock went back (sleep, NTP resync), a `lastrun` in the future blocks
// the task until that date: mails and webhooks are no longer sent.
$DB->update('glpi_crontasks', ['lastrun' => null], [new \Glpi\DBAL\QueryExpression('`lastrun` > NOW()')]);

// ------------------------------------------------------------ API bot user
echo "[6/7] Bot user (memory API)\n";
$botName = env('GLPI_BOT_USER', 'chatbot');
$bot     = new User();
if (!$bot->getFromDBbyName($botName)) {
    $password = bin2hex(random_bytes(24));   // never used: the bot authenticates with a token
    $bot->add([
        'name'      => $botName,
        'realname'  => 'Assistant IA',
        'password'  => $password,
        'password2' => $password,
        'authtype'  => Auth::DB_GLPI,
        'is_active' => 1,
    ]);
    step("user \"{$botName}\" created (id {$bot->getID()})");
} else {
    step("user \"{$botName}\" already exists (id {$bot->getID()})");
}
// Technician profile on the whole entity tree: read tickets and add followups to them.
$technician = new Profile();
$technician->getFromDBByCrit(['name' => 'Technician']);
$profileUser = new Profile_User();
if (!$profileUser->getFromDBByCrit([
    'users_id' => $bot->getID(), 'profiles_id' => $technician->getID(), 'entities_id' => 0,
])) {
    $profileUser->add([
        'users_id'     => $bot->getID(),
        'profiles_id'  => $technician->getID(),
        'entities_id'  => 0,
        'is_recursive' => 1,
    ]);
}
// Default profile of the API session (otherwise Self-Service, which only sees its own tickets).
$DB->update('glpi_users', ['profiles_id' => $technician->getID(), 'entities_id' => 0], ['id' => $bot->getID()]);
if (env('GLPI_USER_TOKEN') !== null) {
    // User::update() ignores api_token: write the encrypted value directly, as GLPI does.
    $DB->update('glpi_users', [
        'api_token'      => (new GLPIKey())->encrypt(env('GLPI_USER_TOKEN')),
        'api_token_date' => $_SESSION['glpi_currenttime'],
    ], ['id' => $bot->getID()]);
    step('User-Token set (GLPI_USER_TOKEN), Technician as default profile');
} else {
    step('GLPI_USER_TOKEN empty: no API token for the bot');
}

// ------------------------------------------- New ticket and followup webhooks
echo "[7/7] New ticket and new followup webhooks → memory API\n";
if (env('GLPI_WEBHOOK_SECRET') === null) {
    step('GLPI_WEBHOOK_SECRET empty: webhooks not created (the API could not verify the signature)');
} else {
    // The URL must be allowed by GLPI_SERVERSIDE_URL_ALLOWLIST (glpi/config/local_define.php)
    $webhookUrl = env('GLPI_WEBHOOK_URL', 'http://api:8000/api/v1/ingest/ticket');
    // Same URL for both: the API tells a followup from a ticket by its payload.
    $webhooks = [
        'API mémoire — nouveau ticket' => 'Ticket',
        'API mémoire — nouveau suivi'  => 'ITILFollowup',
    ];
    foreach ($webhooks as $webhookName => $itemtype) {
        $webhookInput = [
            'name'                => $webhookName,
            'entities_id'         => 0,
            'is_recursive'        => 1,
            'itemtype'            => $itemtype,
            'event'               => 'new',
            'url'                 => $webhookUrl,
            'http_method'         => 'post',
            'secret'              => env('GLPI_WEBHOOK_SECRET'),   // encrypted by Webhook::handleInput
            'use_default_payload' => 1,
            'use_cra_challenge'   => 0,
            'sent_try'            => 5,
            'is_active'           => 1,
            'log_in_item_history' => 1,
        ];
        $webhook = new Webhook();
        if ($webhook->getFromDBByCrit(['name' => $webhookName])) {
            $webhook->update(['id' => $webhook->getID()] + $webhookInput);
            step("webhook updated (id {$webhook->getID()}, $itemtype): $webhookUrl");
        } else {
            $webhook->add($webhookInput);
            step("webhook created (id {$webhook->getID()}, $itemtype): $webhookUrl");
        }
    }
    if (!Toolbox::isUrlSafe($webhookUrl)) {
        step('WARNING: URL rejected by GLPI_SERVERSIDE_URL_ALLOWLIST, the webhooks will not be sent');
    }
}

echo "GLPI configuration done.\n";
