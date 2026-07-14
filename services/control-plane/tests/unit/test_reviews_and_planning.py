"""Unit tests: review output parsing, plan normalization, feedback classification,
and fail-closed validation semantics."""

import json
from pathlib import Path

import pytest

from nexus.domain.enums import (
    ReviewVerdict,
    TaskKind,
    TrustLevel,
    ValidationKind,
    ValidationStatus,
)
from nexus.services.github_feedback import classify_comment
from nexus.services.planner import (
    PlannedTask,
    PlanningError,
    detect_cycles,
    normalize_plan_payload,
)
from nexus.services.reviews import extract_json_object, parse_review_output
from nexus.services.validation import (
    InvalidValidationProfile,
    ValidationOutcome,
    evidence_sufficient,
    validate_profile_config,
    validate_workspace,
)


class TestReviewParsing:
    def test_clean_approval(self):
        result = parse_review_output(
            json.dumps({"verdict": "approved", "summary": "ok", "findings": []})
        )
        assert result.verdict == ReviewVerdict.APPROVED
        assert result.raw_ok

    def test_changes_requested_with_findings(self):
        result = parse_review_output(
            json.dumps(
                {
                    "verdict": "changes-requested",
                    "findings": [
                        {
                            "severity": "high",
                            "description": "bug",
                            "blocking": True,
                            "file": "a.py",
                            "line": "10",
                        }
                    ],
                }
            )
        )
        assert result.verdict == ReviewVerdict.CHANGES_REQUESTED
        assert result.blocking_findings[0]["file"] == "a.py"

    def test_json_wrapped_in_prose_and_fences(self):
        text = (
            "Here is my review:\n```json\n"
            + json.dumps({"verdict": "approved", "findings": []})
            + "\n```\nDone."
        )
        assert parse_review_output(text).verdict == ReviewVerdict.APPROVED

    def test_garbage_is_blocked_not_crash(self):
        result = parse_review_output("I refuse to answer in JSON")
        assert result.verdict == ReviewVerdict.BLOCKED
        assert not result.raw_ok

    def test_approved_with_blocking_finding_is_downgraded(self):
        """A reviewer cannot claim approval while flagging blocking defects."""
        result = parse_review_output(
            json.dumps(
                {
                    "verdict": "approved",
                    "findings": [{"severity": "critical", "description": "x", "blocking": True}],
                }
            )
        )
        assert result.verdict == ReviewVerdict.CHANGES_REQUESTED

    def test_unknown_severity_normalized(self):
        result = parse_review_output(
            json.dumps(
                {
                    "verdict": "changes-requested",
                    "findings": [
                        {"severity": "catastrophic", "description": "x", "blocking": True}
                    ],
                }
            )
        )
        assert result.findings[0]["severity"] == "medium"

    def test_extract_json_nested_braces(self):
        payload = {"verdict": "approved", "findings": [], "summary": "uses {braces}"}
        assert extract_json_object("noise " + json.dumps(payload)) == payload


class TestPlanNormalization:
    def _payload(self, tasks):
        return {"objective": "obj", "tasks": tasks}

    def test_valid_plan(self):
        draft = normalize_plan_payload(
            self._payload(
                [
                    {"title": "a", "kind": "implementation", "instruction": "do a"},
                    {
                        "title": "b",
                        "kind": "testing",
                        "instruction": "test a",
                        "depends_on": [0],
                        "validations": ["tests"],
                    },
                ]
            )
        )
        assert len(draft.tasks) == 2
        assert draft.tasks[1].depends_on_index == [0]

    def test_cycle_rejected(self):
        with pytest.raises(PlanningError, match="cycle"):
            normalize_plan_payload(
                self._payload(
                    [
                        {
                            "title": "a",
                            "kind": "implementation",
                            "instruction": "x",
                            "depends_on": [1],
                        },
                        {
                            "title": "b",
                            "kind": "implementation",
                            "instruction": "y",
                            "depends_on": [0],
                        },
                    ]
                )
            )

    def test_self_dependency_rejected(self):
        with pytest.raises(PlanningError, match="invalid dependency"):
            normalize_plan_payload(
                self._payload(
                    [
                        {
                            "title": "a",
                            "kind": "implementation",
                            "instruction": "x",
                            "depends_on": [0],
                        }
                    ]
                )
            )

    def test_out_of_range_dependency_rejected(self):
        with pytest.raises(PlanningError, match="invalid dependency"):
            normalize_plan_payload(
                self._payload(
                    [
                        {
                            "title": "a",
                            "kind": "implementation",
                            "instruction": "x",
                            "depends_on": [7],
                        }
                    ]
                )
            )

    def test_unknown_kind_rejected(self):
        with pytest.raises(PlanningError, match="unknown kind"):
            normalize_plan_payload(
                self._payload([{"title": "a", "kind": "deploy-to-prod", "instruction": "x"}])
            )

    def test_task_bound_enforced(self):
        with pytest.raises(PlanningError, match="12-task"):
            normalize_plan_payload(
                self._payload(
                    [
                        {"title": f"t{i}", "kind": "implementation", "instruction": "x"}
                        for i in range(13)
                    ]
                )
            )

    def test_empty_plan_rejected(self):
        with pytest.raises(PlanningError):
            normalize_plan_payload({"objective": "obj", "tasks": []})

    def test_detect_cycles_helper(self):
        ok = [
            PlannedTask(title="a", instruction="x"),
            PlannedTask(title="b", instruction="y", depends_on_index=[0]),
        ]
        detect_cycles(ok)


