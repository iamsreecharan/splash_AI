# Splash

Know what changed. Understand what breaks.

Splash V1 is a local-first web app and read-only GitHub App for a small private
pilot. It indexes Python and JavaScript/TypeScript code, follows dependencies
across files, analyzes pull requests in a background worker, and presents
source-linked impact alerts with feedback and deletion controls. The workflow
runs without an AI key; optional OpenAI analysis is explicitly enabled.

V1 ships the web app and GitHub integration. Linear, Slack, editor integrations,
chat, automated patches, and a browser extension are future releases.

## Run locally for free

Requires Docker Engine/Desktop with Compose v2, Git, and Python 3 for the smoke
check. About 4 GB of free memory is a practical starting point for the stack.
From this checkout:

```sh
make up
make smoke
```

`make up` preserves an existing `.env`, creates a private `.secrets` directory,
builds the applications, initializes PostgreSQL 16/pgvector, and starts Redis,
FastAPI, a Celery worker/recovery scheduler, and the Vite web server. All published
ports bind to loopback.
Open `http://localhost:5173` on your own computer. Local demo access is explicit;
it is unsuitable for a publicly accessible server. Use the sample controls in
the app to index the fixture and analyze an unsafe rename or a harmless change.

The local demo uses actual indexing and analysis of fixture source files. It
does not connect to GitHub, send code to OpenAI, or prove external credentials.
The GitHub App and Supabase setup are documented separately below.

Verified deterministic findings currently focus on FastAPI HTTP route/JSON-field
contracts consumed by literal JavaScript/TypeScript `fetch` calls. Dynamic URLs,
indirect client wrappers, unsupported frameworks, and other code relationships
may need investigation. Indexing a language does not guarantee every kind of
breaking change in that language will be detected.

```sh
make logs                # inspect startup and worker failures
make down                # stop containers; keep database and queue volumes
docker compose down -v   # permanently delete this local stack's data
```

## Develop and test

The services can run in Docker while Python and Node run on the host with fast
reload. Follow [local setup](docs/local-development.md) for pinned installation,
startup, checks, and managed-cloud trust configuration. See
[architecture](docs/architecture.md) for the HTTP and analysis contracts.

```sh
make test
python3 scripts/smoke.py
cd tests/e2e && npm ci && npx playwright install chromium && npm test
```

The smoke command checks the API/worker flow against the running local demo and
writes its results to the ignored `.splash/` directory. The browser tests exercise
the web app. Run `python3 scripts/smoke.py --verify-deletion` to check sample
erasure too, or remove it through the UI/API. Removing
a repository permanently deletes its active indexed data and analysis history.

## Connect real repositories

1. Configure [Supabase Auth](docs/supabase-auth.md), disable demo mode, and sign in.
2. Create and install the [read-only GitHub App](docs/github-app.md). A local
   webhook receiver needs a secure tunnel that you configure yourself.
3. Link your GitHub identity inside Splash, choose an authorized installation,
   select repositories, and index them. Open or update a pull request to analyze
   the new commit. Review the result and its source evidence in the app.

Read [privacy](docs/privacy.md), [security](docs/security.md), and
[cost controls](docs/costs.md) before opting in to external AI. Credentials go in
private environment settings or `.env`/`.secrets`, never in Git or chat.

The recommended initial deployment is local. [Render deployment](docs/deployment.md)
describes a later private pilot, its required services, and why a reliable
complete hosted stack should not be assumed free. No cloud resources are created
by this repository.
