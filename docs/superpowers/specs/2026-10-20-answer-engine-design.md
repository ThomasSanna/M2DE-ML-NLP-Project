# Answer engine design

Date: 2026-10-20. Status: approved in conversation, pending written-spec review.
Branch: `feat/llm-pipeline`.

## 1. Context

The memory API (`api/`) stores question/answer pairs embedded with pgvector. The
LLM pipeline described in `doc/note-projet-complet.md` is not implemented. It is
split into sub-projects, each with its own spec:

1. **Answer engine** (this spec): from a ticket thread, retrieve relevant memory,
   optionally search the web, and produce a sourced answer.
2. Later: `escalate` outcome, guardrails IN/OUT, worker consuming `glpi_event`
   (GLPI follow-up + SMTP), persistence of answer runs, feedback rating.

## 2. Goal and non-goals

**Goal.** `POST /api/v1/answer` takes a thread of messages and returns an answer
in French, its cited sources (memory entries and web pages) and a trace (memory
hits, web searches, step count, token usage).

**Non-goals.** No GLPI call, no SMTP, no guardrails, no escalation outcome, no new
table (the engine is stateless), no streaming, no retry policy, no `reuse_count`
update (that belongs to feedback).

## 3. Constraints measured on the university servers (VPN)

Endpoints and model names come from `doc/Projet M2.pdf`.

- The chat endpoints run **Hugging Face TGI 3.2.3**, not vLLM. TGI ignores the
  `model` field; `google/gemma3-4b-it` and `google/gemma-3-4b-it` both work.
- 4B (`:30091`): `max_input_tokens` 4000, `max_total_tokens` 6000 (server launch
  settings, not a model limit). 12B (`:32297`): unreachable on 2026-10-20, limits
  unknown.
- The Gemma chat template **rejects the `tool` role** and requires strict
  user/assistant alternation. Native `tool_calls` exist but cannot be replayed.
- `response_format` accepts `{"type": "json", "value": <JSON schema>}` (also
  `json_object`, `regex`), not `json_schema`. A root `oneOf` is rejected; a flat
  object schema works and gave correct search/answer decisions on the 4B. First
  call with a new grammar: about 11 s (compilation), then about 0.6 s.
- Whether TGI prefix caching is active is unverified (`/metrics` exposes no
  prefix-cache metric). The design keeps the prompt prefix stable regardless.
- Reranker (`:31127/rerank`): `{"pairs": [[q, doc], ...]}` returns
  `{"scores": [...]}`, unbounded logits (2.83 for a relevant pair, -7.58 for an
  unrelated one).
- Exa: `POST https://api.exa.ai/search`, header `x-api-key`, body `query`, `type`,
  `numResults`, `includeDomains`, `contents.highlights.maxCharacters`. Response
  `results[]` (`title`, `url`, `publishedDate`, `highlights[]`) and
  `costDollars.total`.

## 4. Approach

Custom JSON protocol under TGI grammar-constrained decoding. Each step is one
chat completion whose output is forced to match:

```json
{
  "type": "object",
  "properties": {
    "action": {"type": "string", "enum": ["search", "answer"]},
    "query": {"type": "string"},
    "answer": {"type": "string"},
    "sources": {"type": "array", "items": {"type": "string"}}
  },
  "required": ["action"]
}
```

The loop structure follows opencode (`packages/core/src/session/runner/`): a step
cap with a forced final answer, a typed per-step outcome instead of exceptions,
tool failures fed back to the model, truncated tool output, and an append-only
history so the prompt prefix stays cacheable.

## 5. Architecture

Every remote service follows the existing pattern: a `Protocol`, an
`httpx.AsyncClient` created in `lifespan`, the client stored on `app.state`,
exposed through a dependency, and a domain error.

```
POST /api/v1/answer  (X-API-Key)
  routes/answer.py: validate, call one service, return the schema
    services/answer.py: answer(...) -> AnswerResult
      1. memory.retrieve: embed last user message -> pgvector top 20
                          -> rerank -> at most 3 above threshold
      2. agent.run: JSON steps {search | answer}, Exa when needed
```

