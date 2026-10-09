# Code handling and optional hosted AI

The default local demo uses fixtures and makes no hosted model requests. Live
indexing fetches source only from selected authorized GitHub installations.
Splash reads source and diffs as data; it does not execute repository code,
install a repository's dependencies, or run its build scripts.

## Stored data

PostgreSQL stores repository identifiers, parsed source chunks, dependencies,
analysis runs, evidence snippets, alerts, and feedback. GitHub user authorization
tokens are encrypted using the deployment's Fernet key. App private keys,
webhook secrets, Supabase session tokens, and OpenAI keys should live in private
configuration and never in source control. Redis stores queued work and task
metadata. Backups of either service can retain deleted information.

Credential-style filenames, common generated/vendor directories, binary files,
oversized files, and unsupported languages are excluded during indexing. These
filters reduce accidental disclosure; code can still contain inline secrets or
personal data. Review repositories before allowing any hosted processing.

## OpenAI opt-in

Set `SPLASH_AI_ENABLED=true` only after setting `SPLASH_OPENAI_API_KEY` in the
API and worker's private environment. Hosted analysis may send bounded change
summaries, selected changed symbols/diffs, and retrieved source excerpts to
OpenAI for explanation. When AI is enabled, up to eight bounded source chunks
per snapshot are sent for embedding and vectors are stored in PostgreSQL/pgvector.
Source paths,
symbol names, and the included code can disclose private project details.
The repository is not submitted as an executable project or granted tool access.

Optional hybrid search can also send one change query per analysis for embedding:
up to 1,000 characters of endpoint names, changed symbols, and removed-field
names. It searches the bounded sample of embedded chunks, combines those results
with local lexical retrieval, and adds up to three relevant source/documentation
excerpts to the explanation. These excerpts are unverified contextual hints;
they do not establish a dependency. Query embeddings, chunk embeddings, and
explanations share the same job allowance.

Disabling AI prevents new hosted model requests. It does not retract previous
requests or erase a provider's retained records. Provider retention, processing
region, and contractual controls depend on your OpenAI account and current API
terms; review them before private pilot use. Do not assume that API usage is
free or that data-retention guarantees follow from a personal chat subscription.

The deterministic analysis path remains available without the provider. AI
output is advisory: source citations and structural checks govern evidence
status, and a person decides what action to take. Splash does not push fixes,
merge changes, or notify external chat channels in V1.

## Removal

Use the repository removal action to delete that repository's indexed data,
runs, events, alerts, and feedback from the application's active database. This
does not delete the repository on GitHub, revoke the App there, remove external
provider records, or rewrite existing backups. Uninstall the GitHub App when
you want to revoke its access. Decommissioning a local demo can use
`docker compose down -v` to permanently remove its Docker volumes. Apply your
own backup retention and purge policy before retaining real private source.

By default, raw snapshot/chunk retention targets the latest 10 snapshots per
repository; the current analysis's base/head snapshots are protected, so older
protected snapshots may temporarily exceed that target. Evidence snippets and
commit references in historical analysis
alerts remain until repository removal. Expiring a snapshot is therefore not
equivalent to deleting every copy of its source text from the application's
history. Adjust `SPLASH_MAX_SNAPSHOTS_PER_REPOSITORY` only with a reviewed
retention policy.
