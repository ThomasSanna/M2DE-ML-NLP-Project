# API mémoire - référence

> Branche `feat/api-memoire`. Code dans [`api/`](../api/). Lancer et tester : voir le [README](../README.md), section « Memory API ».

L'API stocke des paires question/réponse avec l'embedding de la question (PostgreSQL + pgvector), retrouve les plus proches d'une nouvelle question, et reçoit les webhooks GLPI. Swagger : `http://localhost:8000/docs` (désactivé si `ENVIRONMENT=prod`).

## Endpoints

Routes métier sous `/api/v1`.

| Méthode | Chemin | Auth | Succès | Erreurs |
|---|---|---|---|---|
| `GET` | `/health/live` | - | `200` | - |
| `GET` | `/health/ready` | - | `200` | `503` |
| `POST` | `/api/v1/ingest/ticket` | signature GLPI (si secret défini) | `202` | `401`, `422` |
| `POST` | `/api/v1/memory/knowledge` | `X-API-Key` | `201` | `401`, `422`, `502` |
| `GET` | `/api/v1/memory/knowledge/{knowledge_id}` | `X-API-Key` | `200` | `401`, `404`, `422` |
| `POST` | `/api/v1/memory/search` | `X-API-Key` | `200` | `401`, `422`, `502` |
| `POST` | `/api/v1/answer` | `X-API-Key` | `200` | `401`, `422`, `502` |

### Conventions

- **Authentification** : en-tête `X-API-Key: <API_KEY>` sur toutes les routes `/api/v1/memory/*` et sur `/api/v1/answer`. Absente ou fausse → `401` `{"detail": "Clé API absente ou invalide"}`.
- **Validation** : champ invalide → `422` au format FastAPI standard.
- **Embeddings indisponibles** : toute route qui embedde répond `502` `{"detail": "Serveur d'embeddings indisponible"}`. La cause n'est que dans les logs.

### `GET /health/live`

Ne touche pas à la base. `200` : `{"status": "ok"}`.

### `GET /health/ready`

Exécute `SELECT 1`. `200` : `{"status": "ok"}`. `503` : `{"detail": "Base indisponible"}`.
Ne vérifie **pas** le serveur d'embeddings : une API « ready » peut répondre `502` sur les routes mémoire si le VPN est coupé.

### `POST /api/v1/ingest/ticket`

Reçoit un webhook GLPI, l'enregistre dans `glpi_event` (statut `received`) et répond immédiatement. Pas de `X-API-Key`.

- **En-têtes** : `X-GLPI-signature` et `X-GLPI-timestamp`, obligatoires seulement si `GLPI_WEBHOOK_SECRET` est défini.
- **Corps** : un objet JSON, stocké tel quel dans `payload`. Si `event` est une chaîne, elle est copiée dans `event`.
- **Sortie** : `202` `{"event_id": <int>}`.
- **Erreurs**, dans cet ordre :
  1. `401` `Signature GLPI invalide` : secret défini mais en-tête absent, signature fausse ou corps modifié.
  2. `422` `Corps JSON invalide`.
  3. `422` `Objet JSON attendu` : JSON valide mais pas un objet.
- **Signature** : `HMAC-SHA256(corps brut + timestamp, secret)`, format GLPI 11. Ne pas reparser ni re-sérialiser le corps avant vérification.
- Pas de déduplication. Le ticket n'est pas lu dans GLPI : c'est le rôle de la future pipeline.
- Secret vide = aucune vérification (un avertissement est loggé au démarrage).

### `POST /api/v1/memory/knowledge`

Ajoute une paire question/réponse. **Seule la question est embeddée**, avant l'insertion.

**Corps** (`KnowledgeCreate`)

| Champ | Type | Obligatoire | Contraintes | Défaut |
|---|---|---|---|---|
| `question` | texte | oui | ≥ 1 caractère | - |
| `answer` | texte | oui | ≥ 1 caractère | - |
| `category` | texte ou null | non | ≤ 64 caractères | `null` |
| `source` | `glpi_history`, `llm`, `technician` | non | enum | `glpi_history` |
| `glpi_ticket_id` | entier ou null | non | | `null` |

**Sortie** : `201` avec `KnowledgeRead` : `id`, `question`, `answer`, `category`, `source`, `status`, `glpi_ticket_id`, `reuse_count`, `created_at`, `updated_at`. `status` vaut `active` et `reuse_count` vaut `0` à la création.