class TestFeedbackClassification:
    @pytest.mark.parametrize(
        "body",
        [
            "Please fix the null check in parser.py",
            "This should be using the execution profile instead",
            "typo in the docstring",
            "Missing test for the error branch",
            "can you rename this variable to something clearer",
        ],
    )
    def test_actionable(self, body):
        assert classify_comment("c1", "reviewer", body).actionable

    @pytest.mark.parametrize(
        "body",
        [
            "LGTM!",
            "Nice work, thanks",
            "Why did you choose SKIP LOCKED here?",
            "+1",
            "Nexus pushed an update to this PR.",
            "nexus:resolved — task task_x addressed the feedback",
        ],
    )
    def test_non_actionable(self, body):
        assert not classify_comment("c2", "reviewer", body).actionable


class TestFailClosedValidation:
    def test_requested_check_without_command_is_blocked(self, tmp_path: Path):
        outcomes = validate_workspace(
            tmp_path,
            [ValidationKind.TESTS],
            profile_commands={},
            trust_level=TrustLevel.TRUSTED_LOCAL,
        )
        assert outcomes[0].status == ValidationStatus.BLOCKED
        assert not outcomes[0].passed

    def test_untrusted_repo_scripts_blocked(self, tmp_path: Path):
        outcomes = validate_workspace(
            tmp_path,
            [ValidationKind.TESTS],
            profile_commands={"tests": ["pytest", "-q"]},
            trust_level=TrustLevel.UNTRUSTED,
        )
        assert outcomes[0].status == ValidationStatus.BLOCKED
        assert "trust level" in outcomes[0].summary

    def test_skipped_is_never_passed(self, tmp_path: Path):
        outcomes = validate_workspace(
            tmp_path, [ValidationKind.CHANGED_SCOPE], changed=["a.py"], allowed_globs=[]
        )
        assert outcomes[0].status == ValidationStatus.SKIPPED
        assert not outcomes[0].passed

    def test_empty_outcomes_insufficient_for_implementation(self):
        ok, reason = evidence_sufficient([], TaskKind.IMPLEMENTATION, repository_backed=False)
        assert not ok
        assert "evidence" in reason

    def test_files_exist_alone_insufficient_for_repo_implementation(self):
        outcomes = [
            ValidationOutcome(
                kind=ValidationKind.FILES_EXIST, status=ValidationStatus.PASSED, summary="ok"
            )
        ]
        ok, reason = evidence_sufficient(outcomes, TaskKind.IMPLEMENTATION, repository_backed=True)
        assert not ok
        assert "insufficient" in reason

    def test_files_exist_acceptable_for_scratch_tasks(self):
        outcomes = [
            ValidationOutcome(
                kind=ValidationKind.FILES_EXIST, status=ValidationStatus.PASSED, summary="ok"
            )
        ]
        ok, _ = evidence_sufficient(outcomes, TaskKind.IMPLEMENTATION, repository_backed=False)
        assert ok

    def test_blocked_outcome_prevents_completion(self):
        outcomes = [
            ValidationOutcome(
                kind=ValidationKind.TESTS, status=ValidationStatus.BLOCKED, summary="no command"
            ),
        ]
        ok, _ = evidence_sufficient(outcomes, TaskKind.IMPLEMENTATION, repository_backed=True)
        assert not ok

    def test_expected_file_escape_is_error(self, tmp_path: Path):
        outcomes = validate_workspace(
            tmp_path, [ValidationKind.FILES_EXIST], expected_files=["../../etc/passwd"]
        )
        assert outcomes[0].status == ValidationStatus.ERROR
        assert "unsafe" in outcomes[0].summary

    def test_secret_scan_catches_changed_file(self, tmp_path: Path):
        bad = tmp_path / "config.py"
        bad.write_text("TOKEN = 'ghp_" + "a" * 36 + "'\n")
        outcomes = validate_workspace(tmp_path, [ValidationKind.SECRET_SCAN], changed=["config.py"])
        assert outcomes[0].status == ValidationStatus.FAILED

    def test_scope_violation_detected(self, tmp_path: Path):
        outcomes = validate_workspace(
            tmp_path,
            [ValidationKind.CHANGED_SCOPE],
            changed=["src/ok.py", "infra/secret.tf"],
            allowed_globs=["src/*"],
        )
        assert outcomes[0].status == ValidationStatus.FAILED
        assert "infra/secret.tf" in outcomes[0].summary


class TestValidationProfileConfig:
    def test_valid_profile(self):
        commands = validate_profile_config({"tests": {"command": ["uv", "run", "pytest", "-q"]}})
        assert commands["tests"] == ["uv", "run", "pytest", "-q"]

    def test_unknown_kind_rejected(self):
        with pytest.raises(InvalidValidationProfile, match="unknown validation kind"):
            validate_profile_config({"deploy": {"command": ["make", "deploy"]}})

    def test_unlisted_executable_rejected(self):
        with pytest.raises(InvalidValidationProfile, match="not permitted"):
            validate_profile_config({"tests": {"command": ["curl", "http://evil"]}})

    def test_non_list_command_rejected(self):
        with pytest.raises(InvalidValidationProfile, match="non-empty string list"):
            validate_profile_config({"tests": {"command": "pytest -q"}})