| File | Responsibility | Depends on |
|---|---|---|
| `services/llm.py` | `ChatModel` Protocol, `TGIChatClient`, `Completion`, `LLMError` | httpx |
| `services/reranker.py` | `Reranker` Protocol, `HttpRerankerClient`, `RerankerError` | httpx |
| `services/websearch.py` | `WebSearch` Protocol, `ExaClient`, `WebResult`, `WebSearchError` | httpx |
| `services/memory.py` | new `retrieve(...)`; existing `search` unchanged | `Embedder`, `Reranker` |
| `services/agent_prompt.py` | fixed system prompt, rendering of memory, thread and web results, JSON schemas, `SearchAction` / `AnswerAction` models, character caps | none |
| `services/agent.py` | the loop | `ChatModel`, `WebSearch` |
| `services/answer.py` | orchestration: retrieve, run the loop, build `AnswerResult` | the above |
| `schemas/answer.py` | request and response schemas | none |
| `api/routes/answer.py` | the route | `services/answer.py` |
| `api/deps.py` | `ChatModelDep`, `RerankerDep`, `WebSearchDep` (`WebSearch \| None`, `None` without `EXA_API_KEY`) | none |
| `main.py` | clients in `lifespan`, error handlers, router registration | none |

`agent.py` knows neither the database nor FastAPI: it receives pre-rendered memory
and thread text and can be tested alone with a fake `ChatModel`.

No new dependency: Exa is called over REST with `httpx`, not with `exa-py`.

Text the model reads (system prompt, section labels, feedback messages) is in
French, the answer language, like `embedding_instruction`. Identifiers, comments,
logs and docs stay in English.

## 6. Data flow and prompt layout

```
[system]    fixed text: role, rules (answer in French, concisely, only from the
            sources, cite refs, never invent a URL), JSON action protocol
[user]      Mémoire : [K1] Q ... R ... / [K2] ...
            (or "Aucune connaissance pertinente en mémoire.")
            Fil : Utilisateur : ... / Support : ... / Utilisateur : ...
[assistant] raw content returned at step 1          appended at each step,
[user]      Résultats web pour « ... » : [W1] ...   never modified afterwards
...
```

- The thread is rendered as text inside the first user message, not replayed as
  roles: a GLPI thread can contain two consecutive user messages, which the Gemma
  template rejects. Roles are only used for agent steps, which always alternate.
- The system prompt and the first user message never change during a run. Each
  step appends exactly one assistant message (the raw model output) and one user
  message (the step result).
- Web refs are numbered continuously across searches (`W1`-`W3`, then `W4`-`W6`).
- Character caps (constants in `agent_prompt.py`): memory answer 800 characters
  each, web excerpt 500 characters, thread last 2000 characters. With 3 memory
  entries and 2 searches the prompt stays near 2800 tokens, under the 4B limit.
  If TGI still rejects the input, the result is `LLMError` (502), never a silent
  truncation.

## 7. Agent loop (`services/agent.py`)

Inputs: chat model, web search (or none when disabled), rendered memory and
thread, `max_steps`. Output: `AgentRun(answer, cited_refs, searches, web_results,
steps, usage)`, where `web_results` maps each `W` ref to its `WebResult` and
`usage` sums prompt and completion tokens over all steps.

Each step is one `ChatModel.complete(messages, schema)` call. The raw content is
parsed with a pydantic `TypeAdapter` over `SearchAction | AnswerAction`,
discriminated on `action` (`SearchAction.query` and `AnswerAction.answer` have
`min_length=1`; `sources` defaults to `[]`; extra keys are ignored).

| Model output | Engine behavior |
|---|---|
| valid `answer` | end. Unknown refs are dropped from the citations and logged. Empty `sources` is allowed. |
| valid `search`, new query | call Exa, append `Résultats web pour « query » : [Wn] title - url - excerpt` |
| valid `search`, query already made (compared after `strip().casefold()`) | no Exa call; append `Déjà cherché, résultats ci-dessus. Réponds ou cherche autre chose.` |
| invalid JSON or failed validation, including `finish_reason == "length"` | append the short validation error and ask for a valid JSON object |
| `WebSearchError` | append `Recherche web indisponible, réponds avec la mémoire.`; log the cause |

Every case consumes one step.