- Embeddings KO → `502`, **rien n'est inséré**.
- Pas d'idempotence : une même requête crée un doublon.
- Pas de modification ni de suppression par l'API.

### `GET /api/v1/memory/knowledge/{knowledge_id}`

- `knowledge_id` : UUID (sinon `422`).
- `200` : `KnowledgeRead`, **quel que soit son `status`** (y compris `to_review` et `archived`).
- `404` : `{"detail": "Connaissance introuvable"}`.
- N'appelle pas le serveur d'embeddings : fonctionne sans VPN.

### `POST /api/v1/memory/search`

Renvoie les `top_k` connaissances **`active`** les plus proches de `query`.

**Corps** (`SearchRequest`)

| Champ | Type | Obligatoire | Contraintes | Défaut |
|---|---|---|---|---|
| `query` | texte | oui | ≥ 1 caractère | - |
| `top_k` | entier | non | 1 à 50 | `5` |

**Sortie** : `200`, liste de `SearchHit` (`{"knowledge": KnowledgeRead, "similarity": float}`), du plus au moins similaire. Liste vide si aucune connaissance active.

```bash
curl -X POST http://localhost:8000/api/v1/memory/search \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"query": "eduroam ne marche pas", "top_k": 3}'
```

- `similarity` = similarité cosinus brute, dans [−1, 1] (1 = identique). Aucune pondération (feedback, fraîcheur, réutilisation) pour l'instant.
- **Aucun seuil** : le top-k renvoie toujours `k` résultats s'il en existe, même peu pertinents.
- Lecture seule : `reuse_count` n'est pas incrémenté.

### `POST /api/v1/answer`

Answers a ticket thread. The first and last user messages (the problem and the current
request, 1000 characters at most) are searched in the memory (vector candidates, then the
reranker), and the model either answers from it or runs a web search
(Exa) first, for at most `AGENT_MAX_STEPS` steps. Synchronous: about 1 to 15 s depending on
the number of searches.

**Body** (`AnswerRequest`)

| Field | Type | Required | Constraints |
|---|---|---|---|
| `messages` | list of `{"role", "content"}` | yes | 1 to 50 items, the last one from `user` |
| `messages[].role` | `user` / `assistant` | yes | `assistant` is the support side (technician or bot) |
| `messages[].content` | text | yes | 1 to 20,000 characters, not blank |

```bash
curl -X POST http://localhost:8000/api/v1/answer \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"messages": [
        {"role": "user", "content": "Le wifi eduroam ne marche pas sur mon Mac"},
        {"role": "assistant", "content": "Quel message voyez-vous ?"},
        {"role": "user", "content": "Il demande le mot de passe en boucle"}
      ]}'
```

**Output**: `200`, `AnswerResult`:

```json
{
  "kind": "answer",
  "answer": "Supprimez le profil eduroam dans Réglages > Wi-Fi, puis ... [K1] [W2]",
  "sources": [
    {"ref": "K1", "type": "knowledge", "knowledge_id": "3f2a...", "question": "Eduroam refuse mon mot de passe"},
    {"ref": "W2", "type": "web", "url": "https://...", "title": "Configurer eduroam sous macOS"}
  ],
  "memory": [{"knowledge_id": "3f2a...", "score": 0.94}],
  "searches": ["eduroam macOS mot de passe en boucle"],
  "steps": 2,
  "usage": {"prompt_tokens": 3120, "completion_tokens": 210}
}
```

- `sources`: the refs the answer cites that exist, in citation order. `K<n>` is a memory
  entry, `W<n>` a web result; refs the model made up are dropped. In the answer text only
  bracketed refs (`[K1]`, `[K1, W2]`) count, and refs left in earlier answers of the thread
  are hidden from the model.
- `memory`: every retrieved entry with its reranker score in [0, 1], cited or not. Entries
  under `RERANK_THRESHOLD` are not retrieved.
- `searches`: the queries actually sent to Exa (duplicates are not resent).
- `502` with a generic `detail` when the embeddings server (`Embeddings server unavailable`),
  the reranker (`Reranker unavailable`) or the language model (`Language model unavailable`)
  fails, including a model that still returns invalid JSON at the last step. The cause is
  only logged.
- A failed web search does not fail the request: the model is told to answer from the
  memory. Without `EXA_API_KEY`, web search is disabled and the model can only answer.
