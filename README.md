# Splash

Splash helps you find the other code a pull request might break.

For example, a backend PR renames a JSON field from `temperature` to
`current_temperature`, but the frontend still reads `machine.temperature`.
Splash points out the mismatch and shows the source lines on both sides, so you
can check the finding yourself.

The app runs locally. You can try the sample project without creating accounts
or paying for an AI API. GitHub and OpenAI connections are optional setup steps.

## Try it

You'll need Git, Make, Docker with Compose v2, and Python 3 for the smoke check.
The commands below use a Linux, macOS, or WSL shell. Allow about 4 GB of free
memory for the containers.

```sh
git clone https://github.com/iamsreecharan/splash_AI.git
cd splash_AI
make up
```

Open **http://localhost:5173** on your computer and choose **Try sample project**.
Try the field rename, then the harmless change. Both go through the real indexer
and analysis worker using the bundled source files.

In **Analysis History**, you can find older runs by project, status, title, or PR
number. Open a completed analysis and choose **Download report** to save its
findings and source excerpts as Markdown. Reports also record when a supported
analysis found no impacts.

`make up` creates `.env` if you don't have one, builds the app, and starts the
database, queue, API, background workers, and web server. It keeps an existing
`.env`. Services bind to localhost. Keep demo mode on your own machine; configure
authentication before exposing the app to other people.

```sh
make smoke               # check the running API and analysis worker
make logs                # watch service logs
make down                # stop the app and keep its data
```

To start over completely, `docker compose down -v` deletes this stack's database
and queue volumes.

## What's working

- A dashboard for connected projects, alerts, and recent activity.
- A read-only GitHub App connection, repository indexing, and PR webhooks.
- Python and JavaScript/TypeScript source indexing, with source and documentation
  search and optional semantic retrieval through pgvector.
- Background analysis that checks changes, follows supported dependencies,
  verifies evidence, and suggests what to review.
- Alerts with exact source excerpts, useful/irrelevant feedback, and resolution
  tracking.
- Searchable analysis history and downloadable Markdown reports.
- Supabase sign-in, separate user workspaces, and repository data deletion.
- Optional OpenAI explanations with request limits. Paid AI is off by default.

The first release focuses on FastAPI routes and JSON response fields consumed
through literal JavaScript/TypeScript `fetch` calls. Dynamic URLs, client
wrappers, other frameworks, and many other kinds of breaking changes aren't
covered yet. A completed run with no findings doesn't prove that a PR is safe.

Linear, Slack, chat, suggested patches, and editor extensions are still planned.

## Use your own repositories

1. Set up [Supabase Auth](docs/supabase-auth.md), turn off demo mode, and sign in.
2. Create and install the [GitHub App](docs/github-app.md). GitHub needs a secure
   tunnel to deliver webhooks to your local app. Follow the guide for callback
   URLs.
3. Connect GitHub in Splash, select a repository, and index it. Open or update a
   pull request to start an analysis.

The app reads repository contents and PRs. It doesn't execute your code, push
changes, or merge pull requests. Removing a project deletes its stored source,
analysis history, and alerts from Splash's active database. Downloaded reports
are separate files; delete those yourself if you no longer need them.

Store credentials in `.env`, `.secrets`, or private environment settings. These
local files are ignored by Git. See [privacy](docs/privacy.md) for what optional
AI calls send, [costs](docs/costs.md) for the limits, and
[security](docs/security.md) for the access model.

## Work on the code

The frontend uses React, TypeScript, and Vite. The backend uses FastAPI,
PostgreSQL 16 with pgvector, Redis, and Celery. LangGraph connects the four
analysis stages.

Follow [local development](docs/local-development.md) to install Python and Node
dependencies and run services with reload. Then run:

```sh
make test
python3 scripts/smoke.py
cd tests/e2e && npm ci && npx playwright install chromium && npm test
```

The smoke check uses the running local demo and saves its results in `.splash/`.
Add `--verify-deletion` to check that removing the sample also erases its stored
data. That option deletes the sample project.

[Architecture](docs/architecture.md) describes the API and analysis flow.
[Validation notes](docs/validation.md) list what has been tested and what still
needs real account configuration. The local workflow is tested; a live GitHub,
Supabase, and OpenAI setup still needs to be checked with your account settings.

## Hosting later

Start locally to keep costs down. The [Render guide](docs/deployment.md) covers
a hosted pilot, including the database, queue, workers, and authentication.
Running the whole stack reliably on free hosting isn't assumed, and this repo
doesn't create cloud resources for you.
