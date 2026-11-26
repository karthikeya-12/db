# Self-hosting Nango

Nango handles the OAuth sign-in for each connector (Gmail, Slack, Notion, ...) and stores the tokens.
Our API asks Nango for the connections and the synced records. This guide runs Nango on your machine
with Docker.

## 1. Generate the secrets (once)

```
uv run python scripts/setup_nango_env.py
```

This writes `deploy/nango/.env` (git-ignored) with a random encryption key and database password.
It never overwrites an existing file. **Keep a backup of `NANGO_ENCRYPTION_KEY`**: without it, stored connections cannot be read.

## 2. Start Nango

```
docker compose -f deploy/nango/docker-compose.yml --env-file deploy/nango/.env up -d
docker compose -f deploy/nango/docker-compose.yml logs -f nango-server     # wait until it reports it is listening
```

It starts three containers: Nango's own Postgres and Redis (not reachable from outside Docker) and the Nango server.
Ports: **3003** (API + dashboard) and **3009** (the Connect screen shown to end users). The image is `linux/amd64`;
on Apple Silicon or ARM Windows it runs under emulation and is slower to start.

## 3. Create your dashboard account and copy the secret key

1. Open <http://localhost:3003>. Nango 0.70 has its own accounts, so click **Sign up** and create one with your email and a password.
   (The dashboard user/password lines in `deploy/nango/.env` are not used by this version.)
2. A self-hosted Nango cannot send the verification email. Mark your account verified directly:

   ```
   docker compose -f deploy/nango/docker-compose.yml exec nango-db psql -U nango -d nango -c "UPDATE _nango_users SET email_verified = true;"
   ```

   then sign in at <http://localhost:3003/signin>.
3. Open **Environment Settings** (the `dev` environment is fine to start with) and copy the **Secret Key** into the project's `.env`:

   ```
   NANGO_BASE_URL=http://localhost:3003
   NANGO_SECRET_KEY=<the secret key>
   ```

4. In the same screen set the **Webhook URL** to `http://host.docker.internal:8000/api/nango/webhook`
   (that is how the Nango container reaches our API on your machine).

## 4. Check it

```
uv run python scripts/nango_check.py
```

It confirms the server answers and the secret key is accepted, and lists the integrations configured so far (none yet).

## 5. Integrations (done per provider, later)

Each provider (Google, Slack, Notion, ...) needs an OAuth app that **you** create at the provider, then an
integration in the Nango dashboard that holds its client id and secret. The Connectors screen (P3.3) will list
what is configured, and `docs/CONNECTORS.md` (P3.5) will have the exact steps per provider, including the
callback URL Nango shows you for each one.

## Troubleshooting

- **Browser says "didn't send any data"**: the server listens on 8080 inside the container and the compose file maps 3003 to it; make sure you are on the current `docker-compose.yml` and re-run `up -d`.
- **Port already in use**: set `NANGO_SERVER_PORT` / `NANGO_CONNECT_UI_PORT` in `deploy/nango/.env` and update `NANGO_BASE_URL` to match.
- **nango-server keeps restarting**: `docker compose -f deploy/nango/docker-compose.yml logs nango-server`; a missing or malformed `NANGO_ENCRYPTION_KEY` is the usual cause.
- **Start over completely** (deletes all connections): `docker compose -f deploy/nango/docker-compose.yml down -v`, then delete `deploy/nango/.env` and repeat from step 1.
