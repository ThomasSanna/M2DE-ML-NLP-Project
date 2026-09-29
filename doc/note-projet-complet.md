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

> **Vérification (08/09/2026)** : le LDAP de l'établissement (annuaire RENATER) existe mais **n'est pas interrogeable publiquement** (bind anonyme rejeté, filtrage firewall ; auth via CAS + Shibboleth, Base DN non publié). → Le PoC utilisera un **OpenLDAP simulé** (Docker, base fictive réaliste `dc=example,dc=org` + `ou=people`), à confirmer avec le prof si un accès réel était attendu.

## 2. Objectif

**Proof of Concept** d'un chatbot capable de répondre aux questions des utilisateurs en s'appuyant sur :
1. **Une mémoire** construite à partir de l'historique des tickets (questions/réponses passées), avec un **vrai mécanisme de pondération** basé sur les retours ("la réponse était-elle utile ?").
2. **La recherche internet** (dans le périmètre) pour ce qui n'est pas dans la mémoire.

Positionnement : réponses **simples mais correctes**, pas pointues. Envoi **direct** (ticket + mail **SMTP**), sans validation technicien ; feedback **utilisateur** après réception. Garde-fous **ShieldStral 3B** en entrée (question malveillante/inutile → ticket en attente, technicien répond) et en sortie (données sensibles).

**Enjeu concurrentiel** : ce projet est comparé à d'autres groupes → il faut **innover sur toutes les fonctionnalités** pour être meilleur (voir §6).

## 3. Infrastructure cible

Serveur mis à disposition :
- **~282 Go de VRAM** (probablement 2× NVIDIA **H200** de 141 Go), **2 To de RAM** (32×64 Go).
- **MIG** : chaque H200 se découpe en **7 unités de compute** → plusieurs modèles/expériences en parallèle sur le même serveur.
- Conséquence : **inférence 100 % locale** (pas d'API externe payante). Modèles fixés : **Gemma 4 12B thinking** (raisonnement/agent), **ShieldStral 3B** (garde-fous IN/OUT), **Gemma 4 E2B** (notation du feedback), embeddings **BGE-M3**.

## 4. Décisions arrêtées

| Question | Décision |
|---|---|
| Données | **Aucune donnée réelle** → générer un jeu de données de base **avec une IA**, en **français** |
| Mémoire | **Vrai mécanisme de pondération** (pas un simple RAG) — cœur de l'innovation |
| Feedback | Donné **par l'utilisateur final** par défaut ; possible d'innover (feedback technicien, implicite…) |
| Recherche internet | **Dans le périmètre** du PoC |
| Démonstration | Via une **API** (le PoC est exposé comme service) |
| Trigger pipeline | **Webhook GLPI** à la création du ticket (et à chaque réponse user) → `POST /ingest/ticket` (temps réel ; polling `GET /Ticket` écarté) |
| Modèles | **Gemma 4 12B thinking** (agent : répondre ou websearch, KV cache) + **ShieldStral 3B** (garde-fous IN/OUT) + **Gemma 4 E2B** (notation) ; embeddings **BGE-M3** |
| Garde-fous | ShieldStral 3B sur la question (malveillance/inutilité) et sur la réponse (données sensibles) ; blocage → ticket **en attente**, réponse technicien |
| Feedback | Réponse user (webhook) → **note Gemma 4 E2B** → embed + index de la paire Q/R avec score |
| Envoi réponse | Post **ticket GLPI** + **mail SMTP**, direct, sans validation tech |
| Critères d'évaluation | Pas encore définis par le prof — on verra |

## 5. Architecture envisagée


Composants techniques pressentis :
- **Génération de données** : LLM (via API pendant la dev, le PoC tournant lui en local) → tickets GLPI synthétiques réalistes.
- **Embeddings** : modèle multilingue français (ex. BGE-M3, multilingual-e5).
- **Base vectorielle** : Qdrant / Chroma / pgvector.
- **LLM local** : quantifié pour tenir dans une unité MIG.
- **API** : FastAPI + petite interface de chat pour la démo.
- **Ingest temps réel** : endpoint FastAPI `POST /ingest/ticket` alimenté par le **webhook GLPI** (secret partagé + file de retry).

### Pipeline LLM (détaillé — cf. Page-2 `doc/diagramme.drawio`)
1. **Webhook GLPI** à la création du ticket — et à chaque réponse user dans le fil.
2. **GET ticket** : question seule, ou **fil de discussion** complet si la question suit un ou plusieurs échanges Q/R du même ticket.
3. **Garde-fou IN — ShieldStral 3B** : juge la discussion en insistant sur la nouvelle question (malveillante ? vaut-elle une réponse ?). Si bloqué → ticket **en attente**, un **technicien** répond.
4. **Embeddings BGE-M3** de la question / du fil.
5. **Top-k + scores** des paires Q/R indexées → injectés dans le **prompt initial**.
6. **Appel LLM — Gemma 4 12B thinking** (KV cache conservé entre appels).
7. **Actions LLM** : **répondre**, ou **websearch** (x recherches) → nouvel appel Gemma 4 avec contexte + résultats web → boucle jusqu'à réponse.
8. **Garde-fou OUT — ShieldStral 3B** : pas de données sensibles dans la réponse (prompt système, etc.).
9. **Post dans le ticket GLPI + mail SMTP** à la personne. Envoi direct, sans validation tech.
10. **Feedback** : la réponse de l'user repasse par webhook et sert de feedback.
11. **Gemma 4 E2B** note l'échange → **embed + index** de la discussion/réponse LLM avec son score → la mémoire grandit.

## 6. Mécanisme de mémoire & pondération — le cœur à innover

### Score de chaque "connaissance" (paire Q/R validée)
Score multi-facteurs, ex. :

```
score = w1·feedback_utilisateur      (note **Gemma 4 E2B** sur la réponse user + utile/pas utile, stockée par paire Q/R)
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
- [x] Choix du LLM local et du modèle d'embeddings → **validés le 17/09/2026** : `mistralai/Shieldstral-1.0-3B` (garde-fous), `google/gemma-4-12B-it` (thinking), `google/gemma-4-E2B-it` (notation, ex-« 3B »), `BAAI/bge-m3` (embeddings).
- [ ] Volume du jeu de données synthétique (ordre de grandeur : centaines ? milliers de tickets ?).
- [ ] Le feedback utilisateur est-il dans la démo (client simulé) ou hors périmètre du live ?
- [ ] Moteur de recherche web : simple (DuckDuckGo/SearXNG) vs API structurée.
- [ ] Envoi SMTP des réponses : réutiliser le runbook Brevo existant (`doc/runbook-smtp-brevo-2026-09-09.md`) ?
- [ ] Multi-utilisateurs MIG : qui déploie quoi sur le serveur (coordination avec le prof) ?

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
