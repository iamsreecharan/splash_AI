import hashlib
import hmac
import json
import re
import secrets
from contextlib import asynccontextmanager
from datetime import timedelta, timezone
from typing import Literal
from urllib.parse import urlencode

import redis
from cryptography.fernet import InvalidToken
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import Identity, current_identity
from app.config import get_settings
from app.db import get_db, get_engine, initialize_database
from app.github import (GitHubClient, GitHubError, decrypt_token, encrypt_token,
                        exchange_oauth_code, user_installation_client)
from app.models import (Alert, AnalysisRun, ChangeEvent, Feedback, Installation,
                        OAuthAttempt, Repository, Snapshot, WebhookDelivery, utcnow)
from app.serializers import alert_json, iso, repository_json, run_json
from app.tasks import EnqueueError, demo_snapshot_id, enqueue_run, stored_snapshot


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_settings()  # Validate safety constraints before serving any requests.
    initialize_database()
    yield


app = FastAPI(title="Splash", version="1.0.0", lifespan=lifespan)
settings = get_settings()
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                   allow_credentials=True, allow_methods=["GET", "POST", "DELETE"],
                   allow_headers=["Authorization", "Content-Type"])


@app.exception_handler(GitHubError)
async def github_error_handler(request, exc):
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.exception_handler(EnqueueError)
async def enqueue_error_handler(request, exc):
    return JSONResponse(status_code=503, content={"detail": str(exc)})


def check_github_access(db: Session, identity: Identity, repository: Repository):
    if repository.is_demo:
        return
    installation = db.get(Installation, repository.installation_id)
    if not installation or not installation.active or installation.user_id != identity.user_id:
        raise HTTPException(404, "Repository not found.")
    # Request-scoped cache only. Every new HTTP request revalidates permission;
    # an expired/revoked user token cannot expose previously indexed source.
    cache = db.info.setdefault("github_access", {})
    if installation.id not in cache:
        with user_installation_client(installation) as client:
            repos = client.paginated(f"/user/installations/{installation.github_id}/repositories", key="repositories")
        cache[installation.id] = {item.get("id") for item in repos}
    if repository.github_id not in cache[installation.id]:
        raise HTTPException(404, "Repository is no longer available with your GitHub permissions. Reconnect to refresh repository selection.")


def visible_repositories(db: Session, identity: Identity):
    repos = db.scalars(select(Repository).where(Repository.organization_id == identity.organization_id,
                                               Repository.active.is_(True)).order_by(Repository.full_name)).all()
    allowed = []
    for repo in repos:
        try:
            check_github_access(db, identity, repo)
            allowed.append(repo)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
    return allowed


def authorized_repository(db: Session, identity: Identity, repository_id: str, *, check_live=True) -> Repository:
    repository = db.scalar(select(Repository).where(Repository.id == repository_id,
                                                    Repository.organization_id == identity.organization_id,
                                                    Repository.active.is_(True)))
    if not repository:
        raise HTTPException(404, "Repository not found.")
    if check_live:
        check_github_access(db, identity, repository)
    return repository


def authorized_alert(db: Session, identity: Identity, alert_id: str):
    row = db.execute(select(Alert, Repository).join(Repository, Alert.repository_id == Repository.id)
                     .where(Alert.id == alert_id, Repository.organization_id == identity.organization_id,
                            Repository.active.is_(True))).first()
    if not row:
        raise HTTPException(404, "Alert not found.")
    check_github_access(db, identity, row[1])
    return row


def demo_only(identity: Identity):
    if not settings.demo_mode or not identity.demo:
        raise HTTPException(404, "Demo routes are available only in the isolated local demo workspace.")


@app.get("/health")
def health():
    status = {"database": False, "redis": False}
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1 FROM repositories LIMIT 1"))
        status["database"] = True
    except Exception:
        pass
    try:
        if settings.env == "test" and settings.celery_always_eager:
            status["redis"] = True
        else:
            client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1)
            status["redis"] = bool(client.ping())
            client.close()
    except Exception:
        pass
    ready = all(status.values())
    return JSONResponse({"status": "ok" if ready else "degraded", "dependencies": status,
                         "mode": "demo" if settings.demo_mode else "authenticated"},
                        status_code=200 if ready else 503)


