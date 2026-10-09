import hashlib
import hmac
import json

from sqlalchemy import func, select

from app import main, tasks
from app.models import (AnalysisRun, ChangeEvent, Installation, Repository,
                        WebhookDelivery, Snapshot, Chunk, Alert)


def connect_repository(client, db):
    session = client.get("/api/session").json()
    installation = Installation(github_id=1234, organization_id=session["organization"]["id"],
                                user_id=session["user"]["id"], github_user_id=5678,
                                account_login="private-org")
    db.add(installation)
    db.flush()
    repo = Repository(organization_id=installation.organization_id, installation_id=installation.id,
                      github_id=9012, full_name="private-org/private-repo", default_branch="main")
    db.add(repo)
    db.commit()
    return installation, repo


def payload():
    return {"action": "synchronize", "number": 17, "installation": {"id": 1234},
            "repository": {"id": 9012, "full_name": "private-org/private-repo"},
            "pull_request": {"title": "Rename field", "draft": False,
                             "base": {"sha": "a" * 40}, "head": {"sha": "b" * 40}}}


def send(client, data, delivery="11111111-1111-1111-1111-111111111111", event="pull_request", signature=None):
    body = json.dumps(data, separators=(",", ":")).encode()
    signature = signature or "sha256=" + hmac.new(main.settings.github_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post("/api/webhooks/github", content=body,
                       headers={"X-Hub-Signature-256": signature, "X-GitHub-Delivery": delivery,
                                "X-GitHub-Event": event, "Content-Type": "application/json"})


def test_signature_checked_before_persistence_and_job_enqueue(client, db, monkeypatch):
    connect_repository(client, db)
    dispatched = []
    monkeypatch.setattr(main, "enqueue_run", dispatched.append)
    invalid = send(client, payload(), signature="sha256=invalid")
    assert invalid.status_code == 401
    assert not dispatched and db.scalar(select(func.count()).select_from(WebhookDelivery)) == 0
    valid = send(client, payload())
    assert valid.status_code == 202
    assert len(dispatched) == 1
    run = db.get(AnalysisRun, valid.json()["job_id"])
    assert run.payload["base_sha"] == "a" * 40 and run.payload["head_sha"] == "b" * 40
    duplicate = send(client, payload())
    assert duplicate.status_code == 200 and duplicate.json()["job_id"] == run.id
    assert len(dispatched) == 1
    repeat_event = send(client, payload(), delivery="22222222-2222-2222-2222-222222222222")
    assert repeat_event.status_code == 202 and repeat_event.json()["job_id"] == run.id
    assert db.scalar(select(func.count()).select_from(AnalysisRun)) == 1
    assert db.scalar(select(func.count()).select_from(ChangeEvent)) == 1


def test_failed_enqueue_is_never_acknowledged_and_redelivery_recovers(client, db, monkeypatch):
    connect_repository(client, db)

    def unavailable(run_id):
        raise tasks.EnqueueError("Queue unavailable")

    monkeypatch.setattr(main, "enqueue_run", unavailable)
    for _ in range(2):
        response = send(client, payload())
        assert response.status_code == 503
    db.expire_all()
    delivery = db.scalar(select(WebhookDelivery))
    assert delivery.status == "received" and delivery.run_id
    dispatched = []
    monkeypatch.setattr(main, "enqueue_run", dispatched.append)
    response = send(client, payload())
    assert response.status_code == 200 and len(dispatched) == 1
    db.expire_all()
    assert db.get(WebhookDelivery, delivery.delivery_id).status == "enqueued"
    assert db.scalar(select(func.count()).select_from(AnalysisRun)) == 1


def test_delivery_id_cannot_be_reused_with_changed_signed_payload(client, db, monkeypatch):
    connect_repository(client, db)
    monkeypatch.setattr(main, "enqueue_run", lambda _: None)
    assert send(client, payload()).status_code == 202
    changed = payload()
    changed["number"] = 18
    assert send(client, changed).status_code == 409
    assert send(client, payload(), event="push").status_code == 409


def test_unknown_installation_and_draft_never_enqueue(client, db, monkeypatch):
    connect_repository(client, db)
    dispatched = []
    monkeypatch.setattr(main, "enqueue_run", dispatched.append)
    unknown = payload()
    unknown["installation"]["id"] = 98765
    assert send(client, unknown).json()["status"] == "ignored"
    draft = payload()
    draft["pull_request"]["draft"] = True
    assert send(client, draft, delivery="draft-delivery").json()["status"] == "ignored"
    assert not dispatched


def test_permission_removal_erases_only_that_installations_repository(client, db):
    installation, repo = connect_repository(client, db)
    repo_id = repo.id
    demo_id = client.post("/api/demo/seed").json()["repository_id"]
    data = {"installation": {"id": installation.github_id}, "repositories_removed": [{"id": repo.github_id}]}
    assert send(client, data, event="installation_repositories").status_code == 200
    db.expire_all()
    assert db.get(Repository, repo_id) is None and db.get(Repository, demo_id) is not None


def test_uninstall_erases_prior_derived_data_and_prevents_new_work(client, db, monkeypatch):
    installation, repo = connect_repository(client, db)
    from app.analysis.fixtures import BASE_FILES
    tasks.stored_snapshot(db, repo, "a" * 40, BASE_FILES)
    db.commit()
    monkeypatch.setattr(main, "enqueue_run", lambda _: None)
    assert send(client, payload()).status_code == 202
    data = {"action": "deleted", "installation": {"id": installation.github_id}}
    assert send(client, data, delivery="uninstall-delivery", event="installation").json()["status"] == "disconnected"
    db.expire_all()
    assert not db.get(Installation, installation.id).active
    for model in (Repository, Snapshot, Chunk, AnalysisRun, ChangeEvent, Alert):
        assert db.scalar(select(func.count()).select_from(model)) == 0
    assert send(client, payload(), delivery="after-uninstall").json()["status"] == "ignored"


def test_webhook_size_is_bounded_before_hmac(client, monkeypatch):
    monkeypatch.setattr(main.settings, "max_webhook_bytes", 10)
    assert send(client, payload()).status_code == 413