**Last step** (`step == max_steps`, default 3: at most two searches, then an
answer). Before the call, append `Nombre maximal d'étapes atteint : réponds
maintenant.` to the content of the last user message (the previous step result, or
the initial message when `max_steps` is 1). That message has not been sent yet, so
roles keep alternating and the cached prefix is unchanged. The grammar is also
narrowed to `"enum": ["answer"]`, which guarantees an answer. This is opencode's
`toolChoice: "none"`, but on TGI the grammar is a decoding constraint rather than
prompt text, so the cached prefix is untouched. If the last output is still
invalid (for example cut by `max_tokens`), raise `LLMError`.

**Search disabled** (no `EXA_API_KEY`): the grammar is narrowed to `answer` from
step 1. The system prompt stays the same.

Generation uses `temperature: 0`. No network retry inside the loop.

## 8. Remote clients

**`TGIChatClient.complete(messages, schema) -> Completion(content, finish_reason,
prompt_tokens, completion_tokens)`.** `POST {LLM_BASE_URL}/chat/completions`
(the base URL includes `/v1`, like `EMBEDDING_BASE_URL`) with `model`, `messages`,
`max_tokens`, `temperature: 0`, `response_format: {"type": "json", "value":
schema}`. Transport errors, non-2xx responses (including TGI's 422 on context
overflow) and missing `choices` or `content` raise `LLMError`. The client does not
parse the business JSON.

**`HttpRerankerClient.rerank(query, documents) -> list[float]`.**
`POST {RERANKER_BASE_URL}/rerank` with `{"pairs": [[query, doc], ...]}`. Returns
raw logits. A score count different from the document count, a transport error or
a non-2xx response raises `RerankerError`.

**`ExaClient.search(query) -> list[WebResult(title, url, published, excerpt)]`.**
`POST https://api.exa.ai/search` (constant, not a setting), header `x-api-key`,
body `{query, type: "auto", numResults: EXA_NUM_RESULTS, contents: {highlights:
{maxCharacters: 500}}}`, plus `includeDomains` only when `EXA_INCLUDE_DOMAINS` is
set. A missing title falls back to the URL; `excerpt` joins the highlights,
capped at 500 characters. `costDollars.total` is logged. 401, 429, other non-2xx,
transport and format errors raise `WebSearchError`.

## 9. Retrieval (`memory.retrieve`)

`retrieve(session, embedder, reranker, text, candidates, top_k, threshold) ->
list[tuple[Knowledge, float]]`:

1. `search(session, embedder, text, candidates)` (existing function, unchanged, so
   the HNSW ordering invariant holds). `text` is the last user message.
2. Rerank pairs `(text, question + "\n" + answer)`. The reranker is a cross-encoder
   built for query/passage pairs and the answer often carries the problem's
   vocabulary. The embedding still covers the question only.
3. Score = `sigmoid(logit)`. Keep entries with score `>= threshold`, sorted by
   score descending, at most `top_k`.
4. `RerankerError` propagates (502). No fallback to cosine similarity, which would
   hide an outage.

## 10. API

`POST /api/v1/answer`, guarded by `require_api_key`. Synchronous; expect about
1 to 15 s depending on searches. Status codes: `200`, `401`, `422`, `502`.

Request (`AnswerRequest`):

```json
{"messages": [
  {"role": "user", "content": "Le wifi eduroam ne marche pas sur mon Mac"},
  {"role": "assistant", "content": "Quel message d'erreur voyez-vous ?"},
  {"role": "user", "content": "Il demande le mot de passe en boucle"}
]}
```

- `ThreadMessage(role: Literal["user", "assistant"], content: str, min_length=1)`.
  `assistant` means the support side (technician or bot).
- `messages`: 1 to 50 items; a validator returns 422 when the last one is not
  from `user`.

Response (`AnswerResult`):

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

- `sources`: only refs that were cited and exist, as `KnowledgeCitation` or
  `WebCitation`, discriminated on `type`. The names avoid a clash with the
  existing `KnowledgeSource` enum.
- `memory`: every retrieved entry with its score, cited or not.
- `kind` is a `Literal["answer"]`. Pydantic rejects a one-member discriminated
  union, so the route returns `AnswerResult` for now. Adding escalation turns the
  return type into `Annotated[AnswerResult | EscalationResult,
  Field(discriminator="kind")]` without changing the JSON clients already see.
- The response model is declared by the return annotation.

## 11. Settings

