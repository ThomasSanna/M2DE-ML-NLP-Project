# Runbook — LDAP + GLPI + SMTP Brevo (ce qui a marché le 2026-09-09)

Archive factuelle de la mise en route qui fonctionne actuellement.
Stack : `LDAP (osixia/openldap)` + `GLPI 11.0.8` + `MariaDB 11` via Docker.
Fichiers projet : `docker-compose.yml`, `.env`, `ldap/bootstrap.ldif`, `README.md`.

## 0. État de départ (vérifié)

- `docker compose ps` : `poc-ldap` (healthy, `0.0.0.0:389->389`), `poc-db` (healthy),
  `poc-glpi` (`0.0.0.0:8080->80`), `poc-phpldapadmin` (`0.0.0.0:8081->80`).
- `.env` (valeurs de démo) :
  `LDAP_ADMIN_PASSWORD=ChangeMeAdmin2026!`, `LDAP_CONFIG_PASSWORD=ChangeMeConfig2026!`,
  `MARIADB_ROOT_PASSWORD=ChangeMeRoot2026!`, `MARIADB_PASSWORD=ChangeMeGlpi2026!`.
- Annuaire : 7 users `inetOrgPerson` + 4 groupes (`etudiants`, `enseignants`,
  `techniciens`, `administratifs`) dans `ldap/bootstrap.ldif`.
  Comptes lambda : `camille.dupont/camille2026`, `lucas.moreau/lucas2026`, `lea.petit/lea2026`.
- GLPI : `http://localhost:8080`, admin `glpi/glpi`.
  API déjà activée en BDD (sans rapport avec les mails).

## 1. Connexion admin GLPI via Playwright (méthode qui marche)

- Aller sur `http://localhost:8080/` (page `Authentification - GLPI`).
- Remplir `#login_name=glpi`, `#login_password=glpi`.
- Le bouton `Se connecter` (`button[name=submit]`) est recouvert par un overlay
  select2/div : le clic Playwright classique timeout.
  Méthode qui marche : clic JS dans la page :
  `document.querySelector('button[name=submit]').click()` via evaluate,
  puis attente ~5 s. Arrivée sur `/front/central.php` (`Standard interface - GLPI`).
- Ne pas utiliser `form.submit()` : un champ nommé `submit` masque la méthode.
- Pour `glpi/glpi` en local, mettre la source `select[name=auth]=local`.
  Après création LDAP, la source par défaut devient `Univ Corse LDAP`.

## 2. Annuaire LDAP dans GLPI (créé et testé OK)

- Page : `Configuration > Authentification > Annuaires LDAP` (`/front/authldap.php`).
  Au départ : `No results found`.
- Nouveau : `/front/authldap.form.php?preconfig=OpenLDAP` (pré-remplit :
  filtre `(objectClass=inetOrgPerson)`, `Login field=uid`,
  `Synchronization field=entryuuid`, `Use bind=Yes`, port `389`).
- Valeurs saisies (libellés exacts constatés dans GLPI 11.0.8) :
  - `Name=Univ Corse LDAP`
  - `Default server=Yes` (`select[name=is_default]=1`)
  - `Active=Yes` (`select[name=is_active]=1`)
  - `Server=ldap` (nom du service Docker, pas `localhost`)
  - `Port=389`
  - `Connection filter` (`textarea[name=condition]`) =
    `(&(objectClass=inetOrgPerson)(uid=*))`
  - `BaseDN` (`input[name=basedn]`) = `dc=example,dc=org`
  - `Use bind=Yes` (`select[name=use_bind]=1`)
  - `RootDN (for non anonymous binds)` (`input[name=rootdn]`) =
    `cn=admin,dc=example,dc=org`
  - `Password (for non-anonymous binds)` (`input[name=rootdn_passwd]`) =
    `ChangeMeAdmin2026!` (vient de `.env`)
  - `Login field` (`input[name=login_field]`) = `uid`
  - `Synchronization field` = `entryuuid` (déjà pré-rempli)
- Sauvegarde via clic JS sur le bouton `Add` (même raison d'overlay qu'au login).
  Résultat : redirection `/front/authldap.php?next=extauth_ldap&id=1`,
  ligne `Univ Corse LDAP | ldap | 2026-09-09 | Yes`.
- Onglet `Test` (`/front/authldap.form.php?id=1&forcetab=AuthLDAP$1`) :
  `Connection to ldap on port 389 succeeded`, `Base DN ... is configured`,
  `LDAP URI check succeeded`, `Authentication succeeded`,
  `Search succeeded (7 entries found)`.
- Page `Configuration > Authentification > Configuration`
  (`/front/auth.settings.php`) déjà OK :
  ajout auto depuis source externe = Yes, ajout sans habilitation depuis LDAP = Yes.

