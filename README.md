# GLPI Support Chatbot - PoC

Proof of concept for a GLPI support chatbot backed by a vector memory of past question/answer pairs.

The repository ships a fully simulated environment:

- **OpenLDAP** with a fictitious directory (`example.org`) and phpLDAPadmin
- **GLPI 11** on **MariaDB**, preconfigured for LDAP, REST API, SMTP and satisfaction surveys
- **Memory API** (FastAPI + PostgreSQL/pgvector) storing Q/A pairs, serving similarity search, and answering ticket threads from that memory and a web search

## Prerequisites

- Docker with Docker Compose v2
- A bash shell for `scripts/*.sh` (Git Bash or WSL on Windows)
- Access to the university VPN, for the embeddings, reranker and LLM servers (see [Embeddings](#embeddings)). `LLM_BASE_URL` and `RERANKER_BASE_URL` are required: an existing `.env` without them stops the API from starting
- For local API development only: [uv](https://docs.astral.sh/uv/) (Python 3.12 is installed by uv)

## Quick start

```bash
# 1. Create the environment file, then replace every ChangeMe* value
cp .env.example .env

# 2. Start the stack (GLPI installs itself on first boot)
docker compose up -d

# 3. Apply the GLPI configuration (waits for the GLPI install to finish)
./scripts/configure-glpi.sh

# 4. Check that the memory API is up and reaches its database
curl http://localhost:8000/health/ready

# 5. Optional: seed the memory with sample Q/A pairs (VPN required)
API_KEY=<your API_KEY> python scripts/load_knowledge.py
```

`configure-glpi.sh` is idempotent. Rerun it after any `docker compose down -v`. `load_knowledge.py` is not: running it twice inserts every pair twice.

## Services

| Service | Access | Credentials |
|---|---|---|
| GLPI | http://localhost:8080 | `glpi` / `glpi` (default, change it) |
| Memory API (Swagger UI) | http://localhost:8000/docs | `X-API-Key` header = `API_KEY` |
| phpLDAPadmin | http://localhost:8081 | DN `cn=admin,dc=example,dc=org` / `LDAP_ADMIN_PASSWORD` |
| LDAP | `ldap://localhost:389` | base `dc=example,dc=org` |
| PostgreSQL + pgvector | `127.0.0.1:5432` | `POSTGRES_USER` / `POSTGRES_PASSWORD` |
| MariaDB | internal only (`db:3306`) | `glpi` / `MARIADB_PASSWORD` |

Uppercase names refer to variables in `.env`.

## Memory API

Source in [`api/`](api/). Full reference (endpoints, schema, configuration, invariants): [`doc/api_doc.md`](doc/api_doc.md).

Compose runs it as three services: `postgres` (PostgreSQL 17 + pgvector), `migrate` (one-shot `alembic upgrade head`) and `api` (started once migrations succeed).

| Method | Route | Auth | Purpose |
|---|---|---|---|
| `GET` | `/health/live` | none | process is up |
| `GET` | `/health/ready` | none | database is reachable |
| `POST` | `/api/v1/ingest/ticket` | GLPI signature | queue a GLPI webhook (`202`) |
| `POST` | `/api/v1/memory/knowledge` | `X-API-Key` | add a Q/A pair (the question is embedded) |
| `GET` | `/api/v1/memory/knowledge/{id}` | `X-API-Key` | read a knowledge entry |
| `POST` | `/api/v1/memory/search` | `X-API-Key` | top-k cosine similarity search over `active` entries |
| `POST` | `/api/v1/answer` | `X-API-Key` | answer a ticket thread from memory and web search, with sources |
| `POST` | `/api/v1/tickets/{id}/answer/stream` | `X-API-Key` | answer a GLPI ticket and post the answer as a followup (SSE) |

When `GLPI_WEBHOOK_SECRET` is set, the `X-GLPI-signature` header is verified (`HMAC-SHA256(body + X-GLPI-timestamp, secret)`, GLPI 11 format). When it is empty, every request is accepted and a warning is logged at startup.

### Embeddings

Embeddings are computed by the university GPU server (vLLM, OpenAI-compatible), not inside this project. Model: `BAAI/bge-multilingual-gemma2` (3584 dimensions), endpoint set by `EMBEDDING_BASE_URL`.

**The university VPN is required.** Without it, routes that embed text return `502`; everything else keeps working.

Changing `EMBEDDING_MODEL` or `embedding_instruction` (`api/app/core/config.py`) requires re-embedding the whole memory.

Models available on the university server (VPN required):

| Role | Model | Endpoint |
|---|---|---|
| Embeddings | `BAAI/bge-multilingual-gemma2` | `http://193.48.31.6:31550/v1/embeddings` |
| Reranker | `BAAI/bge-reranker-v2-gemma` | `http://193.48.31.6:31127/rerank` |
| LLM | `google/gemma3-12b-it` | `http://193.48.31.6:32297/v1/chat/completions` |
| LLM (light) | `google/gemma3-4b-it` | `http://193.48.31.6:30091/v1/chat/completions` |

### Local development

```bash
docker compose up -d postgres        # database only
cd api
uv sync
uv run alembic upgrade head          # reads the root .env
uv run uvicorn app.main:create_app --factory --reload
uv run pytest                        # requires Docker (testcontainers)
uv run ruff check . && uv run ruff format .
```

Tests run against a real pgvector container and never call the embeddings server.

To change the schema: edit `app/models/`, run `uv run alembic revision --autogenerate -m "..."`, review the generated migration, then run `uv run alembic check` (it must report no changes). Never edit a migration that has already been shared.

After changing API code, rebuild the container: `docker compose up -d --build api`.

## LDAP directory

Seven fictitious users in four groups, loaded from `ldap/bootstrap.ldif` on first boot (data plan: `doc/note-projet-complet.md` §7).

| UID | Role | Group | Password |
|---|---|---|---|
| `camille.dupont` | M2 DE student | etudiants | `camille2026` |
| `lucas.moreau` | M2 DE student | etudiants | `lucas2026` |
| `lea.petit` | L3 CS student | etudiants | `lea2026` |
| `thomas.girard` | Research lecturer | enseignants | `thomas2026` |
| `emma.lambert` | Associate professor | enseignants | `emma2026` |
| `hugo.mercier` | IT technician | techniciens | `hugo2026` |
| `chloe.renard` | Student records officer | administratifs | `chloe2026` |

Quick check:

```bash
docker exec poc-ldap sh -c 'ldapsearch -x -H ldap://localhost \
  -D "cn=admin,dc=example,dc=org" -w "$LDAP_ADMIN_PASSWORD" \
  -LLL -b "dc=example,dc=org" "(uid=camille.dupont)"'
```

## GLPI configuration

`scripts/configure-glpi.sh` runs `glpi/init/configure.php` inside the GLPI container and sets:

- the base URL (`GLPI_URL_BASE`, used in email links);
- the REST API: enabled, credential login, client restricted to `GLPI_API_IP_START`–`GLPI_API_IP_END` (default `172.16.0.0`–`172.31.255.255`, the Docker network), App-Token `GLPI_APP_TOKEN`;
- the "Univ Corse LDAP" directory: server `ldap`, base `dc=example,dc=org`, login field `uid`, bind `cn=admin,dc=example,dc=org`;
- SMTP relay, notifications, ticket auto-close and the satisfaction survey (`MAIL_*`, `SMTP_*`, `SURVEY_*`, `TICKET_AUTOCLOSE_DAYS`);
- the bot user `GLPI_BOT_USER` (default `chatbot`, Technician profile) with the User-Token `GLPI_USER_TOKEN`;
- the "new ticket" webhook to the memory API, signed with `GLPI_WEBHOOK_SECRET` (not created when the secret is empty);
- a read-only MariaDB user `GLPI_DB_RO_USER` (`SELECT` on the ticket, followup, solution, survey, category and user tables), created only when `GLPI_DB_RO_PASSWORD` is set.

An empty variable skips the step or leaves the corresponding setting unchanged. Generate the tokens with `openssl rand -hex 20` (`openssl rand -hex 32` for the webhook secret).

### GLPI and memory API wiring

| Direction | Mechanism | Detail |
|---|---|---|
| GLPI → API | Webhook `Ticket / New` | `POST http://api:8000/api/v1/ingest/ticket`, HMAC-signed. Queued by GLPI and sent by the `queuedwebhook` task (within 60 s). |
| API → GLPI (read/write) | REST API `/apirest.php` as the bot user | `User-Token` + `App-Token`. Writes go through the REST API only, so GLPI keeps its notifications, history and rights. |
| API → GLPI database (bulk read) | MariaDB user `memoire_ro` | `SELECT` only (`db:3306`). Never any SQL write. |
| Client → API → GLPI | `POST /api/v1/tickets/{id}/answer/stream` (SSE) | Reads the ticket, answers it, then posts the answer as a followup through the REST API. |

GLPI rejects any server-side URL with a port by default. `glpi/config/local_define.php` (mounted into the container) allows `http://api:8000/api/v1/`. It **replaces** the default allowlist, so external RSS feeds and calendars are blocked.

Open a REST API session:

```bash
curl -X POST http://localhost:8080/apirest.php/initSession \
  -H "Content-Type: application/json" \
  -H "Authorization: Basic $(echo -n 'glpi:glpi' | base64)" \
  -d '{"init":"true"}'
# → {"session_token": "..."}
```

### Satisfaction survey export

```bash
./scripts/export-feedback.sh > feedback.tsv
```

Writes the GLPI satisfaction survey answers to stdout as TSV (query: `glpi/sql/feedback.sql`).

## Stop and reset

```bash
docker compose down          # stop, keep data
docker compose down -v       # stop and DELETE ALL DATA (LDAP, GLPI, databases, memory)
```

After `down -v`, rerun `./scripts/configure-glpi.sh`.

## Troubleshooting

**GLPI volume permissions after volumes were recreated.** They may need to be fixed once:

```bash
for v in files config marketplace; do
  docker run --rm -v bisgambiglia-poc_glpi_$v:/var/glpi/$v alpine chown -R 33:33 /var/glpi/$v
done
docker compose restart glpi
```

**LDAP users are missing.** The LDIF is only imported into an empty directory. If the LDAP container was recreated after a first initialization, load it manually:

```bash
docker exec poc-ldap sh -c 'ldapadd -x -c -H ldap://localhost \
  -D "cn=admin,dc=example,dc=org" -w "$LDAP_ADMIN_PASSWORD" \
  -f /container/service/slapd/assets/config/bootstrap/ldif/custom/50-bootstrap.ldif'
```

**Configuring LDAP in GLPI by hand.** Without the script, use `Setup > Authentication > LDAP directories`, then enable the source in `Setup > Authentication > Configuration > Advanced`. The first name field must be `givenname` (lowercase), otherwise it stays empty.

**Embedding routes return `502`.** The university VPN is not connected, or `EMBEDDING_BASE_URL` is wrong.

## Repository layout

| Path | Content |
|---|---|
| `api/` | Memory API (FastAPI, SQLAlchemy, Alembic, tests) |
| `glpi/` | GLPI configuration script and SQL queries |
| `ldap/` | LDAP seed (`bootstrap.ldif`) |
| `scripts/` | GLPI setup, feedback export, memory seeding |
| `data/` | Sample knowledge base (`knowledge_seed.json`) |
| `doc/` | Project notes and API reference |
