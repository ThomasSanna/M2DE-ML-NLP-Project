<?php

/**
 * État de l'assistant sur un ticket, pour le navigateur du demandeur.
 *
 * La clé d'API reste ici : le navigateur ne parle qu'à GLPI. Les deux appels
 * sont bornés à 2 s, et une API injoignable équivaut à « rien en cours ».
 */

include('../../../inc/includes.php');

Session::checkLoginUser();

$ticket_id = (int) ($_GET['id'] ?? 0);
$ticket = new Ticket();
if ($ticket_id <= 0 || !$ticket->getFromDB($ticket_id) || !$ticket->can($ticket_id, READ)) {
    http_response_code(403);
    exit;
}

header('Content-Type: application/json; charset=UTF-8');
echo json_encode(aiassistant_ticket_state($ticket_id), JSON_THROW_ON_ERROR);

/**
 * Réponse du proxy : progression en cours + sources des réponses passées.
 *
 * @return array{status: string, steps: array, followup_id: ?int, answers: array}
 */
function aiassistant_ticket_state(int $ticket_id): array
{
    $empty = ['status' => 'idle', 'steps' => [], 'followup_id' => null, 'answers' => []];

    $config = Config::getConfigurationValues('plugin:aiassistant', ['api_url', 'api_key']);
    $api_url = rtrim((string) ($config['api_url'] ?? ''), '/');
    $api_key = (new GLPIKey())->decrypt((string) ($config['api_key'] ?? ''));
    if ($api_url === '' || $api_key === null || $api_key === '') {
        return $empty;
    }

    $progress = aiassistant_get("$api_url/api/v1/tickets/$ticket_id/progress", $api_key);
    $answers = aiassistant_get("$api_url/api/v1/tickets/$ticket_id/answers", $api_key);
    if ($progress === null || $answers === null) {
        return $empty;
    }

    return [
        'status'      => (string) ($progress['status'] ?? 'idle'),
        'steps'       => is_array($progress['steps'] ?? null) ? $progress['steps'] : [],
        'followup_id' => $progress['followup_id'] ?? null,
        'answers'     => $answers,
    ];
}

/**
 * @return array<mixed>|null Le corps JSON décodé, ou null si l'appel échoue.
 */
function aiassistant_get(string $url, string $api_key): ?array
{
    // Pas de Guzzle ici : le client est déjà fourni par GLPI et évite une
    // dépendance, avec des délais courts pour ne pas bloquer la page du ticket.
    $client = new GuzzleHttp\Client(['timeout' => 2.0, 'connect_timeout' => 2.0]);
    try {
        $response = $client->get($url, ['headers' => ['X-API-Key' => $api_key]]);
    } catch (Throwable) {
        return null;
    }

    $body = json_decode((string) $response->getBody(), true);

    return is_array($body) ? $body : null;
}
