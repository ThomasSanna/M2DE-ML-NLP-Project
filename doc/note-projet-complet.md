# Projet — Chatbot avec mémoire (PoC)

> Notes consolidées le 08/09/2026 — remplace `note_projet.md` (conservé en fin de fichier pour trace).
> **Deadline : 6 octobre** — Livrables : **rapport, démonstration, présentation**.

---

## 1. Contexte

À l'université :
- Les utilisateurs (annuaire **LDAP**, mails **Outlook**, etc.) posent des demandes dans **GLPI** (outil de tickets : demande d'ordinateur, problème de mail, résolution d'incidents…).
- Chaque question est **validée par un technicien**, le ticket lui est affecté, il résout et répond **par mail** à la personne.
- **GLPI a une BDD** qui enregistre toutes les questions/réponses.
- **Problème actuel : aucune mémoire** de ce qui a déjà été répondu. Chaque réponse est réinventée, les questions récurrentes re-consommées manuellement.
- **LDAP est public ; GLPI non public** → le PoC simulera les deux (on génère nos propres données).

> **Vérification (08/09/2026)** : le LDAP de l'établissement (annuaire RENATER) existe mais **n'est pas interrogeable publiquement** (bind anonyme rejeté, filtrage firewall ; auth via CAS + Shibboleth, Base DN non publié). → Le PoC utilise un **OpenLDAP simulé** (Docker, base fictive réaliste `dc=example,dc=org` + `ou=people`).
>
> **Correction (doc du prof, `Projet M2.pdf`)** : l'annuaire réel est un **Active Directory** (`idcsrv-01.udcpp.priv`, base `dc=udcpp,dc=priv`), **interrogeable via le VPN de l'université** (`vpn.univ-corse.fr`) avec un bind authentifié par son propre compte (`votre_compte@udcpp.priv`). Garder l'OpenLDAP simulé ou brancher l'AD réel : à trancher (cf. §9).

## 2. Objectif

**Proof of Concept** d'un chatbot capable de répondre aux questions des utilisateurs en s'appuyant sur :
1. **Une mémoire** construite à partir de l'historique des tickets (questions/réponses passées), avec un **vrai mécanisme de pondération** basé sur les retours ("la réponse était-elle utile ?").
2. **La recherche internet** (dans le périmètre) pour ce qui n'est pas dans la mémoire.

Positionnement : réponses **simples mais correctes**, pas pointues. Envoi **direct** (ticket + mail **SMTP**), sans validation technicien ; feedback **utilisateur** après réception. Garde-fous en entrée (question malveillante/inutile → ticket en attente, technicien répond) et en sortie (données sensibles) ; **ShieldStral 3B** prévu mais **pas encore déployé** par l'université → **Gemma 3 4B en attendant**, cf. §3.

**Enjeu concurrentiel** : ce projet est comparé à d'autres groupes → il faut **innover sur toutes les fonctionnalités** pour être meilleur (voir §6).

## 3. Infrastructure cible

Serveur mis à disposition :
- **~282 Go de VRAM** (probablement 2× NVIDIA **H200** de 141 Go), **2 To de RAM** (32×64 Go).
- **MIG** : chaque H200 se découpe en **7 unités de compute** → plusieurs modèles/expériences en parallèle sur le même serveur.
- Conséquence : **inférence sur le serveur de l'université** (pas d'API externe payante). Les modèles sont **déjà déployés par l'université** et exposés en HTTP (vLLM, API compatible OpenAI), **VPN de l'université obligatoire** (`vpn.univ-corse.fr`). Liste fournie par le prof (`Projet M2.pdf`) :

| Rôle | Modèle | Endpoint |
|---|---|---|
| LLM principal (agent) | `google/gemma3-12b-it` | `http://193.48.31.6:32297/v1/chat/completions` |
| LLM léger (notation ; garde-fous en attendant ShieldStral) | `google/gemma3-4b-it` | `http://193.48.31.6:30091/v1/chat/completions` |
| Embeddings | `BAAI/bge-multilingual-gemma2` (3584 dimensions) | `http://193.48.31.6:31550/v1/embeddings` |
| Reranker | `BAAI/bge-reranker-v2-gemma` | `http://193.48.31.6:31127/rerank` (corps `{"pairs": [[question, doc], …]}`) |

> **Correction** : les notes prévoyaient Gemma 4 12B thinking, Gemma 4 E2B, ShieldStral 3B et BGE-M3. Ce ne sont **pas** les modèles servis : c'est **Gemma 3** (12B et 4B, sans mode thinking), des embeddings **bge-multilingual-gemma2**, et un **reranker** en plus. **ShieldStral 3B** reste prévu pour les garde-fous mais n'est **pas encore déployé** : Gemma 3 4B le remplace d'ici là.

## 4. Décisions arrêtées

