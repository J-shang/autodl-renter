"""Public CLI behavior, with every live API and interactive input isolated."""
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from autodl_rent.api import APIError, TransportError
from autodl_rent.cli import main
from autodl_rent.config import example_config, load_config
from autodl_rent.demo import DemoAPI, demo_machine
from autodl_rent.onboarding import choose_many
from autodl_rent.output import config_schema
from autodl_rent.paths import ROOT
from autodl_rent.state import State, write_private_json


class CLITests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.path = self.folder / "config.json"
        self.auth = self.folder / "private" / "auth.json"
        self.env = {"AUTODL_RENT_HOME": str(self.auth.parent), "AUTODL_WEB_TOKEN": ""}
        self.environment = patch.dict(os.environ, self.env)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        network = patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("No live network in CLI tests"))
        network.start()
        self.addCleanup(network.stop)
        self.api = DemoAPI()

    def call(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main([*argv, "--json"])
        data = json.loads(out.getvalue())  # Also rejects multiple JSON records or mixed human output.
        self.assertEqual(data["exit_code"], code)
        self.assertEqual(data["ok"], code == 0)
        return code, data, err.getvalue()

    def config(self, *, policy="any"):
        raw = example_config(policy)
        raw["image"]["uuid"] = "demo-image"
        raw["poll"]["jitter_seconds"] = 0
        write_private_json(self.path, raw)
        return load_config(self.path)

    def stub_api(self):
        return patch("autodl_rent.cli.make_api", return_value=self.api)

    def test_demo_json_contains_only_machine_result(self):
        code, result, _ = self.call("demo", "--policy", "all")
        self.assertEqual(code, 0)
        self.assertEqual(result["schema_version"], 1)
        self.assertTrue(result["data"]["simulation"])
        self.assertEqual(result["data"]["count"], 2)
        self.assertIsNone(result["error"])
        self.assertFalse(self.auth.exists())

    def test_no_arguments_show_help_without_network(self):
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main([]), 0)
        self.assertIn("python3 -m autodl_rent", output.getvalue())

    def test_source_entrypoint_works_without_site_packages(self):
        process = subprocess.run([sys.executable, "-S", "-m", "autodl_rent", "demo", "--json", "--quiet"],
                                 cwd=ROOT, env=dict(os.environ, **self.env), capture_output=True, text=True, timeout=15)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertTrue(json.loads(process.stdout)["data"]["simulation"])

    def test_argument_errors_are_json_with_exit_two(self):
        for argv in (("demo", "--policy", "unknown"), ("unknown-command",), ("run", "--exe")):
            with self.subTest(argv=argv):
                code, result, _ = self.call(*argv)
                self.assertEqual(code, 2)
                self.assertEqual(result["error"]["type"], "usage")

    def test_common_flags_work_before_and_after_command(self):
        self.config()
        first = self.call("--config", str(self.path), "--json", "status")[1]
        second = self.call("status", "--config", str(self.path))[1]
        self.assertEqual(first["data"], second["data"])

    def test_noninteractive_init_never_prompts_or_connects(self):
        with patch("builtins.input", side_effect=AssertionError("must not prompt")), self.stub_api() as api:
            code, _, _ = self.call("init", "--config", str(self.path))
        self.assertEqual(code, 2)
        api.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_noninteractive_auth_never_reads_terminal(self):
        with patch("getpass.getpass", side_effect=AssertionError("must not prompt")):
            code, _, _ = self.call("auth")
        self.assertEqual(code, 2)
        self.assertFalse(self.auth.exists())

    def test_auth_from_local_environment_does_not_echo_secret(self):
        token = "fictional-cli-auth-token"
        with patch.dict(os.environ, {"AUTODL_WEB_TOKEN": token}), patch("autodl_rent.cli.AutoDL") as api:
            code, result, err = self.call("auth")
        self.assertEqual(code, 0)
        api.return_value.images.assert_called_once_with()
        self.assertNotIn(token, json.dumps(result) + err)
        self.assertEqual(json.loads(self.auth.read_text())["token"], token)

    def test_web_token_auth_works_without_wechat_or_echoing_token(self):
        token = "fictional-manual-web-token"
        out, err = io.StringIO(), io.StringIO()
        with patch("sys.stdin.isatty", return_value=True), patch("getpass.getpass", return_value=token), \
                patch("autodl_rent.cli.AutoDL") as api, patch("autodl_rent.cli.login_with_qr") as qr, \
                redirect_stdout(out), redirect_stderr(err):
            code = main(["auth"])
        self.assertEqual(code, 0)
        api.return_value.images.assert_called_once_with()
        qr.assert_not_called()
        self.assertEqual(json.loads(self.auth.read_text())["token"], token)
        self.assertNotIn(token, out.getvalue() + err.getvalue())

    def test_qr_login_emits_image_event_and_keeps_token_local(self):
        token = "fictional-scanned-token"
        image = self.folder / "test-login-qr.jpg"

        def scan(api, path, **kwargs):
            kwargs["show"](image)
            return token

        with patch("autodl_rent.cli.AutoDL") as api, patch("autodl_rent.cli.login_with_qr", side_effect=scan), patch("webbrowser.open") as browser:
            code, result, err = self.call("login", "--no-open")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(err), {"event": "login_qr", "path": str(image)})
        self.assertNotIn(token, json.dumps(result) + err)
        browser.assert_not_called()
        api.return_value.images.assert_called_once_with()
        self.assertEqual(json.loads(self.auth.read_text())["token"], token)

    def test_invalid_login_timeout_does_not_request_challenge(self):
        with patch("autodl_rent.cli.login_with_qr") as qr:
            code, _, _ = self.call("login", "--timeout", "nan")
        self.assertEqual(code, 2)
        qr.assert_not_called()

    def test_complete_init_flags_work_offline(self):
        code, result, _ = self.call("init", "--config", str(self.path), "--region", "西北B区", "--region", "北京B区",
                                   "--gpu", "RTX 4090", "--gpu", "RTX 3090", "--max-price", "3", "--budget", "6",
                                   "--quantity", "2", "--policy", "total", "--total", "3", "--image", "test-image", "--cuda", "12.8")
        self.assertEqual(code, 0)
        config = load_config(self.path)
        self.assertEqual(config.total_instances, 3)
        self.assertEqual(config.targets[1].regions, ("西北B区", "北京B区"))
        self.assertEqual(config.targets[0].quantity, 2)
        self.assertEqual(result["data"]["plan"]["image"]["uuid"], "test-image")
        self.assertFalse(config.state_file.exists())

    def test_incomplete_or_invalid_init_leaves_no_config(self):
        base = ("init", "--config", str(self.path), "--region", "西北B区", "--gpu", "RTX 4090", "--max-price", "3")
        for extra in ((), ("--budget", "-1"), ("--budget", "3", "--policy", "total"),
                      ("--budget", "3", "--policy", "total", "--total", "2")):
            with self.subTest(extra=extra):
                self.assertEqual(self.call(*base, *extra)[0], 2)
                self.assertFalse(self.path.exists())

    def test_init_does_not_overwrite_existing_config(self):
        self.config()
        before = self.path.read_bytes()
        self.assertEqual(self.call("init", "--config", str(self.path), "--template")[0], 2)
        self.assertEqual(self.path.read_bytes(), before)

    def test_new_config_cannot_reuse_orphaned_job_state(self):
        state_file = self.folder / ".local" / "config.state.json"
        write_private_json(state_file, {"preserve": "orphaned ledger"})
        self.assertEqual(self.call("init", "--config", str(self.path), "--template")[0], 2)
        self.assertFalse(self.path.exists())
        self.assertEqual(json.loads(state_file.read_text()), {"preserve": "orphaned ledger"})

    def test_validate_and_status_are_offline(self):
        self.config()
        with patch("autodl_rent.cli.make_api", side_effect=AssertionError("must stay offline")):
            code, result, _ = self.call("validate", "--config", str(self.path))
            self.assertEqual(code, 0)
            self.assertTrue(result["data"]["valid"])
            self.assertFalse(result["data"]["online_checked"])
            self.assertEqual(self.call("status", "--config", str(self.path))[0], 0)

    def test_online_validation_does_not_quote_or_create(self):
        self.config()
        self.api.quote = Mock(side_effect=AssertionError("validation must not quote"))
        self.api.create = Mock(side_effect=AssertionError("validation must not create"))
        with self.stub_api():
            code, result, _ = self.call("validate", "--config", str(self.path), "--online")
        self.assertEqual(code, 0)
        self.assertTrue(result["data"]["online_checked"])
        self.api.create.assert_not_called()

    def test_preview_has_structured_offers_without_creating_ledger(self):
        config = self.config()
        self.api.create = Mock(side_effect=AssertionError("preview must not create"))
        self.api.quote = Mock(side_effect=AssertionError("preview must not quote"))
        with self.stub_api():
            code, result, _ = self.call("run", "--config", str(self.path))
        self.assertEqual(code, 0)
        self.assertEqual(result["data"]["mode"], "preview")
        self.assertEqual(result["data"]["matches"], 2)
        self.assertEqual(len(result["data"]["offers"]), 2)
        self.assertFalse(config.state_file.exists())

    def test_completed_task_resumes_without_auth_or_network(self):
        self.config()
        with self.stub_api():
            self.assertEqual(self.call("run", "--config", str(self.path), "--execute", "--once")[0], 0)
        with patch("autodl_rent.cli.make_api", side_effect=AssertionError("completed task needs no API")):
            code, result, _ = self.call("run", "--config", str(self.path), "--execute")
        self.assertEqual(code, 0)
        self.assertEqual(result["data"]["count"], 1)
        self.assertEqual(len(self.api.created), 1)

    def test_transient_preview_failure_is_structured_in_json(self):
        self.config()
        self.api.regions = Mock(side_effect=TransportError("HTTP503", transient=True, retry_after=12))
        with self.stub_api():
            code, result, _ = self.call("run", "--config", str(self.path))
        self.assertEqual(code, 2)
        self.assertEqual(result["error"]["code"], "HTTP503")
        self.assertEqual(result["error"]["retry_after"], 12)
        self.assertTrue(result["error"]["transient"])

    def test_uncertain_create_preserves_pending_and_returns_exit_four(self):
        config = self.config()
        self.api.create = Mock(side_effect=TransportError("HTTP500", transient=True))
        with self.stub_api():
            code, result, _ = self.call("run", "--config", str(self.path), "--execute", "--once")
        self.assertEqual(code, 4)
        self.assertEqual(result["error"]["type"], "pending_reconciliation")
        self.assertIsNotNone(result["data"]["pending"])
        self.assertIsNotNone(State(config).pending)
        with patch("autodl_rent.cli.make_api", side_effect=AssertionError("must not retry creation")):
            self.assertEqual(self.call("run", "--config", str(self.path), "--execute")[0], 4)
        self.api.create.assert_called_once()

    def test_market_query_failure_is_not_reported_as_empty_success(self):
        self.config()
        self.api.machines = Mock(side_effect=TransportError("HTTP503", transient=True))
        with self.stub_api():
            code, result, _ = self.call("run", "--config", str(self.path))
        self.assertEqual(code, 2)
        self.assertEqual(result["error"]["code"], "HTTP503")
        self.assertFalse(result["ok"])

    def test_known_pending_id_is_confirmed_without_creating_again(self):
        config = self.config()
        state = State(config)
        state.begin({"target": "4090", "gpu_model": "RTX 4090", "region_name": "西北B区", "price_per_hour": "2"})
        state.attach_instance("test-existing-instance")
        self.api.exists = Mock(return_value=True)
        self.api.create = Mock(side_effect=AssertionError("must not create again"))
        with self.stub_api():
            code, result, _ = self.call("run", "--config", str(self.path), "--execute", "--once")
        self.assertEqual(code, 0)
        self.assertIsNone(result["data"]["pending"])
        self.assertEqual(result["data"]["count"], 1)

    def test_once_returns_partial_progress_with_exit_three(self):
        self.config(policy="all")
        self.api.offers = self.api.offers[:1]
        with self.stub_api():
            code, result, _ = self.call("run", "--config", str(self.path), "--execute", "--once")
        self.assertEqual(code, 3)
        self.assertEqual(result["data"]["count"], 1)
        self.assertFalse(result["data"]["done"])

    def test_invalid_wait_does_not_connect(self):
        self.config()
        with self.stub_api() as api:
            self.assertEqual(self.call("run", "--config", str(self.path), "--wait-seconds", "nan")[0], 2)
        api.assert_not_called()

    def test_interrupt_after_submission_retains_pending_in_json(self):
        config = self.config()
        self.api.create = Mock(side_effect=KeyboardInterrupt)
        with self.stub_api():
            code, result, _ = self.call("run", "--config", str(self.path), "--execute")
        self.assertEqual(code, 130)
        self.assertIsNotNone(result["data"]["pending"])
        self.assertIsNotNone(State(config).pending)

    def test_resolve_requires_actual_console_check_assertion(self):
        config = self.config()
        state = State(config)
        state.begin({"target": "4090", "price_per_hour": "2"})
        code, result, _ = self.call("resolve", "--config", str(self.path), "--not-created")
        self.assertEqual(code, 2)
        self.assertIsNotNone(result["data"]["pending"])
        self.assertIsNotNone(State(config).pending)

    def test_api_errors_keep_code_and_retry_information(self):
        self.config()
        with patch("autodl_rent.cli.make_api", side_effect=APIError("AuthorizeFailed", "please login")):
            code, result, _ = self.call("run", "--config", str(self.path))
        self.assertEqual(code, 2)
        self.assertEqual(result["error"]["code"], "AuthorizeFailed")
        self.assertFalse(result["error"]["transient"])

    def test_doctor_reports_onboarding_without_network_or_secrets(self):
        with patch("autodl_rent.cli.make_api", side_effect=AssertionError("offline doctor")):
            code, result, _ = self.call("doctor", "--config", str(self.path))
        self.assertEqual(code, 0)
        self.assertFalse(result["data"]["ready"])
        self.assertFalse(result["data"]["online_checked"])

    def test_malformed_config_is_reported_as_diagnostic_failure(self):
        self.path.write_text("{}")
        code, result, _ = self.call("doctor", "--config", str(self.path))
        self.assertEqual(code, 2)
        self.assertFalse(result["data"]["ready"])

    def test_agent_contract_and_resources_are_available_offline(self):
        code, result, _ = self.call("agent")
        self.assertEqual(code, 0)
        self.assertEqual(result["data"]["entrypoint"], ["python3", "-m", "autodl_rent"])
        methods = {item["command"]: item for item in result["data"]["commands"]}
        self.assertFalse(methods["auth"]["requires_wechat_binding"])
        self.assertTrue(methods["login"]["requires_wechat_binding"])
        schema = self.call("agent", "schema")[1]["data"]["schema"]
        self.assertEqual(schema, config_schema())
        pattern = schema["properties"]["image"]["properties"]["cuda_version"]["pattern"]
        self.assertIsNotNone(re.fullmatch(pattern, "12.8"))
        self.assertTrue(self.call("agent", "skill")[1]["data"]["skill"].startswith("---\n"))

    def quick_api(self):
        self.api.offers = [demo_machine(model="CPU", identifier="test-cpu-host", price=200)]
        self.api.images = Mock(return_value=[{"uuid": "demo-image", "cuda_version": "11.8", "chip_corp": "nvidia",
                                             "cpu_arch": "x86", "label": "Miniconda / conda3 / 3.10 / CUDA 11.8"}])

    def test_quick_previews_and_reuses_completed_task(self):
        self.quick_api()
        path = self.folder / "config.west-under1.local.json"
        with self.stub_api():
            code, result, _ = self.call("--config", str(path), "quick")
            self.assertEqual(code, 0)
            self.assertEqual(result["data"]["matches"], 1)
            self.assertEqual(self.api.created, [])
            self.assertEqual(self.call("quick", "--config", str(path), "--execute", "--once")[0], 0)
        with patch("autodl_rent.cli.make_api", side_effect=AssertionError("completed quick task stays offline")):
            code, result, _ = self.call("quick", "--config", str(path), "--execute")
        self.assertEqual(code, 0)
        self.assertEqual(result["data"]["count"], 1)
        self.assertEqual(len(self.api.created), 1)
        self.assertEqual(load_config(path).state_file.name, "west-under1.state.json")
        self.assertEqual(self.call("status", "--config", str(path))[1]["data"]["count"], 1)

    def test_quick_rejects_broadened_contract_without_network(self):
        self.config()
        before = self.path.read_bytes()
        with self.stub_api() as api:
            self.assertEqual(self.call("quick", "--config", str(self.path), "--execute")[0], 2)
        api.assert_not_called()
        self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_quick_wait_leaves_no_config_or_api_call(self):
        with self.stub_api() as api:
            self.assertEqual(self.call("quick", "--config", str(self.path), "--wait-seconds", "nan")[0], 2)
        api.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_single_choice_reprompts_instead_of_discarding_setup(self):
        with patch("builtins.input", side_effect=["1,2", "", "2"]), redirect_stdout(io.StringIO()):
            self.assertEqual(choose_many("policy", ["any", "all"], single=True), ["all"])


if __name__ == "__main__":
    unittest.main()
