"""Local-owner credential (ADR-013, hardened in the V1 acceptance pass).

A cryptographically random token is generated on first use and stored in a
local, git-ignored, chmod-600 file. Every state-changing API request must
present it in the X-Nexus-Owner-Token header; comparison is constant-time.

The token is never exposed through API responses, logs, or the browser
bundle: the dashboard talks to a same-origin Next.js proxy route that reads
the token file server-side and attaches the header on the loopback hop.
"""

import hmac
import secrets
import stat
from pathlib import Path

from nexus.config import get_settings
from nexus.observability import get_logger

log = get_logger(__name__)

_TOKEN_BYTES = 32
_MAX_TOKEN_LENGTH = 200


def token_file() -> Path:
    return get_settings().owner_token_file


def get_or_create_token() -> str:
    """Read the owner token, generating it on first use (chmod 600)."""
    path = token_file()
    try:
        existing = path.read_text().strip()
        if existing:
            return existing
    except OSError:
        pass
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token + "\n")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    log.info("owner_auth.token_generated", path=str(path))
    return token


def verify_token(presented: str | None) -> bool:
    """Constant-time verification. Missing, malformed, or wrong => False."""
    if not presented or not isinstance(presented, str):
        return False
    if len(presented) > _MAX_TOKEN_LENGTH or "\n" in presented or "\r" in presented:
        return False
    expected = get_or_create_token()
    return hmac.compare_digest(presented.encode(), expected.encode())