| Question | Décision |
|---|---|
| Données | **Aucune donnée réelle** → générer un jeu de données de base **avec une IA**, en **français** |
| Mémoire | **Vrai mécanisme de pondération** (pas un simple RAG) — cœur de l'innovation |
| Feedback | Donné **par l'utilisateur final** par défaut ; possible d'innover (feedback technicien, implicite…) |
| Recherche internet | **Dans le périmètre** du PoC |
| Démonstration | Via une **API** (le PoC est exposé comme service) |
| Trigger pipeline | **Webhook GLPI** à la création du ticket (et à chaque réponse user) → `POST /ingest/ticket` (temps réel ; polling `GET /Ticket` écarté) |
| Modèles | Ceux servis par l'université (§3) : **Gemma 3 12B** (agent : répondre ou websearch) + **Gemma 3 4B** (notation ; garde-fous en attendant ShieldStral 3B) ; embeddings **bge-multilingual-gemma2** ; reranker **bge-reranker-v2-gemma** |
| Garde-fous | Sur la question (malveillance/inutilité) et sur la réponse (données sensibles), par **ShieldStral 3B** une fois déployé par l'université ; **en attendant, prompt de modération sur Gemma 3 4B** ; blocage → ticket **en attente**, réponse technicien |
| Feedback | Réponse user (webhook) → **note Gemma 3 4B** → embed + index de la paire Q/R avec score |
| Base vectorielle | **PostgreSQL + pgvector** (API mémoire dans `api/`) |
| Envoi réponse | Post **ticket GLPI** + **mail SMTP**, direct, sans validation tech |
| Critères d'évaluation | Pas encore définis par le prof — on verra |

## 5. Architecture envisagée


Composants techniques pressentis :
- **Génération de données** : LLM (via API pendant la dev, le PoC tournant lui en local) → tickets GLPI synthétiques réalistes.
- **Embeddings** : `BAAI/bge-multilingual-gemma2`, servi par l'université.
- **Base vectorielle** : PostgreSQL + pgvector (retenu).
- **LLM** : Gemma 3 12B / 4B servis par l'université (pas de déploiement de notre côté).
- **GLPI** : API REST activée (App Token + User Token, cf. `Projet M2.pdf`) pour lire les tickets et y poster les réponses.
- **API** : FastAPI + petite interface de chat pour la démo.
- **Ingest temps réel** : endpoint FastAPI `POST /ingest/ticket` alimenté par le **webhook GLPI** (secret partagé + file de retry).

### Pipeline LLM (détaillé — cf. Page-2 `doc/diagramme.drawio`)
1. **Webhook GLPI** à la création du ticket — et à chaque réponse user dans le fil.
2. **GET ticket** : question seule, ou **fil de discussion** complet si la question suit un ou plusieurs échanges Q/R du même ticket.
3. **Garde-fou IN — ShieldStral 3B** (en attendant son déploiement : **Gemma 3 4B**, prompt de modération) : juge la discussion en insistant sur la nouvelle question (malveillante ? vaut-elle une réponse ?). Si bloqué → ticket **en attente**, un **technicien** répond.
4. **Embeddings bge-multilingual-gemma2** de la question / du fil.
5. **Top-k + scores** des paires Q/R indexées, **reclassés par le reranker** bge-reranker-v2-gemma → injectés dans le **prompt initial**.
6. **Appel LLM — Gemma 3 12B** (le cache KV est géré côté serveur par vLLM, pas par nous).
7. **Actions LLM** : **répondre**, ou **websearch** (x recherches) → nouvel appel Gemma 3 avec contexte + résultats web → boucle jusqu'à réponse.
8. **Garde-fou OUT — ShieldStral 3B** (en attendant : **Gemma 3 4B**) : pas de données sensibles dans la réponse (prompt système, etc.).
9. **Post dans le ticket GLPI + mail SMTP** à la personne. Envoi direct, sans validation tech.
10. **Feedback** : la réponse de l'user repasse par webhook et sert de feedback.
11. **Gemma 3 4B** note l'échange → **embed + index** de la discussion/réponse LLM avec son score → la mémoire grandit.

## 6. Mécanisme de mémoire & pondération — le cœur à innover

### Score de chaque "connaissance" (paire Q/R validée)
Score multi-facteurs, ex. :

```
score = w1·feedback_utilisateur      (note **Gemma 3 4B** sur la réponse user + utile/pas utile, stockée par paire Q/R)
      + w2·validation_technicien     (hors live : pas de validation avant envoi ; sert en révision différée si le tech corrige)
      + w3·taux_de_réutilisation     (nb de fois réutilisée avec succès)
      + w4·fraîcheur                 (décroissance temporelle — une réponse de 2019 pèse moins)
      + w5·similarité_contextuelle   (même catégorie/même type de demande)
```

