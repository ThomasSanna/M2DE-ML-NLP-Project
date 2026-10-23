# AGENTS.md

Rules for AI agents in this repo. Every line here exists because getting it wrong
breaks something specific. User-facing docs live in `README.md` and `doc/api_doc.md`.

PoC of a GLPI support chatbot. The part under active development is the API in `api/`:
the memory and the answer engine (`POST /api/v1/answer`). FastAPI 0.147 / Starlette 1.9,
SQLAlchemy 2.1 async + asyncpg, Alembic 1.20, pgvector 0.8 (PG 17), Python 3.12, `uv`.
Embeddings, reranker and LLM (TGI) are remote servers on the university network (VPN); web
search is Exa. Guardrails (input and output filtering) are **not implemented**; do not
build them unless asked.

## Commands

Run from `api/` (Settings reads `../.env`; `alembic.ini` lives here). Shell scripts in
`scripts/*.sh` are bash: on Windows, run them from Git Bash.

```bash
uv sync
uv run pytest -x -q                                         # full suite, Docker required
uv run pytest tests/test_memory.py::test_search_ignores_inactive_knowledge -x   # one test
uv run ruff format . && uv run ruff check .
uv run alembic upgrade head
uv run alembic revision --autogenerate --rev-id 0002 -m "add foo"   # next sequential id
uv run alembic check                                        # must print "No new upgrade operations"
uv run uvicorn app.main:create_app --factory --reload
```

From the repo root: `docker compose up -d postgres` (DB only), `docker compose up -d --build api`
(after any change under `api/`: the image does not mount the source).

## Definition of done

A change under `api/` is done only when all of these pass, in this order:
1. `uv run ruff format . && uv run ruff check .` (zero findings, no new `noqa`).
2. `uv run pytest -x -q`, with a new or updated test covering the behavior change.
3. `uv run alembic check` if anything under `app/models/` changed.
4. Docs updated if routes, schemas, status codes or settings changed (see Docs).

If Docker or the VPN is unavailable, say which step was not run. Never claim it passed.

## Boundaries

**Always**
- Read settings through `SettingsDep`, the DB through `SessionDep`, and remote clients
  through `EmbedderDep`, `RerankerDep`, `ChatModelDep`, `WebSearchDep` (`app/api/deps.py`).
  Never call `app.core.config.get_settings()` or build a client inside a route or service:
  it bypasses the test `Settings` and the fakes, and opens an HTTP client outside the
  lifespan.
- Put business logic in `app/services/`; routes validate, call one service, map to a schema.
  `routes/ingest.py` is the only route that writes to the DB itself.
- Give new settings a home in four places: `app/core/config.py`, `.env.example` (placeholder),
  the `x-api-env` anchor in `docker-compose.yml` (the container gets **only** those variables,
  not the `.env` file), and the configuration table in `doc/api_doc.md`. Secrets are
  `SecretStr`; optional ones are `X | None = None` and listed in `_empty_as_none`.
- Stage files by explicit path. The working tree often holds unrelated uncommitted work.

**Ask first**
- Adding a dependency (`uv add`, which updates `uv.lock`; the Docker build uses `--locked`).
- Anything in `glpi/`, `ldap/`, `scripts/configure-glpi.sh`: other group members own GLPI and
  LDAP. Configuring the webhook on the GLPI side is out of scope.
- Running `scripts/load_knowledge.py`: it is not idempotent and duplicates every row on a
  second run. It also needs the VPN.
- Any change to `EMBEDDING_MODEL`, `EMBEDDING_DIM` or `embedding_instruction`: all stored
  vectors become incompatible and the whole `knowledge` table must be re-embedded.

**Never**
- `docker compose down -v`, `docker volume rm`, `DROP`/`TRUNCATE` on the dev database: it wipes
  LDAP, GLPI, MariaDB and the memory with no backup.
- Read, print or copy values from `.env`, or put any real credential in code, tests, docs or
  commits.
- Edit an applied migration (`0001` included). Schema changes go in a new revision.
- Weaken auth: `require_api_key` on `/api/v1/memory/*` and `/api/v1/answer` (constant-time
  compare), the GLPI HMAC
  on `/api/v1/ingest/*`. Do not add a timestamp window to the HMAC check: GLPI queues webhooks
  and replays them later with their original headers.
- Call the real model servers or Exa from tests, or add a local model container / torch.
- Add AI attribution (`Co-Authored-By: Claude`, "Generated with Claude Code") to commits or PRs.

## Invariants that are easy to break

**Vectors.** `knowledge.embedding` is `halfvec(3584)` with an HNSW `halfvec_cosine_ops` index.
pgvector caps HNSW at 2000 dims for `vector`, so never switch the column to `Vector`. Only the
question is embedded, never the answer. The same `<instruct>…\n<query>…` prefix is applied to
stored questions and to search queries; keep both sides symmetric.

**Search** (`services/memory.py`). Keep `.order_by(distance).limit(k)` on the raw
`cosine_distance` expression: ordering by `similarity DESC` or anything else makes Postgres skip
the HNSW index. Filter on `status == ACTIVE`; `GET /knowledge/{id}` deliberately ignores status.

