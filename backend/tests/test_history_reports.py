from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from app import main
from app.auth import Identity, current_identity
from app.github import GitHubError
from app.models import (Alert, AnalysisRun, Installation, Organization, Repository,
                        Snapshot, User, utcnow)
from app.reports import identifier, report_filename, source_block


def add_run(db, repo, *, title="Analyze change", status="completed", kind="analyze",
            pr_number=42, created_at=None, identifier=None):
    run = AnalysisRun(repository_id=repo.id, title=title, status=status, kind=kind,
                      pr_number=pr_number, created_at=created_at or utcnow(),
                      completed_at=utcnow() if status == "completed" else None,
                      metrics={"analysis_base_sha": "a" * 40, "analysis_head_sha": "b" * 40},
                      payload={})
    if identifier:
        run.id = identifier
    db.add(run)
    db.flush()
    return run


def sample_repository(client, db):
    repo_id = client.post("/api/demo/seed").json()["repository_id"]
    return db.get(Repository, repo_id)


def test_history_stable_pagination_count_and_backward_compatible_defaults(client, db):
    repo = sample_repository(client, db)
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    runs = [add_run(db, repo, created_at=timestamp,
                    identifier=f"00000000-0000-4000-8000-{number:012x}") for number in range(1, 5)]
    db.commit()
    default = client.get("/api/runs").json()
    assert default["total"] == 4 and default["limit"] == 100 and default["offset"] == 0
    assert default["has_more"] is False
    first = client.get("/api/runs", params={"limit": 2}).json()
    second = client.get("/api/runs", params={"limit": 2, "offset": 2}).json()
    assert [row["id"] for row in first["items"] + second["items"]] == [run.id for run in reversed(runs)]
    assert first["has_more"] is True and second["has_more"] is False
    assert first["total"] == second["total"] == 4
    empty = client.get("/api/runs", params={"offset": 20}).json()
    assert empty["items"] == [] and empty["total"] == 4 and empty["has_more"] is False
    assert first["items"][0]["kind"] == "analyze"
    assert first["items"][0]["base_sha"] == "a" * 40
    assert first["items"][0]["head_sha"] == "b" * 40


def test_history_filters_combine_and_search_is_literal_case_insensitive(client, db):
    repo = sample_repository(client, db)
    docs = Repository(organization_id=repo.organization_id, full_name="pilot/Docs", is_demo=True)
    db.add(docs)
    db.flush()
    special = add_run(db, repo, title="Special 50% _under_ \\backslash", status="failed", pr_number=1234)
    queued = add_run(db, repo, title="Waiting for worker", status="queued", pr_number=45)
    completed = add_run(db, docs, title="ALPHA release", status="completed", pr_number=46)
    index = add_run(db, docs, title="ALPHA index", status="completed", kind="index", pr_number=None)
    db.commit()
    for term in ["%", "_under_", "\\backslash", "sPeCiAl", "1234"]:
        response = client.get("/api/runs", params={"q": term}).json()
        assert response["total"] == 1 and response["items"][0]["id"] == special.id
    assert client.get("/api/runs", params={"q": "dOcS"}).json()["total"] == 2
    combined = client.get("/api/runs", params={"repository_id": docs.id, "q": "alpha",
                                               "status": "completed", "kind": "analyze"}).json()
    assert combined["total"] == 1 and combined["items"][0]["id"] == completed.id
    assert client.get("/api/runs", params={"status": "queued"}).json()["items"][0]["id"] == queued.id
    assert client.get("/api/runs", params={"kind": "index"}).json()["items"][0]["id"] == index.id


@pytest.mark.parametrize("parameters", [
    {"limit": 0}, {"limit": 501}, {"offset": -1}, {"offset": 1_000_001},
    {"q": "x" * 201}, {"q": "\x00"}, {"status": "unknown"}, {"kind": "unknown"},
])
def test_history_rejects_invalid_or_unbounded_inputs(client, parameters):
    assert client.get("/api/runs", params=parameters).status_code == 422


