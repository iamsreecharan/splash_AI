# Local development

## Container workflow

Run `make up` from the repository root. Docker Compose v2 starts the services
and waits for HTTP/database/cache health checks. `python -m app.bootstrap`
initializes the current V1 schema and pgvector extension before the API starts.
It can safely run again; it is not a general migration framework for later
schema changes. The worker starts only after the API is healthy.
The separate Celery scheduler triggers recovery of persisted jobs every minute.

Exported `SPLASH_*` process variables (including managed environment settings)
override matching `.env` values for API, worker, and scheduler. Compose forwards
those bindings directly; it does not copy secret values into `.env`. An existing
`.env` remains the local fallback. Recreate the containers after a settings
change so they receive the updated configuration. Use `docker compose config
--quiet` to validate syntax; printing the resolved configuration can expose
credential values.

Useful commands:

```sh
docker compose ps
docker compose logs --tail=100 backend worker
curl --fail http://127.0.0.1:8000/health
curl --fail http://127.0.0.1:8000/api/session
python3 scripts/smoke.py
```

If a port is already in use, stop the conflicting local service or change the
published port in your own Compose override and corresponding public URL/CORS
settings. Do not expose demo access to your LAN or a public tunnel. PostgreSQL
and Redis have persistent named volumes. `docker compose down` retains them;
`docker compose down -v` permanently removes both volumes.

## Host workflow with reload

Requires Python 3.12, Node 22, and Docker Compose v2. Dependency versions and
artifact hashes are recorded in the Python requirements lock; npm uses its lock.
The installation commands leave those lockfiles unchanged.

```sh
make services
cd backend
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements.txt
.venv/bin/python -m pip install --require-hashes -r requirements-dev.txt
cd ../frontend
npm ci
```

Use separate terminals. Set the two service URLs to host loopback, because names
`postgres` and `redis` are Docker-internal. The root `.env` is a Compose file,
not automatically a shell script; do not blindly source it.

```sh
# API terminal, repository root. Explicit settings need no secrets for the demo.
cd backend
export SPLASH_ENV=development SPLASH_DEMO_MODE=true SPLASH_AI_ENABLED=false
export SPLASH_DATABASE_URL=postgresql+psycopg://splash:splash-local-only@127.0.0.1:5432/splash
export SPLASH_REDIS_URL=redis://127.0.0.1:6379/0
.venv/bin/python -m app.bootstrap
.venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 --no-access-log
```

```sh
# Worker terminal, repository root; repeat the API's four export commands first.
cd backend
.venv/bin/celery -A app.tasks.celery_app worker --loglevel=info --concurrency=2
```

```sh
# Recovery scheduler terminal, repository root; repeat the same exports.
cd backend
.venv/bin/celery -A app.tasks.celery_app beat --loglevel=info --schedule=/tmp/splash-celerybeat
```

```sh
# Web terminal, repository root. Vite proxies /api and /health to the API.
cd frontend
npm run dev -- --host 127.0.0.1
```

For live credentials use a private process manager/environment file loaded by
your deployment, or Docker Compose's `env_file`. Keep secret values out of shell
history. API and worker must receive the same configuration and encryption key.

The backend defaults cap a repository snapshot at 1,000 files and 8 MB total,
with a 150 KB individual-file limit, a 1 MB webhook-body limit, and a retention
target of 10 snapshots per repository (current analysis snapshots are protected).
Override only after reviewing memory, queue,
privacy, and provider-cost effects. The corresponding settings are
`SPLASH_MAX_REPOSITORY_FILES`, `SPLASH_MAX_REPOSITORY_BYTES`,
`SPLASH_MAX_FILE_BYTES`, `SPLASH_MAX_WEBHOOK_BYTES`, and
`SPLASH_MAX_SNAPSHOTS_PER_REPOSITORY`.

## Checks

```sh
cd backend
.venv/bin/python -m pytest
cd ../frontend
npm run typecheck
npm test
npm run build
cd ..
python3 scripts/smoke.py
cd tests/e2e
npm ci
npx playwright install chromium
npm test
```

The smoke check exercises HTTP, the queue, persistent storage, indexing, the
four-stage workflow, evidence, feedback, and a harmless-change control. The
backend suite verifies deletion cascades and the browser suite checks the user
flow. `python3 scripts/smoke.py --verify-deletion` also verifies erasure against
the running database; it permanently deletes the sample's active data. The UI/API
repository removal action is available for other repositories.
External GitHub OAuth/webhooks, Supabase login, and OpenAI calls require actual
configured accounts and are separate live acceptance checks.

## Managed Codex cloud environment

Use the existing `/workspace/splash_AI` checkout; each cloud task is isolated.
Do not create a Git worktree unless the user requests one. The managed Docker
daemon supplies proxy settings, but build and runtime HTTPS clients also need
the session CA. Use the supplied override:

```sh
make cloud-up
python3 scripts/smoke.py
```

`CODEX_PROXY_CERT` must already name the platform CA file. The override mounts
the public CA as build secrets and read-only runtime files. It preserves TLS,
pip hash checks, and npm strict SSL. Do not disable verification or embed the
platform CA or credentials into image layers. For host npm installation set
`NODE_EXTRA_CA_CERTS` to the supplied CA only for that process. Host Python uses
the managed combined certificate store.

The managed user's original Docker configuration can be read-only, which makes
Buildx fail when it creates its metadata under `~/.docker/buildx`. `make cloud-up`
uses `infra/cloud-compose.sh` to set `BUILDX_CONFIG` to the writable, ignored
`.splash/buildx` directory only for that command. It leaves `DOCKER_CONFIG`,
registry authentication, the reachable proxy defaults, and context selectors
unchanged. No credential configuration is copied into the checkout or snapshot.
Do not select an empty new Docker configuration, because that loses the managed
proxy setup. To run another cloud Compose command, use
`bash infra/cloud-compose.sh logs --tail=100 backend worker scheduler` or the
same wrapper with other normal Compose arguments.

Only setup state and files can persist through cloud publication. Servers must
be restarted in later tasks. Validate readiness with `/health` and the smoke
flow, and inspect logs if either fails. A running process alone is insufficient.
