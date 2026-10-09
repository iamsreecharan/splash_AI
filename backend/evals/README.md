# Static HTTP contract evaluation

Run from `backend`:

```sh
python -m evals.run --output evals/report.json
```

The checked-in report measures 74 independent synthetic snapshot pairs. It
includes 32 response-field migrations across eight business fields and four
response binding forms, 32 safe additions/coordinated migrations/unrelated
endpoints or symbols, and 10 endpoint and ambiguity cases. Each case defines
its expected consumer path, affected field set, and alert count.

Precision counts only alerts matching the expected consumer and fields. Recall
counts those expected impacts found. Evidence faithfulness separately checks
every cited snippet against its exact source lines and supplied snapshot SHA.
These measurements establish behavior on this fixture corpus, not arbitrary
repository or runtime accuracy. Additional targeted pytest cases cover nested
fields, route prefixes, HTTP methods, privacy exclusions, incomplete snapshots,
local mutations, custom/shadowed fetch bindings, and guarded fallback reads.

The supported producer is an imported FastAPI/APIRouter with literal routes and
literal JSON dictionary returns (including a single, unmutated local binding).
The supported consumer is a repository-local literal or path-segment template
`fetch`, with an awaited response and awaited `json()` bound to a named value or
object destructuring. Dot/bracket fields and simple local aliases are tracked.
Response models, computed routes, unsupported syntax, duplicate endpoints,
opaque mutations, and reassigned/shadowed bindings cannot earn verification.
Known optional/nullish fallback reads are left unalerted. Axios wrappers,
Promise chains, dynamic HTTP clients, ORM/Pydantic shapes, and cross-repository
ownership need future parser coverage.

Search-only matches stay unalerted. Lexical exploration is capped at 20 change
queries per run; exact structural dependency checks still cover every recognized
change. Optional hosted explanations receive exact static evidence plus up to
three retrieved source/doc hints, each labelled unverified. With AI explicitly
enabled, the worker's bounded head-snapshot embedding sample can add semantic
context using one shared query embedding and reciprocal-rank fusion. Vector
records must match the indexed SHA, path, lines, and source prefix; stale records
are discarded. This small sample improves context, not exhaustive semantic
coverage. Model explanations cannot create, verify, or raise the severity of an
alert. No source code is imported or executed during analysis.
