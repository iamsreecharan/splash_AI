"""Durable Celery jobs with database records, idempotent claims, and recovery.

Redis is transport, PostgreSQL is the source of truth for jobs and results. Run
the included Celery beat recovery schedule to redispatch work after worker loss.
"""
import logging
import hashlib
import json
from datetime import timedelta, timezone
from uuid import uuid4

from celery import Celery
from sqlalchemy import delete, select, update

from app.config import get_settings
from app.db import session_factory
from app.github import GitHubError, user_installation_client
from app.models import (Alert, AnalysisRun, Chunk, Dependency, Installation,
                        Repository, Snapshot, utcnow)

logger = logging.getLogger(__name__)
settings = get_settings()
celery_app = Celery("splash", broker=settings.redis_url)
celery_app.conf.update(
    task_serializer="json", accept_content=["json"], result_serializer="json",
    task_ignore_result=True, task_acks_late=True, task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1, broker_connection_retry_on_startup=True,
    task_always_eager=settings.celery_always_eager,
    task_eager_propagates=True,
    task_soft_time_limit=840, task_time_limit=900,
    broker_transport_options={"visibility_timeout": 1200},
    beat_schedule={"recover-persisted-jobs": {"task": "splash.recover_jobs", "schedule": 60.0}},
)


class EnqueueError(Exception):
    pass


