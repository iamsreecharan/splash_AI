from dataclasses import dataclass
from functools import lru_cache
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import Organization, User

DEMO_USER_ID = "00000000-0000-4000-8000-000000000001"
DEMO_ORG_ID = "00000000-0000-4000-8000-000000000002"
bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Identity:
    user_id: str
    organization_id: str
    name: str
    email: str
    organization_name: str
    demo: bool


@lru_cache(maxsize=4)
def jwks_client(issuer: str):
    return jwt.PyJWKClient(f"{issuer}/.well-known/jwks.json", cache_keys=False, lifespan=300)


def verify_token(token: str) -> dict:
    settings = get_settings()
    if not settings.supabase_url:
        raise HTTPException(503, "Configure Supabase authentication before signing in.")
    issuer = settings.supabase_url.rstrip("/") + "/auth/v1"
    try:
        # Never accept an algorithm supplied by an attacker as our allowlist.
        if jwt.get_unverified_header(token).get("alg") not in {"RS256", "ES256"}:
            raise jwt.InvalidTokenError("Unsupported signing algorithm")
        key = jwks_client(issuer).get_signing_key_from_jwt(token)
        claims = jwt.decode(token, key.key, algorithms=["RS256", "ES256"],
                            audience="authenticated", issuer=issuer,
                            options={"require": ["exp", "iat", "sub", "aud", "iss"]})
        UUID(claims["sub"])
        if claims.get("role") != "authenticated":
            raise jwt.InvalidTokenError("Authenticated role required")
        return claims
    except jwt.PyJWKClientConnectionError:
        raise HTTPException(503, "Authentication keys are temporarily unavailable. Retry shortly.") from None
    except (jwt.InvalidTokenError, jwt.PyJWKClientError, ValueError, KeyError):
        raise HTTPException(401, "Invalid or expired authentication token.",
                            headers={"WWW-Authenticate": "Bearer"}) from None


def provision_identity(db: Session, claims: dict, *, demo=False) -> Identity:
    user_id = DEMO_USER_ID if demo else claims["sub"]
    user = db.get(User, user_id)
    if not user:
        metadata = claims.get("user_metadata", {})
        name = "Demo teammate" if demo else str(metadata.get("name") or metadata.get("full_name") or "Teammate")[:200]
        email = "demo@splash.local" if demo else str(claims.get("email", ""))[:320]
        org = Organization(id=DEMO_ORG_ID if demo else None,
                           name="Splash demo workspace" if demo else f"{name}'s workspace")
        db.add(org)
        db.flush()
        user = User(id=user_id, email=email, name=name, organization_id=org.id)
        db.add(user)
        try:
            db.commit()
        except IntegrityError:
            # Concurrent first requests must converge on one personal workspace.
            db.rollback()
            user = db.get(User, user_id)
            if not user:
                raise
    org = db.get(Organization, user.organization_id)
    return Identity(user.id, user.organization_id, user.name, user.email, org.name, demo)


def current_identity(credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
                     db: Session = Depends(get_db)) -> Identity:
    settings = get_settings()
    if credentials:
        if len(credentials.credentials) > 16384:
            raise HTTPException(401, "Invalid authentication token.")
        return provision_identity(db, verify_token(credentials.credentials))
    if settings.demo_mode:
        return provision_identity(db, {}, demo=True)
    raise HTTPException(401, "Sign in to access Splash.", headers={"WWW-Authenticate": "Bearer"})