- Read-only: `reuse_count` is not incremented.

## Schéma (résumé)

**`knowledge`** : `id`, `question`, `answer`, `category`, `source`, `status` (`active` / `to_review` / `archived`), `glpi_ticket_id`, `reuse_count`, `embedding` (`halfvec(3584)`, embedding de la question, index HNSW cosinus), `created_at`, `updated_at`.

**`feedback`** : un enregistrement par retour (`knowledge_id`, `source`, `score` ∈ [0, 1], `comment`, `glpi_ticket_id`, `created_at`). Non exposé par l'API pour l'instant.

**`glpi_event`** : `payload` (JSONB), `event`, `status` (`received` → `processing` → `done` / `failed`), `attempts`, `last_error`, `received_at`, `processed_at`. Consommée par la future pipeline.

## Configuration

Lue par `app/core/config.py` (environnement, et `.env` racine en local).

| Variable | Défaut | Rôle |
|---|---|---|
| `API_KEY` | **obligatoire** | clé des routes mémoire |
| `EMBEDDING_BASE_URL` | **obligatoire** | URL du serveur d'embeddings, avec `/v1` (VPN requis) |
| `EMBEDDING_MODEL` | `BAAI/bge-multilingual-gemma2` | modèle demandé au serveur |
| `EMBEDDING_API_KEY` | vide | si le serveur en exige une |
| `GLPI_WEBHOOK_SECRET` | vide | secret du webhook GLPI ; vide = signature non vérifiée |
| `LLM_BASE_URL` | **required** | university LLM URL (TGI, OpenAI-compatible), with `/v1` (VPN required) |
| `LLM_MODEL` | `google/gemma3-12b-it` | model name sent to the server (TGI ignores it) |
| `LLM_MAX_TOKENS` | `512` | max tokens generated per model call |
| `LLM_TIMEOUT_SECONDS` | `60.0` | timeout of one model call |
| `RERANKER_BASE_URL` | **required** | university reranker URL, without `/v1` (VPN required) |
| `RERANKER_TIMEOUT_SECONDS` | `30.0` | timeout of one rerank call |
| `RETRIEVAL_CANDIDATES` | `20` | vector-search candidates sent to the reranker |
| `RETRIEVAL_TOP_K` | `3` | max memory entries given to the model |
| `RERANK_THRESHOLD` | `0.5` | min reranker score (sigmoid of the logit, 0 to 1) to keep an entry |
| `EXA_API_KEY` | empty | Exa key; empty = web search disabled |
| `EXA_INCLUDE_DOMAINS` | empty | comma-separated domains web search is restricted to |
| `EXA_NUM_RESULTS` | `3` | results per web search |
| `EXA_TIMEOUT_SECONDS` | `15.0` | timeout of one web search |
| `AGENT_MAX_STEPS` | `3` | model calls per answer; the last one must answer |
| `POSTGRES_USER` / `_PASSWORD` / `_DB` | `memoire` / - / `memoire` | base de la mémoire |
| `POSTGRES_HOST` / `_PORT` | `localhost` / `5432` | forcé à `postgres:5432` dans Docker |
| `ENVIRONMENT`, `LOG_LEVEL` | `dev`, `INFO` | `prod` désactive `/docs` |

Une variable obligatoire manquante empêche le démarrage.

## Invariants à respecter

- **Changer `EMBEDDING_MODEL` ou l'instruction d'embedding rend les vecteurs stockés incompatibles** : il faut ré-embedder toute la table `knowledge`. Le même préfixe d'instruction s'applique aux questions stockées et aux requêtes (`core/config.py`).
- **VPN de l'université requis** pour tout ce qui embedde (ajout, recherche). Sans VPN : `502`, le reste fonctionne.
- **Ne pas modifier la migration `0001`** : toute évolution du schéma = une nouvelle migration.
- Après une modification du code : `docker compose up -d --build api` (l'image n'est pas montée en direct).
- `docker compose down -v` efface aussi la mémoire (volume `pg_data`).

## Tests

```bash
cd api && uv run pytest -v
```

Les tests démarrent leur propre Postgres+pgvector jetable (testcontainers, Docker requis) et appliquent les vraies migrations. Les embeddings sont remplacés par un faux embedder déterministe : pas besoin du VPN.

## Non implémenté

Pipeline LLM (garde-fous, génération, reranker, websearch), réponse dans GLPI, routes de feedback, pondération de la recherche.
