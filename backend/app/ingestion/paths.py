"""Shared snapshot input limits. These filters also protect model inputs."""
from pathlib import PurePosixPath
import re

MAX_FILE_BYTES = 256_000
MAX_FILES = 2_000
MAX_SNAPSHOT_BYTES = 8_000_000
SUPPORTED_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".md", ".mdx", ".rst"}
MANIFEST_NAMES = {"package.json", "pyproject.toml", "requirements.txt", "requirements-dev.txt", "setup.cfg", "setup.py", "Pipfile", "Cargo.toml", "go.mod"}
EXCLUDED_PARTS = {
    ".git", "node_modules", "vendor", "dist", "build", "coverage", ".venv",
    "venv", "__pycache__", ".next", ".nuxt", "generated", "migrations",
}
SENSITIVE_NAMES = {".env", "credentials", "credentials.json", "secrets.json", "id_rsa", "id_ed25519"}
SECRET_PATTERN = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\s*[:=]\s*[\"'][^\"'\n]{12,}[\"'])",
    re.IGNORECASE,
)


class IncompleteSnapshotError(ValueError):
    """Analysis cannot claim removal from an incomplete snapshot."""


def allowed_path(path: str) -> bool:
    """Accept only safe, supported source paths, never credential-like files."""
    if not isinstance(path, str) or not path or "\\" in path or "\x00" in path:
        return False
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in path.split("/")):
        return False
    lowered = tuple(part.lower() for part in candidate.parts)
    if any(part in EXCLUDED_PARTS for part in lowered):
        return False
    name = lowered[-1]
    if name in SENSITIVE_NAMES or name.startswith(".env") or re.search(r"(?:^|[._-])(?:secrets?|credentials?)(?:[._-]|$)", name) or name.endswith((".min.js", ".d.ts", ".pem", ".key")):
        return False
    return candidate.suffix.lower() in SUPPORTED_SUFFIXES or candidate.name in MANIFEST_NAMES


def source_files(files: dict[str, str], *, strict: bool = False) -> dict[str, str]:
    """Bound processing and omit unsupported, binary, generated and large files."""
    accepted: dict[str, str] = {}
    total = 0
    for path, content in sorted(files.items()):
        if not allowed_path(path) or not isinstance(content, str) or "\x00" in content:
            continue
        size = len(content.encode("utf-8"))
        if size > MAX_FILE_BYTES:
            if strict:
                raise IncompleteSnapshotError(f"Supported source file exceeds {MAX_FILE_BYTES} byte analysis limit: {path}")
            continue
        if len(accepted) >= MAX_FILES or total + size > MAX_SNAPSHOT_BYTES:
            if strict:
                raise IncompleteSnapshotError("Snapshot exceeds analysis file or byte limit; narrow repository scope before analysis")
            break
        if SECRET_PATTERN.search(content) or "@generated" in content[:1000].lower() or "automatically generated" in content[:1000].lower():
            continue
        accepted[path] = content
        total += size
    return accepted
