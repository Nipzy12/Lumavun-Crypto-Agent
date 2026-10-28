"""Structural tests for .github/workflows/agent.yml.

These assert the guarantees the workflow must keep:

* A Python failure in the agent step fails the workflow.
* Tests run before the agent.
* Reports and dedup state are uploaded even when the agent fails.
* Dedup state is restored from a PREVIOUS run, never the current one.

Parsing the real YAML means a regression in the workflow fails CI instead
of silently turning the run green.
"""

from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "agent.yml"


@pytest.fixture(scope="module")
def workflow():
    with open(WORKFLOW, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


@pytest.fixture(scope="module")
def steps(workflow):
    return workflow["jobs"]["scan"]["steps"]


def _find(steps, name_fragment):
    for step in steps:
        name = str(step.get("name", ""))
        if name_fragment.lower() in name.lower():
            return step
    raise AssertionError(f"no step matching {name_fragment!r}")


def _index(steps, name_fragment):
    for i, step in enumerate(steps):
        if name_fragment.lower() in str(step.get("name", "")).lower():
            return i
    raise AssertionError(f"no step matching {name_fragment!r}")


class TestAgentFailurePropagates:
    def test_run_agent_step_has_no_continue_on_error(self, steps):
        step = _find(steps, "Run agent")
        assert step.get("continue-on-error") not in (True, "true"), (
            "continue-on-error on the agent step would mask Python failures "
            "and turn the workflow green"
        )

    def test_run_agent_step_invokes_main(self, steps):
        step = _find(steps, "Run agent")
        assert "src/main.py" in str(step.get("run", ""))

    def test_tests_run_before_the_agent(self, steps):
        assert _index(steps, "Run tests") < _index(steps, "Run agent")

    def test_test_step_is_not_masked(self, steps):
        step = _find(steps, "Run tests")
        assert step.get("continue-on-error") not in (True, "true")

    def test_dev_requirements_installed_for_tests(self, steps):
        step = _find(steps, "Install dependencies")
        assert "requirements-dev.txt" in str(step.get("run", ""))


class TestArtifactsUploaded:
    def test_reports_upload_even_on_failure(self, steps):
        step = _find(steps, "Upload reports")
        assert step.get("if") == "always()"
        path = str(step["with"]["path"])
        assert "latest_report.json" in path
        assert "latest_report.md" in path

    def test_dedup_state_upload_even_on_failure(self, steps):
        step = _find(steps, "Upload dedup state")
        assert step.get("if") == "always()"
        assert "dedup_state.json" in str(step["with"]["path"])

    def test_artifact_names_preserved(self, steps):
        assert _find(steps, "Upload reports")["with"]["name"] == "latest_report"
        assert _find(steps, "Upload dedup state")["with"]["name"] == "dedup_state"


class TestDedupRestoredFromPreviousRun:
    def test_does_not_use_download_artifact_without_run_id(self, steps):
        """actions/download-artifact defaults to the CURRENT run.

        Without an explicit run-id it cannot reach a previous run's state,
        which was the original V1.1 bug.
        """
        for step in steps:
            if str(step.get("uses", "")).startswith("actions/download-artifact"):
                has_run_id = "run-id" in (step.get("with") or {})
                assert has_run_id, (
                    "actions/download-artifact must specify run-id, "
                    "otherwise it reads the current run"
                )

    def test_restore_step_targets_a_previous_run(self, steps):
        step = _find(steps, "Restore dedup state")
        run = str(step.get("run", ""))
        # It must exclude the current run and download by an explicit id.
        assert "CURRENT_RUN" in run or "github.run_id" in run
        assert "gh run download" in run
        assert "dedup_state.json" in run

    def test_restore_step_is_non_fatal_on_first_run(self, steps):
        step = _find(steps, "Restore dedup state")
        assert str(step.get("continue-on-error")).lower() == "true"

    def test_restore_happens_before_agent_runs(self, steps):
        assert _index(steps, "Restore dedup state") < _index(steps, "Run agent")

    def test_actions_read_permission_granted(self, workflow):
        assert workflow["permissions"]["actions"] == "read"

    def test_contents_stay_read_only(self, workflow):
        assert workflow["permissions"]["contents"] == "read"


class TestTriggerAndSafety:
    def test_keeps_manual_and_scheduled_triggers(self, workflow):
        # PyYAML parses the bare key 'on' as boolean True.
        on = workflow.get("on", workflow.get(True))
        assert "workflow_dispatch" in on
        assert any("cron" in entry for entry in on["schedule"])

    def test_telegram_secrets_passed_as_secrets(self, steps):
        env = _find(steps, "Run agent").get("env") or {}
        assert "secrets.TELEGRAM_BOT_TOKEN" in str(env["TELEGRAM_BOT_TOKEN"])
        assert "secrets.TELEGRAM_CHAT_ID" in str(env["TELEGRAM_CHAT_ID"])

    def test_no_secret_is_written_to_stdout(self, steps):
        for step in steps:
            run = str(step.get("run", ""))
            assert "TELEGRAM_BOT_TOKEN" not in run or "echo" not in run


class TestV13SourceVerification:
    """The V1.3 source check must be visible without stopping the scan."""

    def test_verify_sources_step_exists(self, steps):
        step = _find(steps, "Verify sources")
        assert "verify_sources.py" in str(step.get("run", ""))

    def test_verify_sources_is_non_fatal(self, steps):
        step = _find(steps, "Verify sources")
        assert str(step.get("continue-on-error")).lower() == "true"
        assert step.get("id") == "verify"

    def test_verification_happens_before_the_agent(self, steps):
        assert _index(steps, "Verify sources") < _index(steps, "Run agent")

    def test_source_health_artifact_is_uploaded_even_on_failure(self, steps):
        step = _find(steps, "Upload source health")
        assert step.get("if") == "always()"
        assert step["with"]["name"] == "source_health"
        assert "source_health.json" in str(step["with"]["path"])

    def test_state_source_is_passed_into_the_agent(self, steps):
        env = _find(steps, "Run agent").get("env") or {}
        assert "LUMAVUN_STATE_SOURCE" in env
        assert "dedup_in.outputs.state_source" in str(env["LUMAVUN_STATE_SOURCE"])

    def test_summary_reports_dedup_source_and_outcomes(self, steps):
        step = _find(steps, "Summarize run")
        run = str(step.get("run", ""))
        assert step.get("if") == "always()"
        assert "Dedup state source" in run
        assert "latest_report.json" in run
        assert "opportunity_summary" in run
        assert "zero_capital_summary" in run

    def test_workflow_name_reports_the_current_version(self, workflow):
        assert "V1.3" in str(workflow["name"])

    def test_git_hub_variables_still_flow_to_the_agent(self, steps):
        env = _find(steps, "Run agent").get("env") or {}
        for name in (
            "AIRDROP_RSS_FEEDS",
            "PHANTOM_PUBLIC_ADDRESS",
            "SOLANA_RPC_URL",
            "SOLANA_RPC_FALLBACK_URLS",
        ):
            assert name in env

    def test_schedule_and_dispatch_are_preserved(self, workflow):
        on = workflow.get("on", workflow.get(True))
        assert "workflow_dispatch" in on
        assert on["schedule"][0]["cron"] == "17 1,13 * * *"

    def test_run_tests_still_blocks_the_agent(self, steps):
        """A failing test suite must fail the workflow before scanning."""
        tests = _find(steps, "Run tests")
        agent = _find(steps, "Run agent")
        assert tests.get("continue-on-error") not in (True, "true")
        assert tests.get("if") in (None, "success")
        assert agent.get("if") in (None, "success")
        assert _index(steps, "Run tests") < _index(steps, "Verify sources")