**Webhook** (`routes/ingest.py`). Verify the signature on the raw `await request.body()` bytes
before parsing. Never let FastAPI parse the body into a model first: re-serialized JSON does
not match GLPI's HMAC. The handler only stores the event and returns `202`; no GLPI call, no
embedding there (`services/glpi_worker.py` consumes `glpi_event` rows; `/answer` does not).
The worker commits `processing` before answering and never replays a `processing` event:
a public followup is emailed, so a missed answer is better than a duplicate. The bot's own
followups fire the followup webhook too: `_awaits_bot` must keep skipping them, or the bot
answers itself in a loop.

**Remote clients.** Each client turns every transport, format or dimension failure into its
domain error (`EmbeddingError`, `RerankerError`, `LLMError`, `WebSearchError`); `main.py`
maps the first three to `502` with a generic message and logs the cause. Do not return the
cause to the client, and do not catch these errors in routes. `WebSearchError` is the
exception: the agent feeds it back to the model and answers from the memory. A new remote
client follows the same shape: a `Protocol`, an `httpx.AsyncClient` created in `lifespan`,
stored on `app.state`, exposed by a dependency.

**Answer engine** (`services/answer.py`, `services/agent.py`). Commit the session right after
retrieval: the agent loop takes seconds and must not hold a pooled connection. Agent
messages are append-only so the server reuses the cached prompt prefix; the last step adds
the notice to the unsent message and narrows the grammar to `answer`. Only refs shown to
the model (`K<n>`, `W<n>`) may become citations. The search query is logged at INFO and sent
to Exa; the thread itself is logged at DEBUG only.

**Enums.** Use `str_enum()` (VARCHAR + CHECK), never a native Postgres `ENUM`. Autogenerate
does **not** detect CHECK changes: adding or removing a member requires a hand-written
`op.drop_constraint` / `op.create_check_constraint` on `ck_<table>_<enumclassname>`, and
`alembic check` will still report nothing. Renaming the enum class renames the constraint.

**Migrations.** Revision IDs are sequential (`--rev-id 0002`, `0003`, …), not Alembic's
random hex. The template always imports `pgvector.sqlalchemy`; delete it if unused or
`ruff check` fails. Read every autogenerated migration before applying it: autogenerate
misses CHECK constraints, renames (drop + add = data loss) and server defaults.

**Sessions.** `expire_on_commit=False`. Services own `commit()`; routes never commit
(except `ingest`). Return schemas with `KnowledgeRead.model_validate(obj)` and declare the
response model through the return annotation, not `response_model=`.

## Code conventions

Match the neighbors; ruff config in `api/pyproject.toml` is authoritative (line length 100).

- Every function is fully typed. A `# type: ignore[code]` carries a reason comment.
- Errors: `raise HTTPException(status.HTTP_404_NOT_FOUND, "Connaissance introuvable")`.
  Use Starlette 1.x names (`HTTP_422_UNPROCESSABLE_CONTENT`, not `_ENTITY`).
- English: identifiers, file names, commit messages, PRs, and docs (`README.md`, `doc/api_doc.md`,
  `AGENTS.md`).
- Comments explain a non-obvious why (see `core/security.py`, `models/knowledge.py`), never
  restate the code.

## Tests (`api/tests/`)

- The DB is a real throwaway `pgvector/pgvector:0.8.0-pg17` container (session-scoped)
  migrated with the real Alembic revisions. No SQLite, no DB mocks.
- Use the fixtures: `client` (app + `FakeEmbedder`, `FakeReranker`, a `ScriptedChatModel`
  that answers, no web search), `auth` (API key header), `settings`. Override
  `get_chat_model` / `get_web_search` with `ScriptedChatModel` / `FakeWebSearch` to script a
  run.
  Tests build `Settings(_env_file=None, ...)` and must never read the real `.env`.
- Tests are **synchronous** (`TestClient`). Wrap async code in `asyncio.run` like `run_sql`
  and `_embed` do. Do not add pytest-asyncio or `@pytest.mark.anyio` tests.
- `client` truncates `knowledge, feedback, glpi_event ... RESTART IDENTITY` after each test,
  and tests rely on it (`event_id == 1`). A new table must be added to that statement.
- `FakeEmbedder` is a hashed bag of words: similarity tests need shared words between query
  and stored question. Synonyms ("wifi" vs "réseau sans fil") will not match.
- To simulate the embeddings server, use `httpx.MockTransport` (`test_embeddings.py`). To make
  embedding fail in a route test, override `get_embedder` with a failing class
  (`test_embedding_failure_returns_502`).
- New route: test the success path, `401` without key, `422` on invalid input, and `502` for
  each remote model it calls.

## Docs

When a route, request/response schema, status code, table or setting changes, update in the
same commit: `doc/api_doc.md` (endpoint section, schema, configuration table), the route table
in `README.md` § "Memory API", and `.env.example`. The model table in `README.md` is the
source of truth for model names; `doc/note_projet.md` is raw notes and its model names are
outdated.

## Git

- Work on a feature branch; `main` changes only through a PR. Never push or force-push `main`.
- Commit after each completed task, Conventional Commits in English:
  `feat(api): add feedback route`, `fix(api): ...`, `test(api): ...`, `docs: ...`, `chore: ...`.
- One logical change per commit; do not mix a migration with unrelated refactors.
