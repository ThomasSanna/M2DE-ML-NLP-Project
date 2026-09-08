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

> **Vérification (08/09/2026)** : le LDAP de l'univ (`ldap.univ-corse.fr`, RENATER) existe mais **n'est pas interrogeable publiquement** (bind anonyme rejeté, filtrage firewall ; auth via CAS `auth.univ-corse.fr` + Shibboleth, Base DN non publié). → Le PoC utilisera un **OpenLDAP simulé** (Docker, base fictive réaliste `dc=univ-corse,dc=fr` + `ou=people`), à confirmer avec le prof si un accès réel était attendu.

## 2. Objectif

**Proof of Concept** d'un chatbot capable de répondre aux questions des utilisateurs en s'appuyant sur :
1. **Une mémoire** construite à partir de l'historique des tickets (questions/réponses passées), avec un **vrai mécanisme de pondération** basé sur les retours ("la réponse était-elle utile ?").
2. **La recherche internet** (dans le périmètre) pour ce qui n'est pas dans la mémoire.

Positionnement : réponses **simples mais correctes**, pas pointues. Validation par le client/technicien avant envoi final.

**Enjeu concurrentiel** : ce projet est comparé à d'autres groupes → il faut **innover sur toutes les fonctionnalités** pour être meilleur (voir §6).

## 3. Infrastructure cible

Serveur mis à disposition :
- **~282 Go de VRAM** (probablement 2× NVIDIA **H200** de 141 Go), **2 To de RAM** (32×64 Go).
- **MIG** : chaque H200 se découpe en **7 unités de compute** → plusieurs modèles/expériences en parallèle sur le même serveur.
- Conséquence : **inférence 100 % locale** (pas d'API externe payante), modèles ouverts type Llama / Mistral / Qwen.

## 4. Décisions arrêtées

| Question | Décision |
|---|---|
| Données | **Aucune donnée réelle** → générer un jeu de données de base **avec une IA**, en **français** |
| Mémoire | **Vrai mécanisme de pondération** (pas un simple RAG) — cœur de l'innovation |
| Feedback | Donné **par l'utilisateur final** par défaut ; possible d'innover (feedback technicien, implicite…) |
| Recherche internet | **Dans le périmètre** du PoC |
| Démonstration | Via une **API** (le PoC est exposé comme service) |
| Critères d'évaluation | Pas encore définis par le prof — on verra |

## 5. Architecture envisagée

```
Utilisateur (persona LDAP)
   │  question
   ▼
API du chatbot (FastAPI)
   │
   ├─► Mémoire (BDD tickets + embeddings + scores de pondération)
   ├─► Recherche internet (si la mémoire ne suffit pas)
   │
   ▼
LLM local (H200) → projet de réponse (avec sources citées)
   │
   ▼
Validation technicien → envoi au client
   │
   ▼
Feedback client (utile / pas utile) → mise à jour des poids mémoire
```

Composants techniques pressentis :
- **Génération de données** : LLM (via API pendant la dev, le PoC tournant lui en local) → tickets GLPI synthétiques réalistes.
- **Embeddings** : modèle multilingue français (ex. BGE-M3, multilingual-e5).
- **Base vectorielle** : Qdrant / Chroma / pgvector.
- **LLM local** : quantifié pour tenir dans une unité MIG.
- **API** : FastAPI + petite interface de chat pour la démo.

## 6. Mécanisme de mémoire & pondération — le cœur à innover

### Score de chaque "connaissance" (réponse validée)
Score multi-facteurs, ex. :

```
score = w1·feedback_utilisateur      (utile / pas utile, stocké par ticket)
      + w2·validation_technicien     (règle la confiance de base)
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
| **Démonstration** | API + chat : boucle complète question → réponse → validation → feedback → score mis à jour |
| **Présentation** | Pitch : le problème, notre mécanisme de mémoire, démo live, résultats |

Échéance : **6 octobre**.

## 9. Questions ouvertes / à trancher

- [ ] Critères d'évaluation exacts du prof (rapport / démo / présentation) — à demander.
- [ ] Choix du LLM local et du modèle d'embeddings (à tester sur le serveur MIG).
- [ ] Volume du jeu de données synthétique (ordre de grandeur : centaines ? milliers de tickets ?).
- [ ] Le feedback utilisateur est-il dans la démo (client simulé) ou hors périmètre du live ?
- [ ] Moteur de recherche web : simple (DuckDuckGo/SearXNG) vs API structurée.
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