## 3. Pourquoi Office365 univ ne passe pas (constaté, abandonné pour la démo)

- Boîte univ `20220747@webmail.universita.corsica` derrière un portail CAS/SSO fédéré :
  connexion web OK via redirection CAS, mais `smtp.office365.com:587` en `AUTH LOGIN`
  échoue toujours :
  - sans auth : `530 5.7.57 Client not authenticated`, `MAIL FROM:` vide ;
  - avec login court `20220747` : `535 5.7.3 Authentication unsuccessful` ;
  - cause : le SSO fédéré ne supporte pas le basic-auth SMTP direct.
- Conclusion retenue : garder l'univ comme destinataire, envoyer via Brevo.

## 4. Compte Brevo (création manuelle obligatoire)

- Création automatisée via Playwright impossible : validation email + code,
  téléphone, CAPTCHA, CGU à accepter en nom propre.
- Fait à la main sur `brevo.com/fr` : compte gratuit (~300 mails/jour),
  `Paramètres > Clés API > Clés SMTP > Générer`.
- Relais obtenu :
  - `Serveur SMTP=smtp-relay.brevo.com`, `Port=587`
  - `Login SMTP=b8a16e001@smtp-brevo.com` (login relais, pas une adresse d'envoi)
  - `Mot de passe SMTP=clé xsmtpsib-...` (tapie à la main dans GLPI, jamais via chat)
- Expéditeur validé dans Brevo : `Demo GLPI <20220747@webmail.universita.corsica>`
  (statut Vérifié), puis bascule volontaire vers `thomassanna1e@gmail.com`
  (adresse Brevo validée, meilleure délivrabilité vers Gmail).
- Déblocage IP obligatoire (erreur `525 5.7.1 Unauthorized IP address` sinon) :
  `Menu compte (avatar) > Settings > Security > Authorized IPs`,
  section `Blocking unauthorized IP addresses`, ligne `SMTP keys` :
  `Authorize IP addresses` avec l'IP publique cliente vue dans les logs
  (`46.193.64.223`), ou `Deactivate for SMTP` pour une démo à IP dynamique.
  Doc : `help.brevo.com` (`Troubleshooting SMTP`, `Authorize and block IP addresses`).
- Sécurité : les logs SMTP contiennent login + clé en base64 décodable.
  Ne plus les poster en clair ; régénérer la clé après la démo.

## 5. Configuration e-mail GLPI qui a marché (GLPI 11.0.8 réel)

- Activer d'abord :
  `Configuration > Notifications` (`/front/setup.notification.php`) :
  cocher `Enable notifications`, sauvegarder (le formulaire réclame un submit qui
  inclut le bouton, pas `HTMLFormElement.prototype.submit` seul),
  puis cocher `Enable email notifications`, re-sauvegarder.
  Constaté : après la 1re sauvegarde, les cases e-mail/navigateur passent de
  `disabled` à éditables et le menu affiche `Email notifications configuration`.
- Page : `Configuration > Notifications > Configuration des suivis par e-mail`
  (`/front/notificationmailingsetting.form.php`). Libellés exacts constatés :
  `Administrator email address`, `Administrator name`,
  `Email sender address`, `Email sender name`,
  `Reply-To address`, `Reply-To name`, `No-Reply address`, `No-Reply name`,
  `Email signature`, `Way of sending emails`, `Max. delivery retries`,
  `Try to deliver again in (minutes)`, boutons `Save` (`button[name=update]`)
  et `Send a test email to the administrator`.
- Point important : dans cette 11.0.8, `Way of sending emails` (`select[name=smtp_mode]`)
  ne propose que `0=PHP`, `1=SMTP`, `4=SMTP+OAUTH` (pas de `SMTP+TLS` séparé ;
  le TLS se négocie en auto sur le port 587).
- Valeurs finales sauvegardées et re-vérifiées après reload :
  - `Way of sending emails=SMTP` (`1`)
  - `Hôte SMTP` (`smtp_host`) = `smtp-relay.brevo.com`
  - `Port` (`smtp_port`) = `587`
  - `Login SMTP` (`smtp_username`) = `b8a16e001@smtp-brevo.com`
  - `Mot de passe SMTP` = clé Brevo saisie à la main
  - `Adresse e-mail expéditeur` (`from_email`) = `thomassanna1e@gmail.com`
  - `Nom expéditeur` = `GLPI Support`
  - `Adresse de réponse` (`replyto_email`) = `thomassanna1e@gmail.com`
  - `Adresse No-Reply` = `thomassanna1e@gmail.com`
  - `Expéditeur de l'e-mail` (`smtp_sender`) = `thomassanna1e@gmail.com`
  - `Vérifier certificat` = Yes, retries `5` / `5 min`,
    signature `-- Equipe support demo`
  - `Adresse e-mail administrateur` = adresse univ (copie admin reçue dessus)
- Règle qui a tout débloqué : toujours cliquer `Save` via
  `button[name=update]` en JS puis attendre ~5 s et revérifier après reload,
  car le test `Send test` utilise la config **sauvegardée**, pas les champs
  juste tapés. D'où les faux `530`/`535` quand on teste avant de sauver,
  et le `MAIL FROM:` vide quand `from_email` est vide.

## 6. Destinataire lambda (point critique : le LDAP écrase GLPI)

- Fiche : `Administration > Utilisateurs > camille.dupont` (ID 7).
  Champ e-mail : `input[name=_useremails[1]]` (puis `[2]` après resync).
- Tentative GLPI seule (`thomassanna1e@gmail.com`) écrasée au login suivant :
  `Last synchronization` a remis `camille.dupont@example.org`.
  Cause : l'e-mail est synchronisé depuis le LDAP à chaque connexion.
- Fix durable appliqué :
  - `ldapmodify` immédiat :
    `docker cp fix-camille-mail.ldif poc-ldap:/tmp/...` puis
    `ldapmodify -x -H ldap://localhost -D cn=admin,... -w ... -f /tmp/...`
    avec `replace: mail` -> `thomassanna2e@gmail.com`.
    Vérifié par `ldapsearch ... (uid=camille.dupont) mail`.
  - + `ldap/bootstrap.ldif` édité pour les futurs `down -v` :
    `mail: camille.dupont@example.org` -> `mail: thomassanna2e@gmail.com`.
  - Reconnexion `camille.dupont` en source `ldap-1` pour resynchroniser :
    la fiche affiche ensuite `thomassanna2e@gmail.com`.
- Valeur finale : `camille.dupont` -> `thomassanna2e@gmail.com`.

## 7. Preuve bout-en-bout (tickets réellement envoyés)

- Connexion lambda : page `/`, `select[name=auth]=ldap-1 (Univ Corse LDAP)`,
  `camille.dupont/camille2026` -> arrivée `/Helpdesk` (Self-Service).
- Création : `Service catalog > Report an issue` (`/Form/Render/1`),
  `Title=[name=answers_6]`, `Description=TinyMCE sur [name=answers_7]`
  (textarea `display:none` : remplir via l'iframe `Rich Text Area` / API TinyMCE,
  pas au clavier brut), `Submit` via
  `document.querySelector('[data-glpi-form-renderer-action=submit]').click()`.
- Tickets créés par Camille (contenus écrits par Camille, pas par l'admin) :
  - `#1 Test mail Brevo - sanitation`
  - `#2 Test 2 vers gmail`
  - `#3 Test 3 expediteur valide` (`Test avec expediteur thomassanna1e valide vers...`)
  Chaque fois : `Form submitted / Item successfully created`.
- Envoi : `Administration > File de notifications`
  (`/front/queuednotification.php`) listait 2 lignes par ticket
  (`[GLPI #...] New ticket ...` vers admin + demandeur, `NUMBER OF TRIES=0`),
  puis action auto `Configuration > Actions automatiques > queuednotification`
  (`/front/crontask.form.php?id=22`, `Send mails in queue`, toutes les minutes,
  `Last run=Never` au départ) déclenchée par bouton `Execute` en JS.
  Après exécution (`Last run` renseigné), la file affiche `No results found` = remis à Brevo.
- Réception constatée : ticket 3 bien reçu sur `thomassanna2e@gmail.com`
  de la part de `thomassanna1e@gmail.com`, plus copie admin sur `20220747@...`.
  La copie admin est normale : la notif `Nouveau ticket` cible
  `Demandeur + Administrateur` (+ technicien si assigné).
  Pour couper la copie : `Configuration > Notifications > Notifications >
  [Nouveau ticket] > Destinataires`, décocher `Administrateur`.
- Réponse admin -> Camille (suivi/solution) pas encore testée : la tentative
  `Answer` sur le ticket 1 a cliqué le mauvais `Add` et n'a rien posté.
  Tous les mails reçus sont des `New ticket`, aucun `Nouveau suivi` pour l'instant.

## 8. Rappels sécu / démo

- Mots de passe et clé Brevo visibles en base64 dans les logs collés :
  changer le mdp univ concerné si exposé, régénérer la clé Brevo après la démo.
- MdP GLPI stocké quasi en clair : préférer un compte d'envoi dédié, pas un perso.
- Spam : l'alignement DKIM/DMARC Brevo <-> domaine univ/Gmail n'est pas fait ;
  vérifier les spams côté réception pendant la démo.
