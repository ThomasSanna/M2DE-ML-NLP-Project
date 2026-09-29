# Stack LDAP + GLPI — PoC Chatbot

Environnement simulé pour le PoC : annuaire **LDAP** (`example.org`, fictif) + **GLPI 11** (tickets) + **MariaDB**.

## Services

| Service | URL / Accès | Identifiants |
|---|---|---|
| **GLPI** | http://localhost:8080 | `glpi` / `glpi` (admin par défaut — à changer) |
| **API REST GLPI** | http://localhost:8080/apirest.php/ | voir §API |
| **phpLDAPadmin** | http://localhost:8081 | Login DN : `cn=admin,dc=example,dc=org` / mot de passe `LDAP_ADMIN_PASSWORD` du `.env` |
| **LDAP** | `ldap://localhost:389` | Base : `dc=example,dc=org` |
| **MariaDB** | interne (`db:3306`) | `glpi` / `MARIADB_PASSWORD` du `.env` |

## Démarrage

```bash
docker compose up -d
```

- GLPI s'auto-installe au premier démarrage (schéma BDD + cron).
- L'annuaire LDAP est peuplé automatiquement au premier démarrage via `ldap/bootstrap.ldif`.

> ⚠️ Si les volumes sont recréés (`docker compose down -v`), GLPI réinstalle tout seul,
> mais les permissions des volumes GLPI peuvent devoir être corrigées une fois :
> ```bash
> docker run --rm -v bisgambiglia-poc_glpi_files:/var/glpi/files alpine chown -R 33:33 /var/glpi/files
> docker run --rm -v bisgambiglia-poc_glpi_config:/var/glpi/config alpine chown -R 33:33 /var/glpi/config
> docker run --rm -v bisgambiglia-poc_glpi_marketplace:/var/glpi/marketplace alpine chown -R 33:33 /var/glpi/marketplace
> docker compose restart glpi
> ```

## Annuaire LDAP (personas)

7 personnes + 4 groupes, conformes au plan de génération de données (§7 du note-projet-complet) :

| UID | Rôle | Groupe | Mot de passe |
|---|---|---|---|
| `camille.dupont` | Étudiante M2 DE | etudiants | `camille2026` |
| `lucas.moreau` | Étudiant M2 DE | etudiants | `lucas2026` |
| `lea.petit` | Étudiante L3 Info | etudiants | `lea2026` |
| `thomas.girard` | Enseignant-chercheur | enseignants | `thomas2026` |
| `emma.lambert` | Maître de conférences | enseignants | `emma2026` |
| `hugo.mercier` | Technicien DSI | techniciens | `hugo2026` |
| `chloe.renard` | Gestionnaire scolarité | administratifs | `chloe2026` |

(Annuaire **fictif**, simulé : le LDAP réel de l'université n'est pas interrogeable publiquement — voir §1 de `doc/note-projet-complet.md`.)

Test rapide :

```bash
docker exec poc-ldap ldapsearch -x -H ldap://localhost \
  -D "cn=admin,dc=example,dc=org" -w "ChangeMeAdmin2026!" \
  -LLL -b "dc=example,dc=org" "(uid=camille.dupont)"
```

> ⚠️ L'image osixia n'importe `ldap/bootstrap.ldif` que si la base LDAP est vide au démarrage.
> Si le conteneur a été recréé après une première initialisation, l'import est sauté
> (le test ci-dessus ne renvoie rien). Dans ce cas, charger le LDIF à la main :
> ```bash
> docker exec poc-ldap sh -c 'ldapadd -x -c -H ldap://localhost \
>   -D "cn=admin,dc=example,dc=org" -w "$LDAP_ADMIN_PASSWORD" \
>   -f /container/service/slapd/assets/config/bootstrap/ldif/custom/50-bootstrap.ldif'
> ```

## API REST GLPI (activée)

L'API est activée avec login par identifiants, et le client API "full access from localhost"
couvre le réseau Docker `172.16.0.0/12`.

```bash
# 1. Ouvrir une session (identifiants par défaut glpi/glpi)
curl -X POST http://localhost:8080/apirest.php/initSession \
  -H "Content-Type: application/json" \
  -H "Authorization: Basic $(echo -n 'glpi:glpi' | base64)" \
  -d '{"init":"true"}'
# → {"session_token": "..."}

# 2. Lister les tickets
curl http://localhost:8080/apirest.php/Ticket \
  -H "Session-Token: <session_token>" \
  -H "App-Token: <app_token si configuré>"
```

> Ces réglages (activés directement en BDD au setup) sont persistés dans le volume `glpi_config`/`db_data`.
> En cas de recréation complète, il faudra les refaire :
> ```sql
> UPDATE glpi_configs SET value='1' WHERE name IN ('enable_api','enable_api_login_credentials');
> UPDATE glpi_apiclients SET is_active=1,
>   ipv4_range_start=INET_ATON('172.16.0.0'), ipv4_range_end=INET_ATON('172.31.255.255')
>   WHERE id=1;
> ```

## Configuration LDAP dans GLPI (à faire via l'UI)

`Configuration > Authentification > Annuaire LDAP` :

| Champ | Valeur |
|---|---|
| Nom | Univ Corse LDAP |
| Serveur | `ldap` (nom du service Docker) |
| Port | 389 |
| Base DN | `dc=example,dc=org` |
| Filtre de connexion (login) | `(&(objectClass=inetOrgPerson)(uid=*))` |
| Champ login | `uid` |
| Champ nom | `sn` |
| Champ prénom | `givenname` (en minuscules, sinon le prénom reste vide) |
| Champ email | `mail` |
| Bind DN (compte de lecture) | `cn=admin,dc=example,dc=org` |
| Mot de passe bind | valeur de `LDAP_ADMIN_PASSWORD` |

Puis activer la source dans `Configuration > Authentification > Configuration > Avancé`
et tester avec un persona (ex. `camille.dupont` / `camille2026`).

## Arrêt / reset

```bash
docker compose down          # stoppe (garde les données)
docker compose down -v       # stoppe et efface TOUT (LDAP + BDD + GLPI)
```
