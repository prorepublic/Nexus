from nexus.observability import REDACTED, redact_mapping, redact_text


class TestRedaction:
    def test_github_token(self):
        assert redact_text("token ghp_" + "a" * 36) == f"token {REDACTED}"

    def test_notion_token(self):
        assert REDACTED in redact_text("ntn_" + "b" * 40)

    def test_api_key(self):
        assert REDACTED in redact_text("sk-ant-" + "c" * 30)

    def test_bearer_header(self):
        assert REDACTED in redact_text("Authorization: Bearer abcdef1234567890XYZ")

    def test_plain_text_untouched(self):
        text = "ran git status in /workspace, exit 0"
        assert redact_text(text) == text

    def test_secretlike_keys_masked_regardless_of_value(self):
        data = redact_mapping({"notion_token": "short", "message": "ok"})
        assert data["notion_token"] == REDACTED
        assert data["message"] == "ok"

    def test_nested_mappings(self):
        data = redact_mapping({"outer": {"api_key": "x", "note": "fine"}})
        assert data["outer"]["api_key"] == REDACTED
        assert data["outer"]["note"] == "fine"
