"""Domain discovery skill (`nexus domain search`).

Generates enterprise-grade product-name combinations and checks .com (or
another TLD) registration status through RDAP. A demonstration skill for
end-to-end agent-free execution — it never purchases anything and never treats
an RDAP lookup as a legal guarantee of availability.
"""

import csv
import io
import re
from dataclasses import dataclass, field

from nexus.adapters.rdap import DomainLookup, RdapClient

# Curated second words with an enterprise register. Weak, childish, or
# hard-to-pronounce candidates are excluded by design.
ENTERPRISE_TERMS: dict[str, list[str]] = {
    "authority": [
        "core",
        "grid",
        "forge",
        "guard",
        "shield",
        "command",
        "control",
        "anchor",
        "sentinel",
        "bastion",
    ],
    "intelligence": [
        "logic",
        "signal",
        "vector",
        "axiom",
        "matrix",
        "insight",
        "cognition",
        "synthesis",
    ],
    "delivery": ["ops", "works", "labs", "systems", "engine", "pipeline", "factory", "foundry"],
    "trust": ["prime", "atlas", "meridian", "summit", "vertex", "apex", "keystone", "cornerstone"],
}

# Names failing these checks are rejected as weak or hard to pronounce.
_VOWELS = set("aeiou")
_MAX_LENGTH = 18
_BANNED_FRAGMENTS = ["xxx", "kid", "baby", "cute", "fun", "zap", "wow", "yay"]


def is_serious_name(name: str) -> bool:
    lowered = name.lower()
    if not re.fullmatch(r"[a-z]+", lowered):
        return False
    if len(lowered) > _MAX_LENGTH:
        return False
    if any(fragment in lowered for fragment in _BANNED_FRAGMENTS):
        return False
    if not any(ch in _VOWELS for ch in lowered):
        return False
    # Four or more consecutive consonants reads as unpronounceable.
    if re.search(r"[^aeiou]{4,}", lowered):
        return False
    return True


def generate_names(
    required_word: str,
    second_words: list[str] | None = None,
    tone: str = "authority",
    count: int = 20,
) -> list[str]:
    required = required_word.lower().strip()
    pool = second_words or ENTERPRISE_TERMS.get(tone, ENTERPRISE_TERMS["authority"])
    candidates: list[str] = []
    for term in pool:
        for name in (f"{required}{term}", f"{term}{required}"):
            if is_serious_name(name) and name not in candidates:
                candidates.append(name)
    return candidates[:count]


@dataclass
class DomainSearchResult:
    lookups: list[DomainLookup] = field(default_factory=list)

    def to_markdown(self) -> str:
        lines = ["| Domain | Status | Method | Checked at | Detail |", "|---|---|---|---|---|"]
        for item in self.lookups:
            lines.append(
                f"| {item.domain} | {item.status} | {item.method} "
                f"| {item.checked_at} | {item.detail} |"
            )
        lines.append("")
        lines.append(
            "Note: 'likely-available' means no RDAP registration was found. It is "
            "not a legal guarantee of availability; verify with a registrar before "
            "relying on it. Nexus never purchases domains."
        )
        return "\n".join(lines)

    def to_csv(self) -> str:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["domain", "status", "method", "checked_at", "detail"])
        for item in self.lookups:
            writer.writerow([item.domain, item.status, item.method, item.checked_at, item.detail])
        return buffer.getvalue()


def search_domains(
    required_word: str,
    second_words: list[str] | None = None,
    tone: str = "authority",
    count: int = 20,
    tld: str = "com",
    client: RdapClient | None = None,
) -> DomainSearchResult:
    names = generate_names(required_word, second_words, tone, count)
    owns_client = client is None
    client = client or RdapClient()
    result = DomainSearchResult()
    try:
        for name in names:
            result.lookups.append(client.lookup(f"{name}.{tld}"))
    finally:
        if owns_client:
            client.close()
    return result
