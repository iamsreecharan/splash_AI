"""Bounded, read-only GitHub App API access. No repository code is executed."""
import base64
import hashlib
import re
import time
from pathlib import PurePosixPath

import httpx
import jwt
from cryptography.fernet import Fernet
from cryptography.fernet import InvalidToken

from app.config import get_settings


class GitHubError(Exception):
    """A sanitized upstream error safe to present to a pilot user."""


def token_cipher():
    key = get_settings().token_encryption_key
    if not key:
        raise GitHubError("Configure SPLASH_TOKEN_ENCRYPTION_KEY to securely connect GitHub.")
    try:
        return Fernet(key.encode())
    except ValueError:
        raise GitHubError("SPLASH_TOKEN_ENCRYPTION_KEY must be a valid Fernet key.") from None


def encrypt_token(token: str) -> str:
    return token_cipher().encrypt(token.encode()).decode()


def decrypt_token(token: str) -> str:
    try:
        return token_cipher().decrypt(token.encode()).decode()
    except InvalidToken:
        raise GitHubError("GitHub connection credential is unavailable. Reconnect the installation.") from None


def user_installation_client(installation) -> "GitHubClient":
    if not installation.active or not installation.encrypted_user_token:
        raise GitHubError("Reconnect GitHub to authorize access with your current repository permissions.")
    return GitHubClient(decrypt_token(installation.encrypted_user_token))


def safe_source_path(path: str) -> bool:
    if not path or len(path) > 1000 or "\\" in path or any(ord(c) < 32 for c in path):
        return False
    from app.ingestion.paths import allowed_path
    return allowed_path(path)


class GitHubClient:
    def __init__(self, token: str):
        self.client = httpx.Client(base_url="https://api.github.com", timeout=30,
                                  follow_redirects=False,
                                  headers={"Authorization": f"Bearer {token}",
                                           "Accept": "application/vnd.github+json",
                                           "X-GitHub-Api-Version": "2022-11-28",
                                           "User-Agent": "Splash-V1"})

    def close(self):
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def request(self, method: str, path: str, **kwargs):
        if not path.startswith("/") or path.startswith("//") or "://" in path:
            raise GitHubError("Invalid GitHub API path.")
        # Writes are limited to obtaining credentials. Repository operations are GET only.
        if method != "GET" and not re.fullmatch(r"/app/installations/\d+/access_tokens", path):
            raise GitHubError("Splash's GitHub integration is read-only.")
        try:
            response = self.client.request(method, path, **kwargs)
        except httpx.HTTPError:
            raise GitHubError("GitHub could not be reached. Retry after checking network access.") from None
        if response.status_code >= 400 or response.is_redirect:
            if response.status_code in {401, 403}:
                raise GitHubError("GitHub denied access. Check App permissions and installation access.")
            if response.status_code == 404:
                raise GitHubError("GitHub repository or commit is unavailable to this App installation.")
            if response.status_code == 429:
                raise GitHubError("GitHub rate limit reached. Retry later.")
            raise GitHubError(f"GitHub returned HTTP {response.status_code}.")
        try:
            return response.json()
        except ValueError:
            raise GitHubError("GitHub returned an invalid response.") from None

    def paginated(self, path: str, key: str | None = None, max_pages=10):
        result = []
        for page in range(1, max_pages + 1):
            data = self.request("GET", path, params={"per_page": 100, "page": page})
            items = data.get(key, []) if key else data
            if not isinstance(items, list):
                raise GitHubError("GitHub returned an invalid paginated response.")
            result.extend(items)
            if len(items) < 100:
                return result
        raise GitHubError("The GitHub installation exceeds this pilot's 1,000-item limit. Select fewer repositories.")

    def snapshot(self, full_name: str, sha: str) -> dict[str, str]:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", full_name):
            raise GitHubError("Invalid repository name.")
        if not re.fullmatch(r"[a-f0-9]{40}", sha):
            raise GitHubError("An exact Git commit SHA is required for indexing.")
        settings = get_settings()
        tree = self.request("GET", f"/repos/{full_name}/git/trees/{sha}", params={"recursive": "1"})
        if tree.get("truncated"):
            raise GitHubError("GitHub truncated this repository tree. Use a smaller repository for the pilot.")
        candidates = [item for item in tree.get("tree", [])
                      if item.get("type") == "blob" and item.get("mode") != "120000"
                      and safe_source_path(item.get("path", ""))]
        if len(candidates) > settings.max_repository_files:
            raise GitHubError("Repository exceeds the configured source-file limit.")
        # Reject incomplete snapshots instead of silently analyzing missing consumers.
        if any(item.get("size", 0) > settings.max_file_bytes for item in candidates):
            raise GitHubError("A source file exceeds the configured size limit. Raise the limit or select a smaller repository.")
        if sum(item.get("size", 0) for item in candidates) > settings.max_repository_bytes:
            raise GitHubError("Repository exceeds the configured source-byte limit.")
        files, total = {}, 0
        for item in candidates:
            blob_sha = item.get("sha", "")
            if not re.fullmatch(r"[a-f0-9]{40}", blob_sha):
                raise GitHubError("GitHub returned an invalid blob SHA.")
            blob = self.request("GET", f"/repos/{full_name}/git/blobs/{blob_sha}")
            if blob.get("encoding") != "base64" or blob.get("size", 0) > settings.max_file_bytes:
                raise GitHubError("A source blob is unsupported or exceeds the configured limit.")
            try:
                raw = base64.b64decode(blob.get("content", ""), validate=False)
            except ValueError:
                raise GitHubError("GitHub returned invalid source content.") from None
            if len(raw) > settings.max_file_bytes:
                raise GitHubError("Source blob exceeds the configured file limit.")
            actual_sha = hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()
            if actual_sha != blob_sha or blob.get("sha") != blob_sha:
                raise GitHubError("GitHub source blob failed its Git object checksum.")
            total += len(raw)
            if total > settings.max_repository_bytes:
                raise GitHubError("Repository exceeds the configured source-byte limit.")
            try:
                content = raw.decode("utf-8")
            except UnicodeDecodeError:
                raise GitHubError("A supported source file is not UTF-8; no partial analysis was stored.") from None
            if "\x00" in content:
                raise GitHubError("A source file contains binary content.")
            files[item["path"]] = content
        return files