def demo_snapshot_id(files: dict) -> str:
    canonical = json.dumps(files, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return "demo-" + hashlib.sha256(canonical).hexdigest()[:40]


def enqueue_run(run_id: str):
    try:
        process_run.apply_async(args=[run_id], task_id=run_id, retry=False)
    except Exception:
        # The durable queued row stays available to the recovery schedule.
        raise EnqueueError("Background queue is unavailable. The job is saved; retry after Redis and workers are ready.") from None
    with session_factory()() as db:
        db.execute(update(AnalysisRun).where(AnalysisRun.id == run_id)
                   .values(dispatched_at=utcnow()))
        db.commit()


def stored_snapshot(db, repository: Repository, sha: str, files: dict):
    from app.ingestion.indexer import index_files
    from app.ingestion.paths import source_files
    files = source_files(files, strict=True)
    existing = db.scalar(select(Snapshot).where(Snapshot.repository_id == repository.id,
                                                Snapshot.commit_sha == sha))
    if existing:
        return existing
    indexed = index_files(files, repository_name=repository.full_name, commit_sha=sha)
    snapshot = Snapshot(repository_id=repository.id, commit_sha=sha, files=files,
                        file_count=len(files))
    db.add(snapshot)
    db.flush()
    for item in indexed.get("chunks", []):
        db.add(Chunk(repository_id=repository.id, snapshot_id=snapshot.id,
                     path=item["path"], start_line=item["start_line"], end_line=item["end_line"],
                     content=item["content"], symbol=item.get("symbol", "") or "",
                     kind=item.get("kind", "") or ""))
    for item in indexed.get("dependencies", []):
        db.add(Dependency(repository_id=repository.id, snapshot_id=snapshot.id, data=item))
    db.flush()
    return snapshot


def prune_snapshots(db, repository_id: str, protected: set[str]):
    snapshots = db.scalars(select(Snapshot).where(Snapshot.repository_id == repository_id)
                          .order_by(Snapshot.created_at.desc())).all()
    # Alert citations retain SHA/snippet after raw snapshot expiry.
    limit = max(2, settings.max_snapshots_per_repository)
    for snapshot in snapshots[limit:]:
        if snapshot.commit_sha not in protected:
            db.delete(snapshot)


def fetch_run_files(repository: Repository, installation: Installation | None, payload: dict, kind: str):
    if repository.is_demo:
        from app.analysis.fixtures import BASE_FILES, RENAME_FILES, SAFE_FILES
        base = BASE_FILES
        head = SAFE_FILES if payload.get("scenario") == "safe" else RENAME_FILES
        return base, head, demo_snapshot_id(base), demo_snapshot_id(head)
    if not installation or not installation.active:
        raise GitHubError("This GitHub installation is disconnected. Reconnect it before indexing.")
    # GitHub App user-to-server authorization is the intersection of the App's
    # grant and the installing user's *current* permissions. An installation
    # token alone would retain access after that user lost organization access.
    with user_installation_client(installation) as client:
        if kind == "index":
            # Resolve a branch once; every content fetch thereafter is immutable.
            import urllib.parse
            ref = urllib.parse.quote(repository.default_branch, safe="")
            sha = payload.get("commit_sha")
            if not sha:
                commit = client.request("GET", f"/repos/{repository.full_name}/commits/{ref}")
                sha = commit["sha"]
            files = client.snapshot(repository.full_name, sha)
            return files, files, sha, sha
        base_sha, head_sha = payload["base_sha"], payload["head_sha"]
        # A PR diff starts at the merge base, rather than the current target tip:
        # unrelated commits on main must not look like deletions in this PR.
        import re
        if not re.fullmatch(r"[a-f0-9]{40}", base_sha) or not re.fullmatch(r"[a-f0-9]{40}", head_sha):
            raise GitHubError("Pull request analysis requires immutable base and head commit SHAs.")
        comparison = client.request("GET", f"/repos/{repository.full_name}/compare/{base_sha}...{head_sha}")
        merge_base = (comparison.get("merge_base_commit") or {}).get("sha", "")
        if not re.fullmatch(r"[a-f0-9]{40}", merge_base):
            raise GitHubError("GitHub did not provide the PR's immutable merge base; no partial analysis was stored.")
        # PR merge refs make fork commits available through the base repository.
        # Failure is explicit; we never fall back to the default branch.
        return (client.snapshot(repository.full_name, merge_base),
                client.snapshot(repository.full_name, head_sha), merge_base, head_sha)


@celery_app.task(name="splash.process_run", bind=True)
def process_run(self, run_id: str):
    token = str(uuid4())
    with session_factory()() as db:
        # Atomic update is an idempotency claim, including in SQLite unit tests.
        claimed = db.execute(update(AnalysisRun).where(AnalysisRun.id == run_id,
                                                       AnalysisRun.status == "queued")
                             .values(status="running", started_at=utcnow(), lease_token=token, error=None))
        db.commit()
        if not claimed.rowcount:
            return
        run = db.get(AnalysisRun, run_id)
        if not run:
            return
        repository = db.get(Repository, run.repository_id)
        if not repository or not repository.active:
            run.status, run.error, run.completed_at = "failed", "Repository was disconnected.", utcnow()
            db.commit()
            return
        installation = db.get(Installation, repository.installation_id) if repository.installation_id else None
        payload, kind, repo_id = dict(run.payload), run.kind, repository.id
        if kind == "index":
            repository.status, repository.error = "indexing", None
            db.commit()
        # Detach to avoid an idle DB transaction throughout GitHub requests.
        db.expunge(repository)
        if installation:
            db.expunge(installation)
    try:
        base, head, base_sha, head_sha = fetch_run_files(repository, installation, payload, kind)
        provider = None
        embeddings = {}
        provider_note = None
        if settings.ai_enabled:
            from app.retrieval.providers import OpenAIProvider
            from app.ingestion.indexer import index_files
            provider = OpenAIProvider.from_settings(settings)
            # A deliberately bounded sample, sharing one cost allowance with
            # reasoning. Hosted failures never prevent deterministic analysis.
            try:
                for sha, files in ({base_sha: base, head_sha: head} if kind == "analyze" else {base_sha: base}).items():
                    chunks = index_files(files, repository_name=repository.full_name, commit_sha=sha)["chunks"]
                    embeddings[sha] = provider.embed_chunks(chunks[:settings.ai_max_snippets])
            except Exception as exc:
                provider_note = f"Optional hosted embeddings unavailable ({type(exc).__name__}); local analysis remains available."
        if kind == "analyze":
            from app.analysis.engine import analyze_change
            retrieval_context = [{**record["citation"], "citation": record["citation"],
                                  "content": record["snippet"], "snippet": record["snippet"],
                                  "embedding": record["embedding"]}
                                 for record in embeddings.get(head_sha, [])]
            result = analyze_change(repository_name=repository.full_name, base_files=base, head_files=head,
                                    pr_number=payload.get("pr_number", 1), title=payload.get("title", ""),
                                    base_sha=base_sha, head_sha=head_sha, ai_provider=provider,
                                    retrieval_context=retrieval_context)
        else:
            result = {"alerts": [], "stages": [{"name": "index", "status": "completed",
                      "summary": f"Indexed immutable commit {base_sha[:8]}", "duration_ms": 0}], "metrics": {}}
        with session_factory()() as db:
            # Same lock order as revocation: installation -> repository -> run.
            # Revocation either waits for a completed transaction or wins before
            # any new source/results can be persisted.
            current_installation = db.scalar(select(Installation).where(Installation.id == installation.id).with_for_update()) if installation else None
            repo = db.scalar(select(Repository).where(Repository.id == repo_id).with_for_update())
            run = db.scalar(select(AnalysisRun).where(AnalysisRun.id == run_id).with_for_update())
            if not run or not repo or run.lease_token != token or run.status != "running" or not repo.active or (installation and (not current_installation or not current_installation.active)):
                return
            base_snapshot = stored_snapshot(db, repo, base_sha, base)
            if kind == "analyze":
                stored_snapshot(db, repo, head_sha, head)
            else:
                from sqlalchemy import func
                repo.indexed_files = base_snapshot.file_count
                repo.indexed_chunks = db.scalar(select(func.count()).select_from(Chunk)
                                                 .where(Chunk.snapshot_id == base_snapshot.id))
                repo.last_indexed_at, repo.status, repo.error = utcnow(), "ready", None
            if embeddings:
                from app.retrieval.providers import PgVectorStore
                for sha, records in embeddings.items():
                    if db.bind.dialect.name == "postgresql":
                        PgVectorStore(db).upsert(records, organization_id=repo.organization_id,
                                                repository_id=repo.id, commit_sha=sha)
            # Previous persisted results cannot exist unless a completed job was
            # explicitly requeued. Replacing only this run keeps retries idempotent.
            db.execute(delete(Alert).where(Alert.run_id == run_id))
            for item in result.get("alerts", []):
                evidence = item.get("evidence", [])
                if repo.is_demo:
                    evidence = [{**citation, "source_url": ""} for citation in evidence]
                db.add(Alert(repository_id=repo_id, run_id=run_id,
                             title=item["title"], summary=item["summary"],
                             severity=item.get("severity", "medium"), confidence=item.get("confidence", 0.5),
                             evidence_status=item.get("evidence_status", "needs_investigation"),
                             pr_number=run.pr_number, pr_title=run.title,
                             pr_url=payload.get("pr_url", item.get("pr_url", "")),
                             affected_component=item.get("affected_component", ""),
                             evidence=evidence, recommendations=item.get("recommendations", []),
                             change_type=item.get("change_type", ""), ai_explanation=item.get("ai_explanation")))
            run.stages, run.metrics = result.get("stages", []), result.get("metrics", {})
            run.metrics = {**run.metrics, "analysis_base_sha": base_sha, "analysis_head_sha": head_sha,
                           "event_base_sha": payload.get("base_sha", base_sha)}
            if provider_note:
                run.metrics = {**run.metrics, "hosted_embeddings": provider_note}
            if provider:
                run.metrics = {**run.metrics, "estimated_hosted_usd": provider.reserved_usd,
                               "embedding_chunks": sum(len(rows) for rows in embeddings.values())}
            run.alert_count, run.status, run.completed_at = len(result.get("alerts", [])), "completed", utcnow()
            prune_snapshots(db, repo_id, {base_sha, head_sha})
            db.commit()
    except Exception as exc:
        # Do not put upstream response bodies, tokens, source code, or arbitrary
        # exception strings in logs or the user-visible error field.
        message = str(exc) if isinstance(exc, GitHubError) else "Analysis failed. Check worker readiness and supported source files, then retry."
        logger.error("Run %s failed (%s)", run_id, type(exc).__name__)
        with session_factory()() as db:
            run = db.get(AnalysisRun, run_id)
            if run and run.lease_token == token:
                run.status, run.completed_at, run.error = "failed", utcnow(), message
                repo = db.get(Repository, repo_id)
                if repo and kind == "index":
                    repo.status, repo.error = "failed", message
                db.commit()


@celery_app.task(name="splash.recover_jobs")
def recover_jobs():
    """Recover queued dispatch failures and expired worker leases."""
    now = utcnow()
    ids = []
    with session_factory()() as db:
        # Bound each sweep; later sweeps handle the remaining records.
        runs = db.scalars(select(AnalysisRun).where(AnalysisRun.status.in_(["queued", "running"]))
                          .order_by(AnalysisRun.created_at).limit(100)).all()
        for run in runs:
            started = run.started_at.replace(tzinfo=run.started_at.tzinfo or timezone.utc) if run.started_at else None
            dispatched = run.dispatched_at.replace(tzinfo=run.dispatched_at.tzinfo or timezone.utc) if run.dispatched_at else None
            if run.status == "running" and started and started < now - timedelta(minutes=20):
                run.status, run.lease_token, run.dispatched_at = "queued", None, None
                ids.append(run.id)
            elif run.status == "queued" and (not dispatched or dispatched < now - timedelta(minutes=10)):
                ids.append(run.id)
        db.commit()
    for run_id in ids:
        try:
            enqueue_run(run_id)
        except EnqueueError:
            # One unavailable broker means all later dispatches would fail too.
            break
    return len(ids)