Each one goes in `app/core/config.py`, `.env.example`, the `x-api-env` anchor of
`docker-compose.yml` and the configuration table of `doc/api_doc.md`.

| Variable | Default | Note |
|---|---|---|
| `LLM_BASE_URL` | required | `AnyHttpUrl`, includes `/v1` |
| `LLM_MODEL` | `google/gemma3-12b-it` | sent to TGI, which ignores it |
| `LLM_MAX_TOKENS` | `512` | |
| `LLM_TIMEOUT_SECONDS` | `60.0` | cold grammar compilation |
| `RERANKER_BASE_URL` | required | `AnyHttpUrl`, without `/v1` |
| `RERANKER_TIMEOUT_SECONDS` | `30.0` | |
| `RETRIEVAL_CANDIDATES` | `20` | |
| `RETRIEVAL_TOP_K` | `3` | |
| `RERANK_THRESHOLD` | `0.5` | on `sigmoid(score)`, to calibrate on real data |
| `EXA_API_KEY` | `None` | `SecretStr`, in `_empty_as_none`; absent disables web search |
| `EXA_INCLUDE_DOMAINS` | empty | comma-separated |
| `EXA_NUM_RESULTS` | `3` | |
| `EXA_TIMEOUT_SECONDS` | `15.0` | |
| `AGENT_MAX_STEPS` | `3` | |

In development, while the 12B is down, set `LLM_BASE_URL` to the 4B endpoint.

## 12. Errors and logging

Routes catch nothing; `main.py` maps domain errors and logs their cause.

| Error | Raised by | HTTP |
|---|---|---|
| `EmbeddingError` (existing) | retrieval | 502 "Embeddings server unavailable" |
| `RerankerError` | retrieval | 502 "Reranker unavailable" |
| `LLMError` | LLM client, invalid last step | 502 "Language model unavailable" |
| `WebSearchError` | Exa client | none: caught in `agent.py` and fed back to the model |

`WebSearchError` is the only domain error caught, and only inside the service.

Logging: at `INFO`, one line per step (number, action, query, tokens) and the Exa
cost; thread and answer contents only at `DEBUG`, since tickets can contain
personal data.

## 13. Testing

Synchronous tests, fakes only, never the real servers.

- **Clients** (`httpx.MockTransport`, like `test_embeddings.py`):
  - LLM: `response_format` and `temperature: 0` are sent; non-2xx or missing
    content raises `LLMError`.
  - Reranker: a score count mismatch raises `RerankerError`.
  - Exa: `includeDomains` is sent only when set; a missing title falls back to the
    URL; 401 and 429 raise `WebSearchError`.
- **Loop** (`test_agent.py`), with a `ScriptedChatModel` returning predefined
  outputs and recording the messages and schemas it receives, and a
  `FakeWebSearch`:
  - direct answer;
  - search then answer;
  - append-only prefix: step N messages are a prefix of step N+1 messages;
  - duplicate query: no Exa call;
  - invalid JSON fed back, then a valid answer;
  - last step schema has `enum: ["answer"]`;
  - unknown ref dropped;
  - `WebSearchError` fed back;
  - search disabled: answer-only schema from step 1;
  - invalid last step raises `LLMError`.
- **Retrieval** (`test_memory.py`, real database, `FakeEmbedder`, and a
  `FakeReranker` returning fixed scores per document): threshold, `top_k` cap,
  ordering by score, inactive entries excluded.
- **Route** (`test_answer.py`): 200 with the full shape; 401 without key; 422 on
  empty messages and on a last `assistant` message; 502 for embedder, reranker and
  LLM failures; 200 when Exa fails.
- **`conftest.py`**: `client` overrides `get_chat_model`, `get_reranker` and
  `get_web_search` with fakes; `settings` gets `llm_base_url` and
  `reranker_base_url`. No new table, so the `TRUNCATE` statement is unchanged.

**Manual check (VPN), not automated:** `curl` on 2 or 3 cases (answer from memory,
web search, no source) against the 4B, and against the 12B once reachable.

## 14. Follow-ups

- Probe the 12B (`/info`: model id, token limits) when it comes back, and relax
  the character caps if its window is larger.
- Calibrate `RERANK_THRESHOLD` on the loaded knowledge base.
- Next specs: escalation, guardrails IN/OUT, `glpi_event` worker, answer-run
  persistence, feedback rating.