def live_repository(client, db, monkeypatch):
    demo = sample_repository(client, db)
    user = client.get("/api/session").json()["user"]
    installation = Installation(github_id=91, organization_id=demo.organization_id,
                                user_id=user["id"], github_user_id=92, account_login="pilot")
    db.add(installation)
    db.flush()
    live = Repository(organization_id=demo.organization_id, installation_id=installation.id,
                      github_id=93, full_name="pilot/private", is_demo=False)
    db.add(live)
    db.flush()
    access = {"ids": {93}, "expired": False}

    class UserClient:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def paginated(self, path, key):
            if access["expired"]:
                raise GitHubError("Reconnect GitHub to authorize access.")
            return [{"id": value} for value in access["ids"]]

    monkeypatch.setattr(main, "user_installation_client", lambda value: UserClient())
    return demo, live, access


def test_revoked_visibility_is_applied_before_history_count_page_and_search(client, db, monkeypatch):
    demo, live, access = live_repository(client, db, monkeypatch)
    visible = add_run(db, demo, title="Visible older analysis", created_at=utcnow() - timedelta(days=1))
    hidden = add_run(db, live, title="Confidential recent analysis")
    db.commit()
    assert client.get("/api/runs").json()["total"] == 2
    access["ids"] = set()
    page = client.get("/api/runs", params={"limit": 1}).json()
    assert page["total"] == 1 and page["items"][0]["id"] == visible.id and page["has_more"] is False
    search = client.get("/api/runs", params={"q": "Confidential"}).json()
    assert search["items"] == [] and search["total"] == 0
    assert client.get("/api/runs", params={"repository_id": live.id}).status_code == 404
    assert client.get(f"/api/runs/{hidden.id}/report").status_code == 404


def test_expired_github_access_fails_closed_for_history_and_report(client, db, monkeypatch):
    _, live, access = live_repository(client, db, monkeypatch)
    run = add_run(db, live)
    db.commit()
    access["expired"] = True
    for path in ["/api/runs", f"/api/runs?repository_id={live.id}", f"/api/runs/{run.id}/report"]:
        response = client.get(path)
        assert response.status_code == 502 and run.id not in response.text


def test_history_and_report_reject_other_tenants_and_unauthenticated_access(client, db, monkeypatch):
    repo = sample_repository(client, db)
    run = add_run(db, repo)
    db.commit()
    org = Organization(name="Other workspace")
    db.add(org)
    db.flush()
    user = User(id="00000000-0000-4000-8000-000000000099", name="Other", email="other@example.test", organization_id=org.id)
    db.add(user)
    db.commit()
    identity = Identity(user.id, org.id, user.name, user.email, org.name, False)
    main.app.dependency_overrides[current_identity] = lambda: identity
    page = client.get("/api/runs").json()
    assert page["items"] == [] and page["total"] == 0
    assert client.get("/api/runs", params={"repository_id": repo.id}).status_code == 404
    assert client.get(f"/api/runs/{run.id}/report").status_code == 404
    main.app.dependency_overrides.clear()
    monkeypatch.setattr(main.settings, "demo_mode", False)
    assert client.get("/api/runs").status_code == 401
    assert client.get(f"/api/runs/{run.id}/report").status_code == 401


@pytest.mark.parametrize("status,kind", [
    ("queued", "analyze"), ("running", "analyze"), ("failed", "analyze"), ("completed", "index"),
])
def test_reports_require_completed_analysis(client, db, status, kind):
    repo = sample_repository(client, db)
    run = add_run(db, repo, status=status, kind=kind)
    db.commit()
    response = client.get(f"/api/runs/{run.id}/report")
    assert response.status_code == 409
    assert "completed analysis" in response.json()["detail"]