def app_client() -> GitHubClient:
    settings = get_settings()
    if not settings.github_configured:
        raise GitHubError("Configure the read-only GitHub App, OAuth callback, webhook secret, and token encryption key.")
    now = int(time.time())
    try:
        token = jwt.encode({"iat": now - 60, "exp": now + 540, "iss": settings.github_app_id},
                           settings.private_key(), algorithm="RS256")
    except (ValueError, OSError, jwt.PyJWTError):
        raise GitHubError("The GitHub App private key is missing or invalid.") from None
    return GitHubClient(token)


def installation_client(installation_id: int) -> GitHubClient:
    with app_client() as client:
        result = client.request("POST", f"/app/installations/{int(installation_id)}/access_tokens",
                                json={"permissions": {"contents": "read", "pull_requests": "read", "metadata": "read"}})
    if not result.get("token"):
        raise GitHubError("GitHub did not issue an installation credential.")
    return GitHubClient(result["token"])


def exchange_oauth_code(code: str) -> str:
    settings = get_settings()
    try:
        response = httpx.post("https://github.com/login/oauth/access_token",
                              json={"client_id": settings.github_client_id,
                                    "client_secret": settings.github_client_secret,
                                    "code": code,
                                    "redirect_uri": settings.public_api_url.rstrip("/") + "/api/github/oauth/callback"},
                              headers={"Accept": "application/json"}, timeout=30)
        response.raise_for_status()
        token = response.json().get("access_token")
    except (httpx.HTTPError, ValueError):
        raise GitHubError("GitHub authorization could not be completed. Start the connection again.") from None
    if not token:
        raise GitHubError("GitHub declined authorization. Start the connection again.")
    return token