@app.get("/api/public/config")
def public_config():
    return {"demo_mode": settings.demo_mode, "mode": "demo" if settings.demo_mode else "authenticated",
            "supabase_url": settings.supabase_url or None,
            "supabase_anon_key": settings.supabase_anon_key or None,
            "supabase": {"url": settings.supabase_url or None,
                         "anon_key": settings.supabase_anon_key or None,
                         "configured": settings.auth_configured},
            "github": {"configured": settings.github_configured}}


@app.get("/api/session")
def session(identity: Identity = Depends(current_identity)):
    return {"user": {"id": identity.user_id, "name": identity.name, "email": identity.email},
            "organization": {"id": identity.organization_id, "name": identity.organization_name},
            "mode": "demo" if identity.demo else "authenticated",
            "capabilities": {"github": settings.github_configured and not identity.demo,
                             "ai": settings.ai_enabled and bool(settings.openai_api_key),
                             "demo": identity.demo}}


@app.get("/api/overview")
def overview(identity: Identity = Depends(current_identity), db: Session = Depends(get_db)):
    repos = visible_repositories(db, identity)
    ids = [repo.id for repo in repos]
    repo_map = {repo.id: repo for repo in repos}
    alerts = db.scalars(select(Alert).where(Alert.repository_id.in_(ids))
                        .order_by(Alert.created_at.desc()).limit(5)).all()
    runs = db.scalars(select(AnalysisRun).where(AnalysisRun.repository_id.in_(ids))
                      .order_by(AnalysisRun.created_at.desc()).limit(5)).all()
    count_alert = lambda *conditions: db.scalar(select(func.count()).select_from(Alert)
                                                  .where(Alert.repository_id.in_(ids), *conditions))
    completed = db.scalar(select(func.count()).select_from(AnalysisRun)
                           .where(AnalysisRun.repository_id.in_(ids), AnalysisRun.kind == "analyze",
                                  AnalysisRun.status == "completed"))
    return {"repositories": len(repos), "open_alerts": count_alert(Alert.status == "open"),
            "high_severity_alerts": count_alert(Alert.status == "open", Alert.severity == "high"),
            "analyzed_changes": completed,
            "recent_runs": [run_json(run, repo_map[run.repository_id]) for run in runs],
            "recent_alerts": [alert_json(alert, repo_map[alert.repository_id]) for alert in alerts]}


@app.get("/api/repositories")
def repositories(identity: Identity = Depends(current_identity), db: Session = Depends(get_db)):
    repos = visible_repositories(db, identity)
    return {"items": [repository_json(repo) for repo in repos]}


@app.post("/api/repositories/{repository_id}/index", status_code=202)
def reindex(repository_id: str, identity: Identity = Depends(current_identity), db: Session = Depends(get_db)):
    repo = authorized_repository(db, identity, repository_id)
    db.scalar(select(Repository).where(Repository.id == repo.id).with_for_update())
    run = db.scalar(select(AnalysisRun).where(AnalysisRun.repository_id == repo.id,
                                            AnalysisRun.kind == "index",
                                            AnalysisRun.status.in_(["queued", "running"])))
    if not run:
        run = AnalysisRun(repository_id=repo.id, kind="index", title="Index repository", payload={})
        db.add(run)
        db.commit()
    if run.status == "queued":
        enqueue_run(run.id)
    return {"job_id": run.id, "status": "queued"}


@app.get("/api/repositories/{repository_id}/status")
def repository_status(repository_id: str, identity: Identity = Depends(current_identity), db: Session = Depends(get_db)):
    repo = authorized_repository(db, identity, repository_id)
    return {"id": repo.id, "status": repo.status, "indexed_files": repo.indexed_files,
            "indexed_chunks": repo.indexed_chunks, "last_indexed_at": iso(repo.last_indexed_at), "error": repo.error}


@app.delete("/api/repositories/{repository_id}")
def delete_repository(repository_id: str, identity: Identity = Depends(current_identity), db: Session = Depends(get_db)):
    # A workspace owner can erase retained data even after GitHub revocation.
    repo = authorized_repository(db, identity, repository_id, check_live=False)
    db.delete(repo)  # All derived tables use database ON DELETE CASCADE.
    db.commit()
    return {"deleted": True}


