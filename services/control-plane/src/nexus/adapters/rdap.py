"""RDAP domain-registration lookups.

Uses the standards-based RDAP protocol via the rdap.org bootstrap redirector —
a legitimate free lookup source, not scraping. Results are cached locally and
rate-limited. An RDAP answer is never presented as a legal guarantee of
availability; statuses are honest:

- registered:        RDAP returned a registration object
- likely-available:  RDAP returned 404 (no registration found)
- uncertain:         RDAP responded but ambiguously (e.g. 403/redirect loop)
- lookup-failed:     network error, timeout, or repeated 429
"""

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from nexus.config import get_settings
from nexus.observability import get_logger

log = get_logger(__name__)

RDAP_BASE = "https://rdap.org/domain/"

REGISTERED = "registered"
LIKELY_AVAILABLE = "likely-available"
UNCERTAIN = "uncertain"
LOOKUP_FAILED = "lookup-failed"


@dataclass
class DomainLookup:
    domain: str
    status: str
    method: str = "rdap.org"
    checked_at: str = ""
    detail: str = ""
    from_cache: bool = False


class RdapClient:
    def __init__(
        self,
        transport: httpx.BaseTransport | None = None,
        cache_path: Path | None = None,
        min_interval_seconds: float = 0.6,
        cache_ttl_seconds: int = 24 * 3600,
    ) -> None:
        self._client = httpx.Client(
            timeout=15,
            transport=transport,
            follow_redirects=True,
            headers={"Accept": "application/rdap+json"},
        )
        self.cache_path = cache_path or (get_settings().cache_dir / "rdap-cache.json")
        self.min_interval = min_interval_seconds
        self.cache_ttl = cache_ttl_seconds
        self._last_request = 0.0
        self._cache: dict[str, dict[str, str]] = self._load_cache()

    def _load_cache(self) -> dict[str, dict[str, str]]:
        try:
            return json.loads(self.cache_path.read_text())
        except (OSError, ValueError):
            return {}

    def _save_cache(self) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(self._cache, indent=0))
        except OSError:
            log.warning("rdap.cache-write-failed", path=str(self.cache_path))

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request = time.monotonic()

    def lookup(self, domain: str) -> DomainLookup:
        domain = domain.lower().strip()
        now = datetime.now(UTC)

        cached = self._cache.get(domain)
        if cached:
            checked = datetime.fromisoformat(cached["checked_at"])
            if (now - checked).total_seconds() < self.cache_ttl:
                return DomainLookup(
                    domain=domain,
                    status=cached["status"],
                    checked_at=cached["checked_at"],
                    detail=cached.get("detail", ""),
                    from_cache=True,
                )

        self._throttle()
        try:
            response = self._client.get(RDAP_BASE + domain)
        except httpx.HTTPError as exc:
            return DomainLookup(
                domain=domain,
                status=LOOKUP_FAILED,
                checked_at=now.isoformat(),
                detail=str(exc)[:200],
            )

        if response.status_code == 200:
            status, detail = REGISTERED, ""
        elif response.status_code == 404:
            status, detail = LIKELY_AVAILABLE, "no RDAP registration found"
        elif response.status_code == 429:
            return DomainLookup(
                domain=domain,
                status=LOOKUP_FAILED,
                checked_at=now.isoformat(),
                detail="rate limited (429)",
            )
        else:
            status, detail = UNCERTAIN, f"HTTP {response.status_code}"

        result = DomainLookup(
            domain=domain, status=status, checked_at=now.isoformat(), detail=detail
        )
        self._cache[domain] = {"status": status, "checked_at": result.checked_at, "detail": detail}
        self._save_cache()
        return result

    def close(self) -> None:
        self._client.close()
