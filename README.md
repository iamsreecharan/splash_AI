# Splash

Splash checks pull requests for changes that could break other code in the same
repository.

The sample project shows the kind of problem it looks for: an API renames
`temperature` to `current_temperature`, but a TypeScript component still reads
`machine.temperature`. Splash flags the mismatch and shows the source lines
behind it, so you can check the finding before fixing anything.

## Run it locally

The demo needs no accounts or API keys. You'll need Git, Make, Docker with
Compose v2, and Python 3 for the smoke check. Use a Linux, macOS, or WSL shell
and allow about 4 GB of free memory for the containers.

```sh
git clone https://github.com/iamsreecharan/splash_AI.git
cd splash_AI
make up
```

Open http://localhost:5173 and choose **Try sample project**. Try the field
rename, then the harmless change. The demo indexes the bundled files and runs
the same analysis worker used for GitHub PRs. Paid AI is off by default.

`make up` creates `.env` if it's missing and starts the app, database, queue,
worker, and recovery scheduler. It leaves an existing `.env` alone. The services
listen on localhost; keep demo mode on your own machine until you've configured
authentication.

```sh
make smoke   # check the running API and worker
make logs    # watch service logs
make down    # stop the app and keep its data
```

To erase the local database and queue as well, run `docker compose down -v`.

## What it checks

Current detection covers specific FastAPI routes and JSON response fields used
by JavaScript or TypeScript `fetch` calls. Dynamic URLs, client wrappers, other
frameworks, and cross-repository changes still need manual review. A run with no
findings doesn't prove that a PR is safe.

Open an alert to see the affected code and suggested next steps. You can mark
the finding useful, irrelevant, or resolved. **Analysis History** lets you find
past runs by project, status, title, or PR number. A completed analysis has a
**Download report** button that saves its findings and source excerpts as
Markdown, including when there are no findings.

Linear, Slack, chat, suggested patches, and editor extensions are planned for
later releases.

## Connect a GitHub repository

1. Set up [Supabase Auth](docs/supabase-auth.md), turn off demo mode, and sign in.
2. Create and install the [GitHub App](docs/github-app.md). GitHub needs a secure
   tunnel to deliver webhooks to a local app; the guide explains the callback
   URLs too.
3. Connect GitHub in Splash, select a repository, and index it. Opening or
   updating a pull request starts an analysis.

Splash reads repository source. It doesn't execute that code, push changes, or
merge PRs. Removing a project deletes its source, analyses, and alerts from
Splash's active database. Reports you've downloaded remain on your computer.

Keep credentials in `.env`, `.secrets`, or private environment settings. The
local credential files are ignored by Git. OpenAI is optional; see
[privacy](docs/privacy.md) for what gets sent and [costs](docs/costs.md) for the
configured limits. The [security guide](docs/security.md) explains access checks
and data removal.

The local demo has been tested. Live GitHub, Supabase, and OpenAI connections
still need to be checked with your account settings; see the
[validation notes](docs/validation.md).

## Development

The web app uses React, TypeScript, and Vite. Python and FastAPI handle the API;
Celery runs background jobs through Redis. PostgreSQL 16 stores the data, with
pgvector for optional semantic search. LangGraph connects the analysis stages.

Follow [local development](docs/local-development.md) to install the Python and
Node dependencies and start services with reload. Then run:

```sh
make test
python3 scripts/smoke.py
cd tests/e2e && npm ci && npx playwright install chromium && npm test
```

The smoke check uses the running demo and writes results to `.splash/`. Add
`--verify-deletion` to check data removal too; that option deletes the sample
project.

See [architecture](docs/architecture.md) for the API and analysis flow, or the
[Render guide](docs/deployment.md) when you're ready to plan a hosted pilot.