@app.get("/api/repositories/{repository_id}/source")
def snapshot_source(repository_id: str, sha: str, path: str,
                    identity: Identity = Depends(current_identity), db: Session = Depends(get_db)):
    repo = authorized_repository(db, identity, repository_id)
    snapshot = db.scalar(select(Snapshot).where(Snapshot.repository_id == repo.id, Snapshot.commit_sha == sha))
    if not snapshot or path not in snapshot.files:
        raise HTTPException(404, "Source snapshot is unavailable or expired. Citations retain their original snippets.")
    return {"path": path, "commit_sha": sha, "content": snapshot.files[path]}


@app.get("/api/alerts")
def alerts(severity: Literal["high", "medium", "low"] | None = None,
           status: Literal["open", "resolved", "dismissed"] | None = None,
           repository_id: str | None = None,
           limit: int = Query(200, ge=1, le=500),
           identity: Identity = Depends(current_identity), db: Session = Depends(get_db)):
    query = select(Alert, Repository).join(Repository, Alert.repository_id == Repository.id)
    query = query.where(Repository.organization_id == identity.organization_id, Repository.active.is_(True))
    if severity:
        query = query.where(Alert.severity == severity)
    if status:
        query = query.where(Alert.status == status)
    if repository_id:
        authorized_repository(db, identity, repository_id)
        query = query.where(Alert.repository_id == repository_id)
    rows = db.execute(query.order_by(Alert.created_at.desc()).limit(limit)).all()
    allowed_ids = {repo.id for repo in visible_repositories(db, identity)}
    return {"items": [alert_json(alert, repo) for alert, repo in rows if repo.id in allowed_ids]}


@app.get("/api/alerts/{alert_id}")
def alert_detail(alert_id: str, identity: Identity = Depends(current_identity), db: Session = Depends(get_db)):
    return alert_json(*authorized_alert(db, identity, alert_id))


class FeedbackInput(BaseModel):
    kind: Literal["useful", "irrelevant", "resolved"]


@app.post("/api/alerts/{alert_id}/feedback")
def feedback(alert_id: str, body: FeedbackInput,
             identity: Identity = Depends(current_identity), db: Session = Depends(get_db)):
    alert, repo = authorized_alert(db, identity, alert_id)
    alert.feedback = body.kind
    if body.kind == "resolved":
        alert.status = "resolved"
    elif body.kind == "irrelevant":
        alert.status = "dismissed"
    db.add(Feedback(alert_id=alert.id, user_id=identity.user_id, kind=body.kind))
    db.commit()
    return alert_json(alert, repo)


@app.get("/api/runs")
def runs(limit: int = Query(100, ge=1, le=500), identity: Identity = Depends(current_identity), db: Session = Depends(get_db)):
    rows = db.execute(select(AnalysisRun, Repository).join(Repository, AnalysisRun.repository_id == Repository.id)
                       .where(Repository.organization_id == identity.organization_id, Repository.active.is_(True))
                       .order_by(AnalysisRun.created_at.desc()).limit(limit)).all()
    allowed_ids = {repo.id for repo in visible_repositories(db, identity)}
    return {"items": [run_json(run, repo) for run, repo in rows if repo.id in allowed_ids]}


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str, identity: Identity = Depends(current_identity), db: Session = Depends(get_db)):
    row = db.execute(select(AnalysisRun, Repository).join(Repository, AnalysisRun.repository_id == Repository.id)
                     .where(AnalysisRun.id == run_id, Repository.organization_id == identity.organization_id,
                            Repository.active.is_(True))).first()
    if not row:
        raise HTTPException(404, "Run not found.")
    check_github_access(db, identity, row[1])
    return run_json(*row)


