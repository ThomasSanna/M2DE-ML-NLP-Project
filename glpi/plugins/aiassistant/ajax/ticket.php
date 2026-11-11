<?php

/**
 * État de l'assistant sur un ticket, pour le navigateur du demandeur.
 *
 * La clé d'API reste ici : le navigateur ne parle qu'à GLPI. Les deux appels
 * sont bornés à 2 s, et une API injoignable équivaut à « rien en cours ».
 */

include('../../../inc/includes.php');

/**
 * État vide : aucun traitement en cours, aucune réponse enregistrée.
 *
 * @return array{status: string, steps: array, followup_id: ?int, answers: array}
 */
function aiassistant_empty_state(): array
{
    return ['status' => 'idle', 'steps' => [], 'followup_id' => null, 'answers' => []];
}

/**
 * Renvoie l'état au navigateur en JSON.
 *
 * @param array{status: string, steps: array, followup_id: ?int, answers: array} $state
 */
function aiassistant_send_state(array $state, int $http_code = 200): void
{
    http_response_code($http_code);
    header('Content-Type: application/json; charset=UTF-8');
    try {
        // JSON_INVALID_UTF8_SUBSTITUTE : un texte amont exotique ne doit pas
        // faire échouer l'encodage et produire une erreur 500.
        echo json_encode($state, JSON_THROW_ON_ERROR | JSON_INVALID_UTF8_SUBSTITUTE);
    } catch (Throwable) {
        echo json_encode(aiassistant_empty_state(), JSON_THROW_ON_ERROR);
    }
}

try {
    Session::checkLoginUser();
} catch (Throwable) {
    // Session absente ou expirée : JSON 403, pas une redirection HTML vers le
    // login (le navigateur attend du JSON).
    aiassistant_send_state(aiassistant_empty_state(), 403);
    exit;
}

$id = (string) (is_scalar($_GET['id'] ?? null) ? $_GET['id'] : '');
if (!ctype_digit($id)) {
    aiassistant_send_state(aiassistant_empty_state(), 403);
    exit;
}

$ticket_id = (int) $id;
$ticket    = new Ticket();
if ($ticket_id <= 0 || !$ticket->getFromDB($ticket_id) || !$ticket->can($ticket_id, READ)) {
    aiassistant_send_state(aiassistant_empty_state(), 403);
    exit;
}

aiassistant_send_state(aiassistant_ticket_state($ticket_id));

/**
 * Réponse du proxy : progression en cours + sources des réponses passées.
 *
 * Les deux routes dégradent séparément : l'échec de l'une n'efface pas l'autre.
 *
 * @return array{status: string, steps: array, followup_id: ?int, answers: array}
 */
function aiassistant_ticket_state(int $ticket_id): array
{
    $config = Config::getConfigurationValues('plugin:aiassistant', ['api_url', 'api_key']);
    $api_url = rtrim((string) ($config['api_url'] ?? ''), '/');
    $api_key = (new GLPIKey())->decrypt((string) ($config['api_key'] ?? ''));
    if ($api_url === '' || $api_key === null || $api_key === '') {
        return aiassistant_empty_state();
    }

    $progress = aiassistant_get("$api_url/api/v1/tickets/$ticket_id/progress", $api_key);
    $answers  = aiassistant_get("$api_url/api/v1/tickets/$ticket_id/answers", $api_key);

    return [
        'status'      => $progress === null ? 'idle' : (string) ($progress['status'] ?? 'idle'),
        'steps'       => is_array($progress['steps'] ?? null) ? $progress['steps'] : [],
        'followup_id' => $progress['followup_id'] ?? null,
        'answers'     => $answers ?? [],
    ];
}

/**
 * @return array<mixed>|null Le corps JSON décodé, ou null si l'appel échoue.
 */
function aiassistant_get(string $url, string $api_key): ?array
{
    // Guzzle est déjà une dépendance de GLPI : on n'ajoute donc aucune
    // dépendance ici. Délais courts pour ne pas bloquer la page du ticket.
    $client = new GuzzleHttp\Client(['timeout' => 2.0, 'connect_timeout' => 2.0]);
    try {
        $response = $client->get($url, ['headers' => ['X-API-Key' => $api_key]]);
    } catch (Throwable) {
        return null;
    }

    $body = json_decode((string) $response->getBody(), true);

    return is_array($body) ? $body : null;
}