def test_report_uses_only_exact_run_and_preserves_stored_evidence_after_snapshot_expiry(client, db, monkeypatch):
    repo = sample_repository(client, db)
    first_id = client.post("/api/demo/analyze", json={"scenario": "rename"}).json()["job_id"]
    other_id = client.post("/api/demo/analyze", json={"scenario": "rename"}).json()["job_id"]
    first_alert = db.scalar(select(Alert).where(Alert.run_id == first_id))
    other_alert = db.scalar(select(Alert).where(Alert.run_id == other_id))
    first_alert.title, other_alert.title = "Selected run finding", "Unrelated later finding"
    snippets = [citation["snippet"] for citation in first_alert.evidence]
    db.execute(delete(Snapshot).where(Snapshot.repository_id == repo.id))
    db.commit()
    from app.retrieval.providers import OpenAIProvider
    monkeypatch.setattr(OpenAIProvider, "from_settings", lambda *_: pytest.fail("Export must not call hosted AI"))
    response = client.get(f"/api/runs/{first_id}/report")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.headers["content-disposition"] == f'attachment; filename="splash-analysis-{first_id}.md"'
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    report = response.text
    assert first_id in report and first_alert.id in report and "Selected run finding" in report
    assert other_id not in report and other_alert.id not in report and "Unrelated later finding" not in report
    assert "machine.temperature" in report and all(snippet in report for snippet in snippets)
    for citation in first_alert.evidence:
        assert citation["commit_sha"] in report and citation["path"] in report
        assert f"Lines: {citation['start_line']}–{citation['end_line']}" in report
    assert "Analysis stages" in report and "Recommendations" in report and "Scope" in report


def test_no_findings_report_is_useful_without_claiming_complete_safety(client):
    client.post("/api/demo/seed")
    run_id = client.post("/api/demo/analyze", json={"scenario": "safe"}).json()["job_id"]
    response = client.get(f"/api/runs/{run_id}/report")
    assert response.status_code == 200
    assert "No findings were recorded for this analysis run." in response.text
    assert "not that every possible change is safe" in response.text
    assert "local fixture" in response.text


def test_report_escapes_untrusted_prose_preserves_hostile_code_and_bounds_unverified_ai(client, db):
    repo = sample_repository(client, db)
    run = add_run(db, repo, title="<script>alert(1)</script>\n# Forged heading [click](javascript:evil)")
    run.stages = [{"name": "<img src=x>", "status": "completed", "summary": "[tracking](https://evil.invalid)", "duration_ms": 5}]
    snippet = "  const value = `literal`;  \r\n``````````\r\n<script>source text</script>\r\n"
    alert = Alert(repository_id=repo.id, run_id=run.id, title="![image](https://evil.invalid/track)",
                  summary="<svg onload=evil()>\n# Forged finding", severity="high", confidence=0.9,
                  evidence_status="verified", affected_component="web/`odd`_name.ts",
                  evidence=[{"path": "web/`odd`_name.ts", "commit_sha": "snapshot-1", "start_line": 3,
                             "end_line": 7, "role": "consumer", "snippet": snippet,
                             "source_url": "javascript:evil()"}],
                  recommendations=["[run this](javascript:evil)\n<script>evil()</script>"],
                  ai_explanation={"summary": "X" * 5000, "hypotheses": [{"claim": "Y" * 2000, "citation_ids": ["C-FAKE"]}] * 20,
                                  "recommendations": ["![track](https://evil.invalid)"] * 20})
    db.add(alert)
    db.commit()
    report = client.get(f"/api/runs/{run.id}/report").text
    assert "<script>alert(1)</script>" not in report and "<svg onload=evil()>" not in report
    assert "&lt;script&gt;" in report and "\\# Forged" in report and "\\[click\\]" in report
    assert snippet in report and "```````````text\n" in report
    assert "web/`odd`_name.ts" in report and run.id in report
    assert "#### AI notes — unverified" in report and "advisory" in report
    assert "C-FAKE" not in report and report.count("- Hypothesis:") == 4
    assert report.count("- Suggested follow-up:") == 4 and "… truncated" in report
    assert "X" * 1201 not in report and "Y" * 601 not in report


def test_inline_identifiers_and_attachment_names_resist_delimiter_and_header_injection():
    assert identifier("`odd`_name.ts") == "`` `odd`_name.ts ``"
    assert identifier("abc\r\n# injected") == "`abc # injected`"
    assert source_block("```\ntext\n") == "````text\n```\ntext\n````"
    name = report_filename('../../bad\r\nContent-Type: text/html')
    assert name.startswith("splash-analysis-") and name.endswith(".md")
    assert "/" not in name and "\r" not in name and "\n" not in name and '"' not in name