@app.post("/api/runs/{run_id}/retry", status_code=202)
def retry_run(run_id: str, identity: Identity = Depends(current_identity), db: Session = Depends(get_db)):
    row = db.execute(select(AnalysisRun, Repository).join(Repository, AnalysisRun.repository_id == Repository.id)
                     .where(AnalysisRun.id == run_id, Repository.organization_id == identity.organization_id,
                            Repository.active.is_(True))).first()
    if not row:
        raise HTTPException(404, "Run not found.")
    run, repo = row
    check_github_access(db, identity, repo)
    db.scalar(select(Repository).where(Repository.id == repo.id).with_for_update())
    run = db.scalar(select(AnalysisRun).where(AnalysisRun.id == run.id).with_for_update())
    if not run:
        raise HTTPException(404, "Run not found.")
    if run.status not in {"failed", "queued"}:
        raise HTTPException(409, "Only a failed or queued run can be retried.")
    run.status, run.error, run.completed_at, run.dispatched_at, run.lease_token = "queued", None, None, None, None
    db.commit()
    enqueue_run(run.id)
    return {"job_id": run.id, "status": "queued"}


@app.get("/api/integrations")
def integrations(identity: Identity = Depends(current_identity), db: Session = Depends(get_db)):
    connected = db.scalar(select(func.count()).select_from(Installation)
                           .where(Installation.organization_id == identity.organization_id,
                                  Installation.active.is_(True))) > 0
    return {"github": {"configured": settings.github_configured, "connected": connected,
                       "app_name": settings.github_app_slug or "Splash GitHub App"},
            "openai": {"configured": bool(settings.openai_api_key), "enabled": settings.ai_enabled,
                       "model": settings.openai_model},
            "supabase": {"configured": settings.auth_configured},
            "mode": "demo" if identity.demo else "authenticated"}


@app.post("/api/demo/seed")
def seed_demo(identity: Identity = Depends(current_identity), db: Session = Depends(get_db)):
    demo_only(identity)
    from app.analysis.fixtures import BASE_FILES, DEMO_REPOSITORY_NAME
    repo = db.scalar(select(Repository).where(Repository.organization_id == identity.organization_id,
                                             Repository.is_demo.is_(True)))
    if not repo:
        repo = Repository(organization_id=identity.organization_id, full_name=DEMO_REPOSITORY_NAME,
                          description="Fixture: machine temperature API and its dashboard consumer",
                          language="Python / TypeScript", default_branch="main", is_demo=True,
                          html_url="", status="ready")
        db.add(repo)
        db.flush()
    snapshot = stored_snapshot(db, repo, demo_snapshot_id(BASE_FILES), BASE_FILES)
    from app.models import Chunk
    repo.indexed_files = snapshot.file_count
    repo.indexed_chunks = db.scalar(select(func.count()).select_from(Chunk).where(Chunk.snapshot_id == snapshot.id))
    repo.last_indexed_at, repo.status, repo.error = utcnow(), "ready", None
    db.commit()
    return {"repository_id": repo.id, "message": "Local fixture indexed. Analyze its breaking rename or safe change."}


class DemoAnalysisInput(BaseModel):
    scenario: Literal["rename", "safe"] = "rename"


@app.post("/api/demo/analyze", status_code=202)
def analyze_demo(body: DemoAnalysisInput, identity: Identity = Depends(current_identity), db: Session = Depends(get_db)):
    demo_only(identity)
    repo = db.scalar(select(Repository).where(Repository.organization_id == identity.organization_id,
                                             Repository.is_demo.is_(True)))
    if not repo:
        raise HTTPException(409, "Load the sample repository before analyzing a demo change.")
    # Multiple deliberate analyses are separate jobs, not synthetic stored alerts.
    title = "Rename temperature API field" if body.scenario == "rename" else "Add a harmless optional field"
    run = AnalysisRun(repository_id=repo.id, pr_number=42 if body.scenario == "rename" else 43,
                      title=title, kind="analyze",
                      payload={"scenario": body.scenario, "pr_number": 42 if body.scenario == "rename" else 43,
                               "title": title, "pr_url": ""})
    db.add(run)
    db.commit()
    enqueue_run(run.id)
    return {"job_id": run.id, "status": "queued"}


