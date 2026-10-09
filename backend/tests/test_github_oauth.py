from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from cryptography.fernet import Fernet
from sqlalchemy import select

from app import main
from app.auth import Identity, current_identity
from app.github import GitHubError, decrypt_token, encrypt_token
from app.models import Installation, OAuthAttempt, Organization, Repository, User


def real_identity(client, db, monkeypatch):
    # Explicit signed-in identity: a demo identity cannot connect real repos.
    session = client.get("/api/session").json()
    identity = Identity(session["user"]["id"], session["organization"]["id"], "Pilot", "pilot@example.test", "Pilot workspace", False)
    main.app.dependency_overrides[current_identity] = lambda: identity
    fields = {"github_app_id": "123", "github_app_slug": "splash-test", "github_private_key": "configured-test-key",
              "github_client_id": "test-client", "github_client_secret": "test-secret",
              "token_encryption_key": Fernet.generate_key().decode()}
    for key, value in fields.items():
        monkeypatch.setattr(main.settings, key, value)
    return identity


class UserClient:
    installation_app = 123
    installation_id = 456
    repo_ids = [789]

    def __init__(self, token):
        self.token = token

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def request(self, method, path):
        assert path == "/user"
        return {"id": 22}

    def paginated(self, path, key):
        if path == "/user/installations":
            return [{"id": self.installation_id, "app_id": self.installation_app, "account": {"login": "pilot-owner"}}]
        assert path == f"/user/installations/{self.installation_id}/repositories"
        return [{"id": rid, "full_name": f"pilot-owner/repo-{rid}", "description": "Private repository",
                 "default_branch": "main", "language": "Python", "html_url": f"https://github.com/pilot-owner/repo-{rid}"}
                for rid in self.repo_ids]


def installation_state(client, monkeypatch):
    monkeypatch.setattr(main, "exchange_oauth_code", lambda code: "private-user-oauth-token")
    monkeypatch.setattr(main, "GitHubClient", UserClient)
    response = client.get("/api/github/install")
    assert response.status_code == 200
    state = parse_qs(urlparse(response.json()["url"]).query)["state"][0]
    callback = client.get("/api/github/oauth/callback", params={"state": state, "code": "valid-code"}, follow_redirects=False)
    assert callback.status_code == 302
    assert "private-user-oauth-token" not in callback.headers["location"]
    new_state = parse_qs(urlparse(callback.headers["location"]).query)["state"][0]
    return state, new_state


def test_oauth_proof_state_rotation_encrypted_token_retention_and_replay(client, db, monkeypatch):
    real_identity(client, db, monkeypatch)
    old_state, state = installation_state(client, monkeypatch)
    attempts = db.scalars(select(OAuthAttempt)).all()
    assert len(attempts) == 2
    pending = next(attempt for attempt in attempts if not attempt.consumed)
    assert "private-user-oauth-token" not in pending.encrypted_token
    assert decrypt_token(pending.encrypted_token) == "private-user-oauth-token"
    response = client.get("/api/github/install/callback", params={"state": state, "installation_id": 456}, follow_redirects=False)
    assert response.status_code == 302
    db.expire_all()
    installation = db.scalar(select(Installation))
    assert installation.github_id == 456 and installation.github_user_id == 22
    assert installation.encrypted_user_token != "private-user-oauth-token"
    assert decrypt_token(installation.encrypted_user_token) == "private-user-oauth-token"
    assert db.scalar(select(Repository)).github_id == 789
    assert all(attempt.consumed and attempt.encrypted_token is None for attempt in db.scalars(select(OAuthAttempt)).all())
    replay = client.get("/api/github/install/callback", params={"state": state, "installation_id": 456})
    assert replay.status_code == 400
    old_replay = client.get("/api/github/oauth/callback", params={"state": old_state, "code": "valid-code"})
    assert old_replay.status_code == 400


def test_forged_installation_and_unbound_browser_state_are_rejected(client, db, monkeypatch):
    real_identity(client, db, monkeypatch)
    _, state = installation_state(client, monkeypatch)
    forged = client.get("/api/github/install/callback", params={"state": state, "installation_id": 99999})
    assert forged.status_code == 403
    assert db.scalar(select(Installation)) is None
    client.cookies.clear()
    unbound = client.get("/api/github/install/callback", params={"state": state, "installation_id": 456})
    assert unbound.status_code == 400


def test_installation_must_belong_to_this_github_app(client, db, monkeypatch):
    real_identity(client, db, monkeypatch)
    _, state = installation_state(client, monkeypatch)
    monkeypatch.setattr(UserClient, "installation_app", 999)
    response = client.get("/api/github/install/callback", params={"state": state, "installation_id": 456})
    assert response.status_code == 403
    assert db.scalar(select(Installation)) is None


def test_installation_cannot_be_claimed_by_second_workspace(client, db, monkeypatch):
    real_identity(client, db, monkeypatch)
    other_org = Organization(name="Other workspace")
    db.add(other_org)
    db.flush()
    user = User(id="other-user", name="Other", email="other@example.test", organization_id=other_org.id)
    db.add(user)
    db.flush()
    existing = Installation(github_id=456, organization_id=other_org.id, user_id=user.id,
                            github_user_id=22, account_login="pilot-owner")
    db.add(existing)
    db.commit()
    _, state = installation_state(client, monkeypatch)
    response = client.get("/api/github/install/callback", params={"state": state, "installation_id": 456})
    assert response.status_code == 409
    db.expire_all()
    assert db.get(Installation, existing.id).organization_id == other_org.id


def test_revoked_user_cannot_read_indexed_data_but_can_erase_it(client, db, monkeypatch):
    real_identity(client, db, monkeypatch)
    _, state = installation_state(client, monkeypatch)
    client.get("/api/github/install/callback", params={"state": state, "installation_id": 456}, follow_redirects=False)
    repo = db.scalar(select(Repository))
    repo_id = repo.id
    from app.tasks import stored_snapshot
    stored_snapshot(db, repo, "a" * 40, {"api.py": "PRIVATE_SOURCE = 42"})
    db.commit()
    import app.github as github
    monkeypatch.setattr(github, "GitHubClient", UserClient)
    assert len(client.get("/api/repositories").json()["items"]) == 1
    monkeypatch.setattr(UserClient, "repo_ids", [])
    assert client.get("/api/repositories").json()["items"] == []
    assert client.get(f"/api/repositories/{repo_id}/source", params={"sha": "a" * 40, "path": "api.py"}).status_code == 404
    assert client.post(f"/api/repositories/{repo_id}/index").status_code == 404
    assert client.delete(f"/api/repositories/{repo_id}").status_code == 200
    db.expire_all()
    assert db.get(Repository, repo_id) is None


def test_expired_user_token_fails_closed_for_indexed_data(client, db, monkeypatch):
    real_identity(client, db, monkeypatch)
    _, state = installation_state(client, monkeypatch)
    client.get("/api/github/install/callback", params={"state": state, "installation_id": 456}, follow_redirects=False)
    repo = db.scalar(select(Repository))

    class ExpiredClient(UserClient):
        def paginated(self, path, key):
            raise GitHubError("GitHub denied access. Reconnect the installation.")

    import app.github as github
    monkeypatch.setattr(github, "GitHubClient", ExpiredClient)
    assert client.get("/api/repositories").status_code == 502
    assert client.get(f"/api/repositories/{repo.id}/status").status_code == 502
    assert client.delete(f"/api/repositories/{repo.id}").status_code == 200