### Idées d'innovation (pour dépasser les autres groupes)
1. **Consolidation** : regrouper les tickets quasi-doublons en **connaissances canoniques** dont les scores s'agrègent (une question posée 40 fois avec de bons retours devient une réponse "experte").
2. **Sélection type bandit** : les réponses souvent validées remontent ; une réponse mal notée est **pénalisée** et marquée **"à réviser"** pour le technicien → la mémoire se corrige elle-même.
3. **Boucle de révision** : file de réponses dégradées que le technicien peut corriger ; la correction hérite du score historique.
4. **Escalade honnête** : si le score de confiance (meilleure source trop faible, conflit de réponses) est trop bas → ne pas répondre, escalader au technicien. Mieux vaut ne pas répondre qu'inventer.
5. **Traçabilité** : chaque réponse IA cite ses sources (tickets + liens web) → crédibilité en démo.
6. **Détection de doublon/reluire** : "cette question a déjà été posée N fois" — gain de temps mis en avant.
7. **Dashboard mémoire** (bonus démo) : taux de résolution automatique, répartition par catégorie, évolution des scores.

## 7. Génération des données (français)

- **Personas** : étudiants, enseignants, chercheurs, personnel administratif.
- **Catégories réalistes de tickets** : mots de passe, mail/Outlook, WiFi/VPN, impression, logiciels, matériel, accès salles, licences…
- Générer : `question utilisateur` + `réponse technicien` (qualité variable) + `feedback simulé` (utile/pas utile) + métadonnées (date, catégorie, technicien).
- Générer aussi un **jeu de test de questions inédites** pour mesurer la qualité des réponses du chatbot (pas dans la mémoire).

## 8. Livrables & jalons

| Livrable | Contenu |
|---|---|
| **Rapport** | Contexte, état de l'art (RAG, mémoire, feedback), architecture, mécanisme de pondération, évaluation |
| **Démonstration** | API + chat : boucle complète question → garde-fous → réponse → feedback noté → score mis à jour |
| **Présentation** | Pitch : le problème, notre mécanisme de mémoire, démo live, résultats |

Échéance : **6 octobre**.

## 9. Questions ouvertes / à trancher

- [ ] Critères d'évaluation exacts du prof (rapport / démo / présentation) — à demander.
- [x] Choix des modèles → **imposés par ce que sert l'université** (`Projet M2.pdf`) : `google/gemma3-12b-it`, `google/gemma3-4b-it`, `BAAI/bge-multilingual-gemma2` (embeddings), `BAAI/bge-reranker-v2-gemma` (reranker). Remplace la liste du 17/09 (Gemma 4, BGE-M3), qui ne correspond à aucun modèle servi.
- [ ] Garde-fous : **ShieldStral 3B** à venir sur le serveur de l'université ; **Gemma 3 4B en attendant** (garder le client du garde-fou interchangeable pour basculer sans toucher à la pipeline).
- [ ] Annuaire : garder l'OpenLDAP simulé ou utiliser l'AD réel (`idcsrv-01.udcpp.priv`, via VPN) ?
- [ ] Volume du jeu de données synthétique (ordre de grandeur : centaines ? milliers de tickets ?).
- [ ] Le feedback utilisateur est-il dans la démo (client simulé) ou hors périmètre du live ?
- [ ] Moteur de recherche web : simple (DuckDuckGo/SearXNG) vs API structurée.
- [ ] Envoi SMTP des réponses : réutiliser le runbook Brevo existant (`doc/runbook-smtp-brevo-2026-09-09.md`) ?
- [x] Déploiement des modèles sur le serveur → **fait par l'université**, on consomme les endpoints (VPN requis).

---

## Annexe — note d'origine (verbatim)

> 6 octoble
> l'univ utiliser ldap, outlook (mails), ...
> rapport, demonstration, présentation
> 282 VRAM 32*64 RAM
> configuration mig , 1 carte nvidia H200 divité en 7 unité de compute (en gros on peut leur demander de mettre un modele dans leurs serveurs)
> répondre aux question des personnes via IA. Réponse peut être mauvaise.
>
> ### Contexte :
> LDAP. Une personne dans LDAP peut poser une question dans GLPI (outil dans lequel on peut poser des questions (demande d'ordinateur, résolution de problèmes, de mail...))
> Ces questions, validées par un technicien, le ticket de la question est affectée à un technicien, et va le résoudre en envoyant le mail en retour à la personne qui a posé la question.
> actuellement On n'a pas de mémoire de ce qu'on a répondu aux questions précédentes.
> le LDAP est public, pas le GLPI. on le fera nous meme
> GLPI a une bdd qui enregistre toutes les réponses aux questions posées.
>
> ### Ce qu'on veut :
> PROOF OF CONCEPT
> Pour ce projet, on va faire une mémoire: demander au client si la reponse était utile), des nouvelles. Mettre des poids sur à quel point la réponse était utile.
> Avec cette mémoire, et avec recherche internet, un chatbot pourra répondre aux questions des utilisateurs plus tard, validée par le client. Les réponses IA ne doivent pas être très pointues, tout en restant correctes.
