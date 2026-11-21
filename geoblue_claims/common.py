import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path


class ClaimError(ValueError):
    """An actionable failure, safe to display without a traceback."""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def fingerprint(value):
    return digest(canonical(value).encode("utf-8"))


def private_dir(path):
    path = Path(path)
    if path.is_symlink():
        raise ClaimError("Private directory must not be a symbolic link: " + str(path))
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def atomic_write(path, data):
    path = Path(path)
    private_dir(path.parent)
    fd, temp = tempfile.mkstemp(prefix=".write-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def write_json(path, value):
    atomic_write(path, (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8"))


def read_json(path):
    try:
        with Path(path).open(encoding="utf-8") as stream:
            value = json.load(stream, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    except (OSError, ValueError) as exc:
        raise ClaimError("Cannot read valid JSON: " + str(path)) from exc
    if not isinstance(value, dict):
        raise ClaimError("Expected a JSON object: " + str(path))
    return value


def identifier(value, label="id"):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}", value):
        raise ClaimError(label + " must contain 1-80 letters, digits, underscores or hyphens")
    return value


def require_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ClaimError(label + " is required")
    return value.strip()
