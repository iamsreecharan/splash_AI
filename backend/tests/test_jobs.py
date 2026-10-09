from datetime import timedelta

from sqlalchemy import func, select

from app import main, tasks
from app.models import Alert, AnalysisRun, Installation, Repository, Snapshot, utcnow


def test_retry_and_duplicate_worker_delivery_preserve_one_result(client, db, monkeypatch):
    client.post("/api/demo/seed")
    from app.analysis import engine
    original = engine.analyze_change

    def fail(**kwargs):
        raise ValueError("sensitive-source-or-secret-should-never-appear")

    monkeypatch.setattr(engine, "analyze_change", fail)
    response = client.post("/api/demo/analyze", json={"scenario": "rename"})
    run_id = response.json()["job_id"]
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "failed"
    assert "sensitive-source" not in run["error"]
    monkeypatch.setattr(engine, "analyze_change", original)
    assert client.post(f"/api/runs/{run_id}/retry").status_code == 202
    assert client.get(f"/api/runs/{run_id}").json()["status"] == "completed"
    count = db.scalar(select(func.count()).select_from(Alert))
    tasks.process_run.run(run_id)
    assert db.scalar(select(func.count()).select_from(Alert)) == count
    assert client.post(f"/api/runs/{run_id}/retry").status_code == 409


def test_recovery_requeues_lost_worker_and_undispatched_jobs(client, db, monkeypatch):
    repo = client.post("/api/demo/seed").json()["repository_id"]
    queued = AnalysisRun(repository_id=repo, status="queued", payload={})
    lost = AnalysisRun(repository_id=repo, status="running", started_at=utcnow() - timedelta(minutes=21), lease_token="expired")
    recent = AnalysisRun(repository_id=repo, status="running", started_at=utcnow(), lease_token="active")
    db.add_all([queued, lost, recent])
    db.commit()
    dispatched = []
    monkeypatch.setattr(tasks, "enqueue_run", dispatched.append)
    tasks.recover_jobs.run()
    assert set(dispatched) == {queued.id, lost.id}
    db.expire_all()
    assert db.get(AnalysisRun, lost.id).status == "queued"
    assert db.get(AnalysisRun, lost.id).lease_token is None
    assert db.get(AnalysisRun, recent.id).status == "running"


def test_live_pr_uses_merge_base_and_user_permission_intersection(client, monkeypatch):
    repo = Repository(id="repo", full_name="owner/repo", default_branch="main", is_demo=False)
    installation = Installation(id="install", github_id=12, active=True, encrypted_user_token="ciphertext")
    seen = []

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def request(self, method, path):
            seen.append(path)
            return {"merge_base_commit": {"sha": "c" * 40}}

        def snapshot(self, name, sha):
            seen.append((name, sha))
            return {"api.py": "# baseline"}

    monkeypatch.setattr(tasks, "user_installation_client", lambda value: FakeClient())
    base, head, baseline_sha, head_sha = tasks.fetch_run_files(repo, installation,
        {"base_sha": "a" * 40, "head_sha": "b" * 40}, "analyze")
    assert baseline_sha == "c" * 40 and head_sha == "b" * 40
    assert ("owner/repo", "a" * 40) not in seen
    assert ("owner/repo", "c" * 40) in seen and ("owner/repo", "b" * 40) in seen
    assert any(isinstance(value, str) and "/compare/" in value for value in seen)


def test_credentials_and_unsupported_files_never_enter_persisted_snapshots(client, db):
    repo_id = client.post("/api/demo/seed").json()["repository_id"]
    repo = db.get(Repository, repo_id)
    source = {"api.py": "print('hello')", ".env": "PASSWORD=secret",
              "private_secrets.py": "password='secret'", "keys.py": "API_KEY='sk-abcdefghijklmnopqrstuvwxy'",
              "README.md": "# Architecture", "package.json": '{"name":"demo"}'}
    snapshot = tasks.stored_snapshot(db, repo, "f" * 40, source)
    db.commit()
    assert set(snapshot.files) == {"api.py", "README.md", "package.json"}


def test_snapshot_retention_prunes_old_raw_content(client, db, monkeypatch):
    repo_id = client.post("/api/demo/seed").json()["repository_id"]
    repo = db.get(Repository, repo_id)
    monkeypatch.setattr(tasks.settings, "max_snapshots_per_repository", 2)
    for i in range(4):
        tasks.stored_snapshot(db, repo, f"snapshot-{i}", {"api.py": f"VALUE = {i}"})
    db.commit()
    tasks.prune_snapshots(db, repo_id, {"snapshot-2", "snapshot-3"})
    db.commit()
    snapshots = db.scalars(select(Snapshot).where(Snapshot.repository_id == repo_id)).all()
    assert {snapshot.commit_sha for snapshot in snapshots} == {"snapshot-2", "snapshot-3"}


def test_worker_reuses_bounded_head_embeddings_and_one_provider_for_retrieval(client, monkeypatch):
    from app.retrieval import providers
    from app.retrieval.lexical import citation_for
    from app.analysis import engine
    client.post("/api/demo/seed")
    monkeypatch.setattr(tasks.settings, "ai_enabled", True)
    received = {}

    class Provider:
        reserved_usd = 0.001

        def embed_chunks(self, chunks):
            assert len(chunks) <= tasks.settings.ai_max_snippets
            return [{"citation": citation_for(chunk), "snippet": chunk["content"],
                     "embedding": [0.1] * 1536} for chunk in chunks if chunk["content"].strip()]

    provider = Provider()
    monkeypatch.setattr(providers.OpenAIProvider, "from_settings", lambda settings: provider)

    def analyze(**kwargs):
        received.update(kwargs)
        return {"alerts": [], "stages": [], "metrics": {}}

    monkeypatch.setattr(engine, "analyze_change", analyze)
    response = client.post("/api/demo/analyze", json={"scenario": "rename"})
    run = client.get(f"/api/runs/{response.json()['job_id']}").json()
    assert run["status"] == "completed"
    assert received["ai_provider"] is provider
    context = received["retrieval_context"]
    assert context and len(context) <= tasks.settings.ai_max_snippets
    assert all(item["commit_sha"] == received["head_sha"] for item in context)
    assert all(item["embedding"] and item["content"] and item["citation"]["path"] == item["path"] for item in context)
