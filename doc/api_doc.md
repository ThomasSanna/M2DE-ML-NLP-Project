# API mémoire — architecture et choix techniques

> Branche `feat/api-memoire` — 19/10/2026. Code dans [`api/`](../api/).
> Ce document explique **ce qui a été construit, comment c'est organisé et pourquoi**.
> Pour juste lancer et tester : voir le [README](../README.md) (section « API mémoire »).

---

## 1. En bref

L'API est la **mémoire** du chatbot : elle stocke des paires question/réponse avec l'embedding de
la question, et sait retrouver les plus proches d'une nouvelle question. Elle reçoit aussi les
webhooks GLPI et les met de côté pour la future pipeline LLM.

Ce qui existe aujourd'hui :

- un serveur **FastAPI** (Python 3.12), dockerisé à côté de GLPI et LDAP ;
- une base **PostgreSQL 17 + pgvector** qui contient à la fois les vecteurs et les données relationnelles ;
- un client vers le **serveur d'embeddings de l'université** (aucun modèle ne tourne chez nous) ;
- des routes pour **ajouter / lire / rechercher** des connaissances, et une route qui **reçoit les webhooks GLPI** ;
- des **migrations** de schéma (Alembic) et **18 tests** automatiques.

Ce qui n'existe **pas encore** (volontairement) : la pipeline LLM elle-même — garde-fous, appel à
Gemma, websearch, réponse dans GLPI, notation du feedback, pondération. L'API en pose les fondations
(tables `feedback` et `glpi_event`, point d'accroche dans `search`), voir §10.

---

## 2. Vue d'ensemble

```
                         VPN université
                    ┌──────────────────────────────────────────┐
                    │  Serveur GPU (vLLM)  193.48.31.6          │
                    │   :31550  bge-multilingual-gemma2 (embed) │
                    │   :32297  gemma3-12b-it   (plus tard)     │
                    │   :30091  gemma3-4b-it    (plus tard)     │
                    │   :31127  reranker        (plus tard)     │
                    └───────────────▲──────────────────────────┘
                                    │ POST /v1/embeddings
 ┌─────────── docker compose (réseau poc_net) ──────────────────────────────┐
 │                                  │                                        │
 │  GLPI ── webhook ──►  ┌──────────┴─────────┐        ┌──────────────────┐   │
 │  (poc-glpi)          │  api  (poc-api)     │  SQL   │ postgres          │   │
 │                      │  FastAPI :8000      ├───────►│ (poc-postgres)    │   │
 │  client / Swagger ──►│                     │        │ PG 17 + pgvector  │   │
 │  X-API-Key           └──────────▲──────────┘        └────────▲─────────┘   │
 │                                 │ démarre après               │            │
 │                      ┌──────────┴──────────┐                  │            │
 │                      │ migrate (one-shot)  ├──────────────────┘            │
 │                      │ alembic upgrade head│                               │
 │                      └─────────────────────┘                               │
 │  ldap, phpldapadmin, db (MariaDB GLPI) : inchangés                         │
 └────────────────────────────────────────────────────────────────────────────┘
```

Trois nouveaux services Compose :

| Service | Image | Rôle |
|---|---|---|
| `postgres` | `pgvector/pgvector:0.8.0-pg17` | la base de la mémoire. Port exposé **uniquement sur 127.0.0.1** (dev local) |
| `migrate` | `bisgambiglia-poc/api` | lance `alembic upgrade head` puis s'arrête |
| `api` | `bisgambiglia-poc/api` (même image) | le serveur FastAPI, port `8000` |

**Ordre de démarrage** : `postgres` healthy → `migrate` termine avec succès → `api` démarre. L'API ne
démarre donc jamais sur un schéma pas à jour. Si une migration échoue, l'API ne démarre pas (c'est
voulu : mieux vaut un arrêt visible qu'une API qui plante à la première requête).

---

## 3. Les choix et leurs raisons

| Choix | Pourquoi | Alternative écartée |
|---|---|---|
| **PostgreSQL + pgvector** | Une seule base pour les vecteurs **et** le relationnel (feedback, statuts, file d'événements) : jointures, transactions, contraintes. La pondération combinera similarité + feedback + fraîcheur dans **une même requête SQL**. | Qdrant / Chroma : il aurait fallu une 2e base pour le relationnel et synchroniser les deux. |
| **Embeddings distants** | L'université sert déjà les modèles sur GPU. L'image reste légère (~300 Mo, pas de torch). | Modèle local : image de plusieurs Go, CPU lent. |
| **`halfvec(3584)`** au lieu de `vector` | Le modèle sort 3584 dimensions ; l'index HNSW de pgvector est limité à **2000** dims en `vector`, **4000** en `halfvec` (float16). Perte de précision négligeable pour du cosinus. | `vector(3584)` sans index : recherche exhaustive, OK à petite échelle mais ne tient pas. |
| **Webhook → table `glpi_event`** (boîte de réception) | Le webhook répond `202` immédiatement après l'avoir enregistré. La pipeline (lente : LLM, websearch) traitera ensuite, pourra **réessayer** en cas d'échec, et rien n'est perdu si elle plante. | Traiter dans la requête du webhook : GLPI attendrait des dizaines de secondes, et un crash = ticket perdu. |
| **Async** (FastAPI + SQLAlchemy async + httpx async) | La pipeline passera son temps à attendre le réseau (LLM, embeddings, web) : l'async permet de servir d'autres requêtes pendant ce temps. | — |
| **Alembic** (migrations) | Le schéma va évoluer (pondération, etc.) : chaque changement est un fichier versionné, rejouable sur n'importe quelle machine, sans `DROP` manuel. | `create_all()` au démarrage : ne gère pas les évolutions. |
| **Une image, deux services** (`migrate` + `api`) | Les migrations tournent une fois, avant l'API, même si on lance plusieurs instances de l'API. | Migrer au démarrage de l'API : course entre instances. |

---

## 4. Organisation du code

```
api/
├── app/
│   ├── main.py              ← création de l'app (create_app) + démarrage/arrêt (lifespan)
│   ├── core/
│   │   ├── config.py        ← toute la configuration (variables d'environnement)
│   │   ├── logging.py
│   │   └── security.py      ← vérification de la signature des webhooks GLPI
│   ├── db/
│   │   ├── base.py          ← classe de base des modèles, conventions de nommage
│   │   └── session.py       ← connexion à Postgres (engine, sessions)
│   ├── models/              ← les TABLES (SQLAlchemy)
│   │   ├── knowledge.py     ← knowledge (+ dimension des embeddings)
│   │   ├── feedback.py
│   │   └── glpi_event.py
│   ├── schemas/             ← les FORMATS JSON d'entrée/sortie (Pydantic)
│   │   └── knowledge.py
│   ├── services/            ← la LOGIQUE MÉTIER, indépendante de HTTP
│   │   ├── embeddings.py    ← client du serveur d'embeddings
│   │   └── memory.py        ← ajouter / lire / rechercher dans la mémoire
│   └── api/
│       ├── deps.py          ← dépendances injectées (session BDD, embedder, clé API)
│       └── routes/          ← les ENDPOINTS HTTP (fins : ils délèguent aux services)
│           ├── health.py
│           ├── ingest.py
│           └── memory.py
├── migrations/              ← Alembic : env.py + versions/0001_initial_schema.py
├── tests/                   ← pytest
├── Dockerfile, alembic.ini, pyproject.toml, uv.lock
```

### Les couches, et la règle à respecter

```
routes (HTTP)  →  services (métier)  →  models (BDD)
     ↑ schemas (validation JSON)          ↑ embeddings (HTTP sortant)
```

- **Une route** valide l'entrée (via un schéma), appelle un service, formate la sortie. Pas de SQL dedans.
- **Un service** contient la logique (« embedder la question puis l'insérer »). Il ne connaît pas HTTP :
  la **pipeline LLM pourra appeler `services/memory.py` directement**, sans passer par l'API.
- **Les modèles** décrivent les tables ; **les schémas** décrivent le JSON. Ce sont deux choses
  différentes exprès : on peut changer la base sans changer le contrat de l'API, et inversement.

### Exemple : le trajet d'une recherche

`POST /api/v1/memory/search {"query": "eduroam ne marche pas", "top_k": 3}`

1. `deps.require_api_key` vérifie l'en-tête `X-API-Key` → sinon `401`.
2. FastAPI valide le corps avec `SearchRequest` (`top_k` entre 1 et 50) → sinon `422`.
3. La route `search_knowledge` récupère une **session BDD** et l'**embedder** (injectés par `deps.py`).
4. `services/memory.search` :
   - envoie la requête au serveur d'embeddings → vecteur de 3584 nombres ;
   - exécute en SQL : *« les connaissances `active`, triées par distance cosinus `<=>` à ce vecteur, limit 3 »*
     (l'index HNSW rend ça rapide) ;
   - renvoie chaque connaissance avec `similarity = 1 − distance`.
5. La route transforme le résultat en `list[SearchHit]` (JSON).
6. Si le serveur d'embeddings est injoignable (pas de VPN…), `EmbeddingError` est convertie en **`502`**
   par un gestionnaire global dans `main.py`.

### Comment l'app démarre (`main.py`)

`create_app()` construit l'application ; le **lifespan** s'exécute au démarrage :
- crée le pool de connexions Postgres ;
- ouvre **un** client HTTP réutilisé pour tous les appels d'embeddings (connexions gardées ouvertes) ;
- les range dans `app.state`, où `deps.py` les récupère.

Pourquoi une fabrique `create_app(settings)` plutôt qu'une variable globale `app` : les tests créent
une app avec leur propre configuration (base de test) et un faux embedder, sans toucher au code.

---

## 5. La base de données

### Tables (migration `0001`)

**`knowledge`** — une connaissance de la mémoire

| Colonne | Type | Rôle |
|---|---|---|
| `id` | UUID | identifiant |
| `question`, `answer` | texte | la paire Q/R |
| `category` | varchar(64) | ex. « réseau », « messagerie » |
| `source` | `glpi_history` / `llm` / `technician` | d'où vient la réponse |
| `status` | `active` / `to_review` / `archived` | seules les `active` sortent dans la recherche ; `to_review` = mal notée, à corriger par un technicien |
| `glpi_ticket_id` | int | ticket d'origine |
| `reuse_count` | int | nb de fois réutilisée (pour la pondération) |
| `embedding` | `halfvec(3584)` | embedding **de la question** (c'est elle qu'on compare aux nouvelles demandes) |
| `created_at`, `updated_at` | timestamptz | fraîcheur |

Index : **HNSW cosinus** sur `embedding` (recherche approchée rapide), + `status`, `glpi_ticket_id`.

**`feedback`** — historique brut des retours sur une connaissance

`knowledge_id` (supprimé avec la connaissance), `source` (`user` / `technician` / `implicit`),
`score` **entre 0 et 1** (contrainte en base), `comment`, `glpi_ticket_id`, `created_at`.
On stocke **chaque** retour plutôt qu'une moyenne : la formule de pondération pourra changer
et être recalculée sur tout l'historique.

**`glpi_event`** — boîte de réception des webhooks GLPI

`payload` (le JSON reçu, en JSONB), `event` (ex. `new`), `status`
(`received` → `processing` → `done` / `failed`), `attempts`, `last_error`, `received_at`, `processed_at`.

### Détails à connaître

- Les « enums » sont stockés en **texte + contrainte CHECK**, pas en type ENUM Postgres : ajouter
  une valeur = une migration simple, alors qu'un ENUM Postgres est pénible à modifier.
- Les noms de contraintes et d'index suivent une **convention fixe** (`db/base.py`) : sans ça, Alembic
  génère des noms aléatoires et les migrations deviennent ingérables.
- L'extension `vector` est créée par la migration elle-même.

### Modifier le schéma (procédure)

```bash
cd api
# 1. modifier app/models/…
uv run alembic revision --autogenerate -m "ajoute colonne x"
# 2. RELIRE le fichier créé dans migrations/versions/ (l'autogénération se trompe parfois)
uv run alembic upgrade head
uv run alembic check          # doit afficher "No new upgrade operations detected."
```

> La migration `0001` a été réécrite une fois (passage à `halfvec(3584)`) parce qu'elle n'existait
> que sur ma machine. **À partir de maintenant qu'elle est sur une branche partagée, on ne la modifie
> plus : toute évolution = une nouvelle migration.**

---

## 6. Les embeddings

- **Modèle** : `BAAI/bge-multilingual-gemma2`, servi par le vLLM de l'université
  (`http://193.48.31.6:31550/v1`, **VPN obligatoire**). Vecteurs de **3584** dimensions, déjà normalisés.
- **Client** : `services/embeddings.py` → `POST {EMBEDDING_BASE_URL}/embeddings` au format OpenAI.
  Plusieurs textes peuvent partir dans un seul appel. Temps mesuré : ~0,3 à 1,5 s par appel.
- **Contrôles** : si le serveur est injoignable, répond une erreur, ou renvoie une dimension
  différente de 3584 (mauvais modèle configuré), on lève `EmbeddingError` → l'API répond `502`.
  On ne stocke **jamais** un vecteur incohérent.
- **Instruction préfixée** : ce modèle attend un format `<instruct>{tâche}\n<query>{texte}`.
  L'instruction (en français, dans `core/config.py`) est appliquée **aux questions stockées et aux
  requêtes** : on compare question contre question. Mesuré sur 6 questions d'assistance :

  | Variante | Bonne réponse en tête | Écart moyen avec la 2e |
  |---|---|---|
  | sans instruction | 6/6 | 0,138 |
  | instruction sur la requête seulement | 6/6 | 0,193 |
  | **instruction des deux côtés (fr)** ← retenu | 6/6 | **0,245** |
  | instruction des deux côtés (en) | 6/6 | 0,211 |

- **Abstraction `Embedder`** : les routes et services dépendent d'une interface (`embed(texts)`),
  pas du client HTTP. Les tests branchent un faux embedder ; on pourrait changer de fournisseur
  sans toucher au reste.

> ⚠️ **Changer de modèle d'embeddings ou d'instruction rend tous les vecteurs stockés incompatibles**
> (ils ne vivent plus dans le même espace). Il faudra alors ré-embedder toute la mémoire
> (et, si la dimension change, une migration de la colonne).

---

## 7. Sécurité

| Où | Mécanisme |
|---|---|
| Routes `/api/v1/memory/*` | En-tête **`X-API-Key`** comparé à `API_KEY` (comparaison à temps constant). Absent ou faux → `401`. |
| Webhook `/api/v1/ingest/ticket` | Si `GLPI_WEBHOOK_SECRET` est défini : vérification de **`X-GLPI-signature`** = `HMAC-SHA256(corps + X-GLPI-timestamp, secret)`, le format de GLPI 11 (vérifié dans le code source de GLPI). Faux → `401`. S'il est vide : tout est accepté et un avertissement est loggé au démarrage. |
| Secrets | Tous en variables d'environnement (`.env`, ignoré par git). Typés `SecretStr` : ils n'apparaissent pas dans les logs. |
| Conteneur | Utilisateur non-root, pas d'outils de build dans l'image finale. `/docs` désactivé si `ENVIRONMENT=prod`. |
| Postgres | Port publié uniquement sur `127.0.0.1`. |

---

## 8. Configuration

Tout passe par `app/core/config.py` (lit l'environnement et, en local, le `.env` racine).

| Variable | Défaut | Rôle |
|---|---|---|
| `POSTGRES_USER` / `_PASSWORD` / `_DB` | `memoire` / — / `memoire` | base de la mémoire |
| `POSTGRES_HOST` / `_PORT` | `localhost` / `5432` | forcé à `postgres:5432` dans Docker |
| `API_KEY` | **obligatoire** | clé des routes mémoire |
| `GLPI_WEBHOOK_SECRET` | vide | secret partagé avec le webhook GLPI |
| `EMBEDDING_BASE_URL` | **obligatoire** | `http://193.48.31.6:31550/v1` |
| `EMBEDDING_MODEL` | `BAAI/bge-multilingual-gemma2` | |
| `EMBEDDING_API_KEY` | vide | si le serveur en exige une un jour |
| `API_PORT` | `8000` | port de l'API sur la machine |
| `ENVIRONMENT`, `LOG_LEVEL` | `dev`, `INFO` | |

Une variable obligatoire manquante → l'API refuse de démarrer avec un message clair (plutôt que
d'échouer plus tard).

---

## 9. Tests

```bash
cd api && uv run pytest -v
```

- Les tests démarrent **leur propre Postgres+pgvector jetable** dans Docker (testcontainers), y
  appliquent les **vraies migrations**, puis vident les tables entre chaque test. Ta base locale
  n'est jamais touchée.
- Les embeddings sont remplacés par un **faux embedder** déterministe (sac de mots haché) : pas
  besoin du VPN, et la recherche reste testable (une question qui partage des mots remonte en tête).
- Le client d'embeddings est testé à part avec un faux serveur HTTP (format de la requête,
  instruction, ordre des résultats, erreurs, mauvaise dimension).

| Fichier | Couvre |
|---|---|
| `test_health.py` | `/health/live`, `/health/ready` |
| `test_memory.py` | clé API, ajout/lecture, 404, classement de la recherche, exclusion des non-`active`, validation `top_k`, `502` si embeddings HS |
| `test_ingest.py` | signature valide/invalide/absente, corps modifié, JSON non-objet |
| `test_embeddings.py` | client HTTP du serveur d'embeddings |

Qualité du code : `uv run ruff check . && uv run ruff format .`

---

## 10. Ce qu'il reste à faire, et où le brancher

| À faire | Où |
|---|---|
| **Worker de la pipeline** : lire les `glpi_event` en `received`, les passer en `processing`, faire le travail, finir en `done` / `failed` (+ `attempts`, `last_error`) | nouveau module (ex. `app/pipeline/`), qui appelle `services/memory.py` |
| Lire le ticket / poster la réponse dans GLPI | client de l'**API REST GLPI** (App Token + User Token, cf. `Projet M2.pdf`) |
| Garde-fous IN/OUT | client LLM vers **Gemma 3 4B** en attendant **ShieldStral 3B** — à écrire derrière une interface, comme `Embedder`, pour basculer sans toucher à la pipeline |
| Reranker | client `POST :31127/rerank` à appliquer sur le top-k de `search` |
| Génération de la réponse | client chat **Gemma 3 12B** (`/v1/chat/completions`) |
| **Pondération** (le cœur du projet) | dans `services/memory.search` : combiner la similarité avec les `feedback`, `reuse_count`, la fraîcheur (`created_at`)… en SQL |
| Routes de feedback | nouvelle route qui insère dans `feedback` ; passage en `to_review` si mal noté |
| Remplir la mémoire | script d'import du jeu synthétique / de l'historique GLPI via `services/memory.add_knowledge` (ajouter un ajout par lot pour embedder plusieurs questions en un appel) |

---

## 11. À retenir / pièges

- **VPN obligatoire** pour tout ce qui embedde (ajout, recherche). Sans VPN : `502`, le reste marche.
  Docker Desktop passe par le VPN de la machine hôte, rien à configurer.
- **Changer de modèle d'embeddings ou d'instruction = ré-embedder toute la mémoire.**
- **Ne plus modifier la migration `0001`** : nouvelle migration pour tout changement.
- Après une modification du code : `docker compose up -d --build api` (l'image n'est pas montée en live).
  Pour développer avec rechargement automatique : lancer l'API hors Docker (`uv run uvicorn app.main:create_app --factory --reload`).
- La `similarity` renvoyée est la **similarité cosinus brute** (dans [−1, 1], 1 = identique) ; elle
  n'intègre pas encore le feedback.
- GLPI (`poc-glpi`) ne démarre pas tant que le port **8080** est pris par un autre projet sur ta machine.
- `docker compose down -v` efface **aussi** la mémoire (volume `pg_data`).