def state_digest(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()


def set_state_cookie(response, state):
    response.set_cookie("splash_github_state", state, httponly=True,
                        secure=settings.env == "production", samesite="lax",
                        max_age=600, path="/api/github")


def oauth_attempt(db, request: Request, state: str, stage: str):
    cookie = request.cookies.get("splash_github_state", "")
    if not state or not cookie or not hmac.compare_digest(state, cookie):
        raise HTTPException(400, "GitHub connection state is missing or invalid. Start the connection again.")
    attempt = db.scalar(select(OAuthAttempt).where(OAuthAttempt.state_hash == state_digest(state)).with_for_update())
    expiry = attempt.expires_at.replace(tzinfo=attempt.expires_at.tzinfo or timezone.utc) if attempt else utcnow()
    if not attempt or attempt.consumed or attempt.stage != stage or expiry <= utcnow():
        raise HTTPException(400, "GitHub connection expired or was already used. Start the connection again.")
    return attempt


@app.get("/api/github/install")
def github_install(identity: Identity = Depends(current_identity), db: Session = Depends(get_db)):
    if identity.demo:
        raise HTTPException(503, "Sign in with Supabase to connect real repositories. The local demo uses an isolated fixture.")
    if not settings.github_configured:
        raise HTTPException(503, "Configure the GitHub App ID, slug, private key, client ID, client secret, webhook secret, and token encryption key. See docs/github-app.md.")
    state = secrets.token_urlsafe(32)
    db.execute(delete(OAuthAttempt).where(OAuthAttempt.expires_at < utcnow()))
    db.add(OAuthAttempt(state_hash=state_digest(state), user_id=identity.user_id,
                        organization_id=identity.organization_id, expires_at=utcnow() + timedelta(minutes=10)))
    db.commit()
    url = "https://github.com/login/oauth/authorize?" + urlencode({
        "client_id": settings.github_client_id, "state": state,
        "redirect_uri": settings.public_api_url.rstrip("/") + "/api/github/oauth/callback"})
    response = JSONResponse({"url": url})
    set_state_cookie(response, state)
    return response


@app.get("/api/github/oauth/callback")
def github_oauth_callback(request: Request, state: str = "", code: str = "", db: Session = Depends(get_db)):
    attempt = oauth_attempt(db, request, state, "oauth")
    if not code or len(code) > 1000:
        raise HTTPException(400, "GitHub authorization was cancelled or invalid.")
    token = exchange_oauth_code(code)
    with GitHubClient(token) as client:
        user = client.request("GET", "/user")
    attempt.encrypted_token, attempt.github_user_id = encrypt_token(token), int(user["id"])
    # Rotate state and bind the installation callback to this verified GitHub user.
    new_state = secrets.token_urlsafe(32)
    attempt.consumed = True
    db.add(OAuthAttempt(state_hash=state_digest(new_state), user_id=attempt.user_id,
                        organization_id=attempt.organization_id, stage="installation",
                        encrypted_token=attempt.encrypted_token, github_user_id=attempt.github_user_id,
                        expires_at=utcnow() + timedelta(minutes=10)))
    attempt.encrypted_token = None
    db.commit()
    response = RedirectResponse(f"https://github.com/apps/{settings.github_app_slug}/installations/new?" + urlencode({"state": new_state}), status_code=302)
    set_state_cookie(response, new_state)
    return response


@app.get("/api/github/install/callback")
def github_install_callback(request: Request, state: str = "", installation_id: int = 0,
                            db: Session = Depends(get_db)):
    attempt = oauth_attempt(db, request, state, "installation")
    if installation_id <= 0:
        raise HTTPException(400, "GitHub did not supply an installation ID.")
    try:
        token = decrypt_token(attempt.encrypted_token or "")
    except InvalidToken:
        raise HTTPException(400, "GitHub connection credential expired. Start the connection again.") from None
    with GitHubClient(token) as client:
        installations = client.paginated("/user/installations", key="installations")
        selected = next((item for item in installations if item.get("id") == installation_id), None)
        if not selected or str(selected.get("app_id")) != str(settings.github_app_id):
            raise HTTPException(403, "This installation does not belong to the authorized user and Splash App.")
        accessible_repos = client.paginated(f"/user/installations/{installation_id}/repositories", key="repositories")
    installation = db.scalar(select(Installation).where(Installation.github_id == installation_id).with_for_update())
    if installation and installation.organization_id != attempt.organization_id:
        raise HTTPException(409, "This installation is already connected to another Splash workspace.")
    if not installation:
        try:
            with db.begin_nested():
                installation = Installation(github_id=installation_id, organization_id=attempt.organization_id,
                                            user_id=attempt.user_id, github_user_id=attempt.github_user_id,
                                            account_login=selected.get("account", {}).get("login", "GitHub"))
                db.add(installation)
                db.flush()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "The installation was connected concurrently. Refresh the integrations page.") from None
    else:
        installation.active = True
    installation.encrypted_user_token = encrypt_token(token)
    existing = {repo.github_id: repo for repo in db.scalars(select(Repository).where(Repository.installation_id == installation.id)).all()}
    imported = set()
    for item in accessible_repos:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", item.get("full_name", "")):
            continue
        imported.add(item["id"])
        repo = existing.get(item["id"])
        if not repo:
            repo = Repository(organization_id=attempt.organization_id, installation_id=installation.id,
                              github_id=item["id"], full_name=item["full_name"])
            db.add(repo)
        repo.full_name, repo.description = item["full_name"], item.get("description") or ""
        repo.default_branch, repo.language = item.get("default_branch", "main"), item.get("language") or ""
        repo.html_url, repo.active = item.get("html_url") or "", True
    for github_id, repo in existing.items():
        if github_id not in imported:
            db.delete(repo)
    attempt.consumed, attempt.encrypted_token = True, None
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "The installation was connected concurrently. Refresh the integrations page.") from None
    response = RedirectResponse(settings.web_url.rstrip("/") + "/integrations?github=connected", status_code=302)
    response.delete_cookie("splash_github_state", path="/api/github")
    return response


