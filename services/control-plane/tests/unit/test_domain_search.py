import json
from pathlib import Path

import httpx

from nexus.adapters.rdap import (
    LIKELY_AVAILABLE,
    LOOKUP_FAILED,
    REGISTERED,
    UNCERTAIN,
    RdapClient,
)
from nexus.skills.domain_search import generate_names, is_serious_name, search_domains


class TestNameGeneration:
    def test_generates_required_word_combinations(self):
        names = generate_names("nexus", count=10)
        assert names
        assert all("nexus" in name for name in names)

    def test_rejects_weak_names(self):
        assert not is_serious_name("nexuszap")
        assert not is_serious_name("babynexus")
        assert not is_serious_name("nexus-thing")
        assert not is_serious_name("nexusfun")

    def test_rejects_unpronounceable(self):
        assert not is_serious_name("nxstrgrd")  # no clear vowel rhythm
        assert not is_serious_name("bcdfnexus")

    def test_accepts_serious_names(self):
        assert is_serious_name("nexusforge")
        assert is_serious_name("nexuscore")

    def test_count_respected(self):
        assert len(generate_names("nexus", count=5)) == 5

    def test_explicit_second_words(self):
        names = generate_names("nexus", second_words=["forge"], count=10)
        assert set(names) <= {"nexusforge", "forgenexus"}


def _mock_client(tmp_path: Path, status_map: dict[str, int]) -> RdapClient:
    def handler(request: httpx.Request) -> httpx.Response:
        domain = request.url.path.rsplit("/", 1)[-1]
        code = status_map.get(domain, 404)
        body = {"objectClassName": "domain"} if code == 200 else {"error": code}
        return httpx.Response(code, json=body)

    return RdapClient(
        transport=httpx.MockTransport(handler),
        cache_path=tmp_path / "cache.json",
        min_interval_seconds=0,
    )


class TestRdapClassification:
    def test_registered(self, tmp_path: Path):
        client = _mock_client(tmp_path, {"google.com": 200})
        assert client.lookup("google.com").status == REGISTERED

    def test_likely_available(self, tmp_path: Path):
        client = _mock_client(tmp_path, {})
        result = client.lookup("very-unlikely-name-xyz.com")
        assert result.status == LIKELY_AVAILABLE

    def test_uncertain(self, tmp_path: Path):
        client = _mock_client(tmp_path, {"weird.com": 403})
        assert client.lookup("weird.com").status == UNCERTAIN

    def test_rate_limited_is_lookup_failed(self, tmp_path: Path):
        client = _mock_client(tmp_path, {"busy.com": 429})
        assert client.lookup("busy.com").status == LOOKUP_FAILED

    def test_network_error_is_lookup_failed(self, tmp_path: Path):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom")

        client = RdapClient(
            transport=httpx.MockTransport(handler),
            cache_path=tmp_path / "cache.json",
            min_interval_seconds=0,
        )
        assert client.lookup("x.com").status == LOOKUP_FAILED

    def test_cache_hit_avoids_second_request(self, tmp_path: Path):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, json={"objectClassName": "domain"})

        client = RdapClient(
            transport=httpx.MockTransport(handler),
            cache_path=tmp_path / "cache.json",
            min_interval_seconds=0,
        )
        client.lookup("cached.com")
        second = client.lookup("cached.com")
        assert calls["n"] == 1
        assert second.from_cache

    def test_cache_persisted_to_disk(self, tmp_path: Path):
        client = _mock_client(tmp_path, {"x.com": 200})
        client.lookup("x.com")
        data = json.loads((tmp_path / "cache.json").read_text())
        assert data["x.com"]["status"] == REGISTERED


class TestSearchAndExport:
    def test_search_and_exports(self, tmp_path: Path):
        client = _mock_client(tmp_path, {"nexuscore.com": 200})
        result = search_domains("nexus", count=4, client=client)
        assert len(result.lookups) == 4
        markdown = result.to_markdown()
        assert "not a legal guarantee" in markdown
        csv_text = result.to_csv()
        assert csv_text.splitlines()[0] == "domain,status,method,checked_at,detail"
        assert len(csv_text.splitlines()) == 5
