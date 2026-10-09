from sqlalchemy import func, select

from app.auth import Identity, current_identity
from app.main import app
from app.models import (Alert, AnalysisRun, ChangeEvent, Chunk, Dependency, Feedback,
                        Organization, Repository, Snapshot, User)


def test_demo_is_real_analysis_idempotent_index_and_safe_control(client, db):
    assert client.get("/health").status_code == 200
    first = client.post("/api/demo/seed").json()
    second = client.post("/api/demo/seed").json()
    assert first["repository_id"] == second["repository_id"]
    repo_id = first["repository_id"]
    assert db.scalar(select(func.count()).select_from(Snapshot)) == 1
    chunks = db.scalar(select(func.count()).select_from(Chunk))
    assert chunks > 0
    status = client.get(f"/api/repositories/{repo_id}/status").json()
    assert status["status"] == "ready" and status["indexed_chunks"] == chunks

    breaking = client.post("/api/demo/analyze", json={"scenario": "rename"})
    assert breaking.status_code == 202
    run = client.get(f"/api/runs/{breaking.json()['job_id']}").json()
    assert run["status"] == "completed", run
    assert len(run["stages"]) == 4 and run["alert_count"] >= 1
    alerts = client.get("/api/alerts").json()["items"]
    high = [alert for alert in alerts if alert["severity"] == "high"]
    assert high and high[0]["evidence_status"] == "verified"
    assert high[0]["pr_number"] == 42
    assert all(item["commit_sha"].startswith("demo-") for item in high[0]["evidence"])
    assert any("temperature" in item["snippet"] for item in high[0]["evidence"])
    assert all(not item["source_url"] for item in high[0]["evidence"])
    assert not high[0]["pr_url"]
    safe = client.post("/api/demo/analyze", json={"scenario": "safe"})
    safe_run = client.get(f"/api/runs/{safe.json()['job_id']}").json()
    assert safe_run["status"] == "completed" and safe_run["alert_count"] == 0
    overview = client.get("/api/overview").json()
    assert overview["repositories"] == 1 and overview["analyzed_changes"] == 2
    assert overview["high_severity_alerts"] >= 1


def test_feedback_filters_and_complete_repository_erasure(client, db):
    repo = client.post("/api/demo/seed").json()["repository_id"]
    client.post("/api/demo/analyze", json={"scenario": "rename"})
    alert = client.get("/api/alerts").json()["items"][0]
    aid = alert["id"]
    useful = client.post(f"/api/alerts/{aid}/feedback", json={"kind": "useful"}).json()
    assert useful["feedback"] == "useful" and useful["status"] == "open"
    resolved = client.post(f"/api/alerts/{aid}/feedback", json={"kind": "resolved"}).json()
    assert resolved["status"] == "resolved"
    assert client.get("/api/alerts?status=open").json()["items"] == []
    dismissed = client.post(f"/api/alerts/{aid}/feedback", json={"kind": "irrelevant"}).json()
    assert dismissed["status"] == "dismissed"
    assert client.post(f"/api/alerts/{aid}/feedback", json={"kind": "anything"}).status_code == 422
    assert client.delete(f"/api/repositories/{repo}").status_code == 200
    db.expire_all()
    for model in (Repository, Snapshot, Chunk, Dependency, ChangeEvent, AnalysisRun, Alert, Feedback):
        assert db.scalar(select(func.count()).select_from(model)) == 0
    assert client.get(f"/api/alerts/{aid}").status_code == 404


def test_every_object_route_and_list_is_tenant_scoped(client, db):
    repo = client.post("/api/demo/seed").json()["repository_id"]
    run_id = client.post("/api/demo/analyze", json={"scenario": "rename"}).json()["job_id"]
    alert_id = client.get("/api/alerts").json()["items"][0]["id"]
    org = Organization(name="Second private workspace")
    db.add(org)
    db.flush()
    user = User(id="00000000-0000-4000-8000-000000000009", name="Other user", email="other@example.test", organization_id=org.id)
    db.add(user)
    db.commit()
    identity = Identity(user.id, org.id, user.name, user.email, org.name, False)
    app.dependency_overrides[current_identity] = lambda: identity
    assert client.get("/api/repositories").json()["items"] == []
    assert client.get("/api/runs").json()["items"] == []
    assert client.get("/api/alerts").json()["items"] == []
    assert client.get("/api/overview").json()["open_alerts"] == 0
    checks = [("get", f"/api/repositories/{repo}/status", None),
              ("post", f"/api/repositories/{repo}/index", None),
              ("delete", f"/api/repositories/{repo}", None),
              ("get", f"/api/repositories/{repo}/source?sha={'a' * 40}&path=api/machines.py", None),
              ("get", f"/api/alerts/{alert_id}", None),
              ("post", f"/api/alerts/{alert_id}/feedback", {"kind": "resolved"}),
              ("get", f"/api/runs/{run_id}", None),
              ("post", f"/api/runs/{run_id}/retry", None),
              ("get", f"/api/alerts?repository_id={repo}", None),
              ("post", "/api/demo/seed", None),
              ("post", "/api/demo/analyze", {"scenario": "rename"})]
    for method, path, body in checks:
        response = getattr(client, method)(path, **({"json": body} if body else {}))
        assert response.status_code == 404, (method, path, response.text)
    assert db.get(Repository, repo) is not None


def test_source_endpoint_serves_only_saved_exact_snapshot(client):
    from app.analysis.fixtures import BASE_FILES
    from app.tasks import demo_snapshot_id
    repo = client.post("/api/demo/seed").json()["repository_id"]
    sha = demo_snapshot_id(BASE_FILES)
    source = client.get(f"/api/repositories/{repo}/source", params={"sha": sha, "path": "api/machines.py"})
    assert source.status_code == 200 and '"temperature": 72.4' in source.json()["content"]
    for path in ["../../etc/passwd", ".env", "/etc/passwd"]:
        assert client.get(f"/api/repositories/{repo}/source", params={"sha": sha, "path": path}).status_code == 404
    assert client.get(f"/api/repositories/{repo}/source", params={"sha": "main", "path": "api/machines.py"}).status_code == 404


def test_public_config_never_exposes_credentials(client, monkeypatch):
    from app import main
    monkeypatch.setattr(main.settings, "github_client_secret", "private-client-secret")
    monkeypatch.setattr(main.settings, "github_webhook_secret", "private-webhook-secret")
    monkeypatch.setattr(main.settings, "openai_api_key", "private-openai-key")
    response = client.get("/api/public/config")
    for secret in ["private-client-secret", "private-webhook-secret", "private-openai-key"]:
        assert secret not in response.text
    assert response.json()["demo_mode"] is True