@app.post("/api/webhooks/github")
async def webhook(request: Request, db: Session = Depends(get_db)):
    if not settings.github_webhook_secret:
        raise HTTPException(503, "Configure a GitHub webhook secret before accepting deliveries.")
    declared_length = request.headers.get("content-length", "")
    if declared_length.isdigit() and int(declared_length) > settings.max_webhook_bytes:
        raise HTTPException(413, "Webhook payload exceeds the configured limit.")
    body_parts, size = [], 0
    async for part in request.stream():
        size += len(part)
        if size > settings.max_webhook_bytes:
            raise HTTPException(413, "Webhook payload exceeds the configured limit.")
        body_parts.append(part)
    body = b"".join(body_parts)
    signature = request.headers.get("x-hub-signature-256", "")
    expected = "sha256=" + hmac.new(settings.github_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(401, "Invalid GitHub webhook signature.")
    delivery_id = request.headers.get("x-github-delivery", "")
    event_name = request.headers.get("x-github-event", "")
    if not re.fullmatch(r"[A-Za-z0-9-]{1,128}", delivery_id) or not re.fullmatch(r"[a-z_]{1,100}", event_name):
        raise HTTPException(400, "GitHub delivery ID and event headers are required.")
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError()
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "Invalid GitHub webhook JSON.") from None
    digest = hashlib.sha256(body).hexdigest()
    delivery = db.scalar(select(WebhookDelivery).where(WebhookDelivery.delivery_id == delivery_id).with_for_update())
    if not delivery:
        try:
            # The savepoint catches uniqueness races before any later SELECT can
            # autoflush a conflicting delivery outside our error handling.
            with db.begin_nested():
                delivery = WebhookDelivery(delivery_id=delivery_id, event=event_name, payload_digest=digest)
                db.add(delivery)
                db.flush()
            is_new = True
        except IntegrityError:
            delivery = db.scalar(select(WebhookDelivery).where(WebhookDelivery.delivery_id == delivery_id).with_for_update())
            is_new = False
    else:
        is_new = False
    if not is_new:
        if delivery.payload_digest != digest or delivery.event != event_name:
            raise HTTPException(409, "Delivery ID was already used for a different payload.")
        job_id, needs_dispatch = delivery.run_id, delivery.run_id and delivery.status != "enqueued"
        # Release the delivery lock before dispatch's separate run-row update.
        # Repository deletion cascades run -> delivery and uses the same order.
        db.commit()
        if needs_dispatch:
            enqueue_run(job_id)
            delivery = db.get(WebhookDelivery, delivery_id)
            delivery.status = "enqueued"
            db.commit()
        return {"status": "duplicate", "job_id": job_id}
    installation_data = payload.get("installation") or {}
    github_installation_id = installation_data.get("id")
    installation = db.scalar(select(Installation).where(Installation.github_id == github_installation_id).with_for_update()) if isinstance(github_installation_id, int) else None
    if installation and event_name == "installation" and payload.get("action") in {"deleted", "suspend"}:
        installation.active = False
        for repo in db.scalars(select(Repository).where(Repository.installation_id == installation.id)).all():
            db.delete(repo)
        delivery.status = "ignored"
        db.commit()
        return {"status": "disconnected"}
    if installation and event_name == "installation_repositories":
        removed = [item.get("id") for item in payload.get("repositories_removed", []) if isinstance(item, dict)]
        for repo in db.scalars(select(Repository).where(Repository.installation_id == installation.id,
                                                       Repository.github_id.in_(removed))).all():
            db.delete(repo)
        delivery.status = "ignored"
        db.commit()
        return {"status": "permissions_updated", "message": "Reconnect to import newly granted repositories."}
    repo_data = payload.get("repository") or {}
    repo_github_id = repo_data.get("id")
    repo = db.scalar(select(Repository).where(Repository.github_id == repo_github_id,
                                            Repository.installation_id == installation.id,
                                            Repository.active.is_(True)).with_for_update()) if installation and installation.active and isinstance(repo_github_id, int) else None
    event_payload, kind = None, "analyze"
    if repo and event_name == "pull_request" and payload.get("action") in {"opened", "reopened", "synchronize", "ready_for_review"}:
        pr = payload.get("pull_request") or {}
        if pr.get("draft"):
            delivery.status = "ignored"
            db.commit()
            return {"status": "ignored", "message": "Draft PRs are analyzed when ready for review."}
        base_sha, head_sha = (pr.get("base") or {}).get("sha", ""), (pr.get("head") or {}).get("sha", "")
        pr_number = payload.get("number")
        if not re.fullmatch(r"[a-f0-9]{40}", base_sha) or not re.fullmatch(r"[a-f0-9]{40}", head_sha) or not isinstance(pr_number, int) or pr_number <= 0:
            db.rollback()
            raise HTTPException(400, "Pull request must contain exact base/head commit SHAs and a valid number.")
        event_payload = {"base_sha": base_sha, "head_sha": head_sha, "pr_number": pr_number,
                         "title": str(pr.get("title", "Pull request"))[:500],
                         "pr_url": f"https://github.com/{repo.full_name}/pull/{pr_number}"}
    elif repo and event_name == "push" and payload.get("ref") == f"refs/heads/{repo.default_branch}" and not payload.get("deleted"):
        sha = payload.get("after", "")
        if re.fullmatch(r"[a-f0-9]{40}", sha):
            event_payload, kind = {"commit_sha": sha, "title": "Index default-branch push"}, "index"
    if not event_payload:
        delivery.status = "ignored"
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
        return {"status": "ignored"}
    event_key = (f"pr:{event_payload['pr_number']}:{event_payload['base_sha']}:{event_payload['head_sha']}"
                 if kind == "analyze" else f"push:{event_payload['commit_sha']}")
    change = db.scalar(select(ChangeEvent).where(ChangeEvent.repository_id == repo.id, ChangeEvent.event_key == event_key))
    if change:
        run = db.scalar(select(AnalysisRun).where(AnalysisRun.event_id == change.id))
    else:
        try:
            with db.begin_nested():
                change = ChangeEvent(repository_id=repo.id, event_key=event_key, kind=event_name, payload=event_payload)
                db.add(change)
                db.flush()
                run = AnalysisRun(repository_id=repo.id, event_id=change.id, kind=kind,
                                  pr_number=event_payload.get("pr_number"), title=event_payload["title"], payload=event_payload)
                db.add(run)
                db.flush()
        except IntegrityError:
            change = db.scalar(select(ChangeEvent).where(ChangeEvent.repository_id == repo.id, ChangeEvent.event_key == event_key))
            run = db.scalar(select(AnalysisRun).where(AnalysisRun.event_id == change.id))
    delivery.run_id = run.id
    db.commit()
    # Commit before dispatch so a fast worker never sees a nonexistent job.
    # Failed enqueue stays 'received' and returns 503 for GitHub redelivery.
    if run.status == "queued":
        enqueue_run(run.id)
    delivery.status = "enqueued"
    db.commit()
    return JSONResponse({"status": "queued", "job_id": run.id}, status_code=202)
