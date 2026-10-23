import io
import json
import os
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

from autodl_rent.api import APIError, AutoDL, TransportError, flatten_images, resolve_regions, retry_delay
from autodl_rent.cli import main, validate_token
from autodl_rent.config import ConfigError, example_config, load_config
from autodl_rent.demo import DemoAPI, demo_machine
from autodl_rent.runner import Runner, UncertainResult, allowed_gpu_count, candidate
from autodl_rent.qr_auth import login_with_qr, qr_image
from autodl_rent.quick_rent import select_quick_image
from autodl_rent.state import State, state_lock, write_private_json


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class RentTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "config.json"
        self.raw = example_config()
        self.raw["image"]["uuid"] = "demo-image"
        self.raw["poll"]["jitter_seconds"] = 0
        self.clock = FakeClock()
        self.api = DemoAPI()

    def config(self):
        write_private_json(self.path, self.raw)
        return load_config(self.path)

    def runner(self, *, execute=True, state=None):
        config = self.config()
        return Runner(config, self.api, state or State(config), execute=execute,
                      monotonic=self.clock.now, sleep=self.clock.sleep, jitter=lambda a, b: 0)

    def test_any_stops_immediately_after_first_success(self):
        runner = self.runner()
        self.assertEqual(runner.run(once=True), 0)
        self.assertEqual(len(self.api.created), 1)
        self.assertEqual(runner.state.count("4090"), 1)
        self.assertEqual(runner.state.count("3090"), 0)
        self.assertFalse(any(query[0] == "3090" for query in self.api.queries))

    def test_all_counts_each_target(self):
        self.raw["stop"]["policy"] = "all"
        runner = self.runner()
        self.assertEqual(runner.run(once=True), 0)
        self.assertEqual(len(self.api.created), 2)
        self.assertTrue(runner.state.done())

    def test_total_respects_threshold_and_each_quota(self):
        self.raw["stop"] = {"policy": "total", "total_instances": 2}
        self.raw["targets"][0]["quantity"] = 4
        self.api.offers.insert(1, demo_machine(identifier="demo-4090-b"))
        runner = self.runner()
        self.assertEqual(runner.run(once=True), 0)
        self.assertEqual(len(self.api.created), 2)
        self.assertEqual(runner.state.count("4090"), 2)
        self.assertEqual(runner.state.count("3090"), 0)

    def test_target_quota_prevents_extra_same_model_rental(self):
        self.raw["stop"]["policy"] = "all"
        self.api.offers.insert(1, demo_machine(identifier="demo-4090-b"))
        runner = self.runner()
        self.assertEqual(runner.run(once=True), 0)
        self.assertEqual([x["machine_id"] for x in self.api.created], ["demo-4090", "demo-3090"])

    def test_dry_run_does_not_create_quote_or_write_ledger(self):
        self.api.create = Mock(side_effect=AssertionError("paid API must not be called"))
        self.api.quote = Mock(side_effect=AssertionError("dry preview only reads the market"))
        runner = self.runner(execute=False)
        self.assertEqual(runner.run(), 0)
        self.assertFalse(runner.state.path.exists())
        self.api.create.assert_not_called()
        self.api.quote.assert_not_called()

    def test_cheapest_can_select_a_later_target(self):
        self.raw["selection"] = "cheapest"
        self.assertEqual(self.runner().run(once=True), 0)
        self.assertEqual(self.api.created[0]["machine_id"], "demo-3090")

    def test_multi_gpu_is_one_instance_and_price_multiplies(self):
        self.raw["targets"][0]["gpus_per_instance"] = 2
        runner = self.runner()
        self.assertEqual(runner.run(once=True), 0)
        self.assertEqual(self.api.created[0]["gpu_count"], 2)
        self.assertEqual(runner.state.hourly_total, Decimal("4"))
        self.assertEqual(runner.state.count(), 1)

    def test_exact_model_matching_does_not_accept_4090d(self):
        self.api.offers = [demo_machine(model="RTX 4090D")]
        runner = self.runner()
        runner.prepare()
        target = runner.config.targets[0]
        self.assertIsNone(candidate(self.api.offers[0], target, ["west-B"], runner.config.image, runner.gpus))
        self.assertEqual(runner.run(once=True), 3)
        self.assertFalse(self.api.created)

    def test_server_filter_is_not_trusted_for_region_or_gpu(self):
        runner = self.runner()
        runner.prepare()
        target = runner.config.targets[0]
        for patch_values in ({"region_sign": "other"}, {"gpu_name": "RTX 4090D"},
                             {"on_line": 3}, {"binding_instance_num": 8}, {"gpu_idle_num": 0},
                             {"chip_corp": "huawei"}, {"cpu_arch": "arm"}, {"machine_sku_info": []},
                             {"highest_cuda_version": "11.8"}, {"machine_sku_info": [{"current_price": "2.0"}]}):
            with self.subTest(patch=patch_values):
                host = dict(demo_machine(), **patch_values)
                self.assertIsNone(candidate(host, target, ["west-B"], runner.config.image, runner.gpus))

    def test_min_memory_cpu_and_vram_are_checked(self):
        self.raw["targets"][0].update(min_gpu_memory_gb=32, min_cpu_per_gpu=32, min_memory_per_gpu_gb=96)
        runner = self.runner()
        runner.prepare()
        host = demo_machine()
        self.assertIsNone(candidate(host, runner.config.targets[0], ["west-B"], runner.config.image, runner.gpus))
        host.update(gpu_memory=48 * 1024**3, cpu_per_gpu=32, mem_per_gpu=96 * 1024**3)
        self.assertIsNotNone(candidate(host, runner.config.targets[0], ["west-B"], runner.config.image, runner.gpus))

    def test_blackwell_rejects_old_cuda(self):
        self.raw["targets"][0]["gpu_models"] = ["RTX 5090"]
        self.raw["image"]["cuda_version"] = "11.8"
        runner = self.runner(execute=False)
        gpu = {"RTX 5090": {"gpu_series_type": "NVIDIA-Blackwell"}}
        self.assertIsNone(candidate(demo_machine(model="RTX 5090"), runner.config.targets[0], ["west-B"], runner.config.image, gpu))

    def test_cpu_can_be_rented_without_gpu_or_cuda_metadata(self):
        self.raw["targets"][0]["gpu_models"] = ["CPU"]
        self.raw["targets"][0]["max_price_per_gpu_hour"] = 0.999
        self.raw["targets"] = self.raw["targets"][:1]
        host = demo_machine(model="CPU", identifier="cpu-host", price=200)
        del host["highest_cuda_version"]
        del host["gpu_memory"]
        host["chip_corp"] = "cpu"
        self.api.offers = [host]
        runner = self.runner()
        self.assertEqual(runner.run(once=True), 0)
        self.assertEqual(len(self.api.created), 1)
        self.assertEqual(runner.state.hourly_total, Decimal("0.2"))

    def test_cpu_is_excluded_when_gpu_memory_is_required(self):
        self.raw["targets"][0].update(gpu_models=["CPU"], min_gpu_memory_gb=1)
        self.api.offers = [demo_machine(model="CPU", identifier="cpu-host", price=200)]
        runner = self.runner()
        self.assertEqual(runner.run(once=True), 3)
        self.assertFalse(self.api.created)

    def test_under_one_yuan_cap_does_not_accept_exactly_one_yuan(self):
        self.raw["targets"] = self.raw["targets"][:1]
        self.raw["targets"][0].update(gpu_models=["CPU"], max_price_per_gpu_hour=0.999)
        self.api.offers = [demo_machine(model="CPU", identifier="cpu-one-yuan", price=1000)]
        runner = self.runner()
        self.assertEqual(runner.run(once=True), 3)
        self.assertFalse(self.api.created)

    def test_base_and_multiple_gpu_modes(self):
        host = demo_machine()
        host["rent_mode"] = "Base2"
        self.assertFalse(allowed_gpu_count(host, 1))
        self.assertTrue(allowed_gpu_count(host, 3))
        host["rent_mode"] = "Multiple2"
        self.assertFalse(allowed_gpu_count(host, 3))
        self.assertTrue(allowed_gpu_count(host, 4))
        host["gpu_idle_num"] = 1
        self.assertTrue(allowed_gpu_count(host, 1))
        host["rent_mode"] = "unknown"
        self.assertFalse(allowed_gpu_count(host, 1))

    def test_total_budget_is_rechecked_after_each_creation(self):
        self.raw["stop"]["policy"] = "all"
        self.raw["max_total_price_per_hour"] = 2
        runner = self.runner()
        self.assertEqual(runner.run(once=True), 3)
        self.assertEqual(len(self.api.created), 1)
        self.assertLessEqual(runner.state.hourly_total, Decimal(2))

    def test_price_increase_before_creation_skips_host(self):
        self.api.quote = Mock(return_value=Decimal("99"))
        runner = self.runner()
        self.assertEqual(runner.run(once=True), 3)
        self.assertEqual(self.api.created, [])
        self.assertIsNone(runner.state.pending)

    def test_stock_disappeared_on_host_refresh_does_not_create(self):
        original = self.api.machines

        def response(target, regions, page, page_size, machine_id=""):
            return {"list": []} if machine_id else original(target, regions, page, page_size)

        self.api.machines = response
        runner = self.runner()
        self.assertEqual(runner.run(once=True), 3)
        self.assertEqual(self.api.created, [])

    def test_known_stock_rejection_can_continue_to_another_target(self):
        original = self.api.create

        def create(machine_id, *args):
            if machine_id == "demo-4090":
                raise APIError("MachineGpuNumUpdateFailed", "sold out")
            return original(machine_id, *args)

        self.api.create = create
        runner = self.runner()
        self.assertEqual(runner.run(once=True), 0)
        self.assertEqual(len(self.api.created), 1)
        self.assertEqual(runner.state.count("3090"), 1)
        self.assertIsNone(runner.state.pending)

    def test_auth_or_balance_rejection_stops_and_is_not_retried(self):
        self.api.create = Mock(side_effect=APIError("TORecharge", "balance"))
        runner = self.runner()
        with self.assertRaises(APIError):
            runner.run()
        self.assertEqual(self.api.create.call_count, 1)
        self.assertIsNone(runner.state.pending)

    def test_pending_is_written_before_paid_request(self):
        def inspect_before_submit(*args):
            state = State(self.config())
            self.assertIsNotNone(state.pending)
            self.assertIsNone(state.pending["instance_uuid"])
            raise TransportError("NetworkError", "timeout", transient=True)

        self.api.create = Mock(side_effect=inspect_before_submit)
        runner = self.runner()
        with self.assertRaises(UncertainResult):
            runner.run()
        self.assertEqual(self.api.create.call_count, 1)
        self.assertIsNotNone(State(runner.config).pending)

    def test_unknown_create_response_is_not_retried(self):
        self.api.create = Mock(side_effect=APIError("UnrecognizedServerError", "unknown"))
        runner = self.runner()
        with self.assertRaises(UncertainResult):
            runner.run()
        self.assertEqual(self.api.create.call_count, 1)
        self.assertIsNotNone(runner.state.pending)

    def test_http_500_during_creation_is_uncertain_not_a_read_retry(self):
        self.api.create = Mock(side_effect=TransportError("HTTP500", "error", transient=True))
        runner = self.runner()
        with self.assertRaises(UncertainResult):
            runner.run()
        self.assertEqual(self.api.create.call_count, 1)
        self.assertIsNotNone(runner.state.pending)

    def test_restart_without_known_instance_id_refuses_to_create(self):
        runner = self.runner()
        runner.state.begin({"target": "4090", "price_per_hour": "2", "machine_id": "m1"})
        self.api.create = Mock(side_effect=AssertionError("must not retry"))
        with self.assertRaises(UncertainResult):
            self.runner().run()
        self.api.create.assert_not_called()

    def test_restart_can_confirm_known_id_without_another_create(self):
        runner = self.runner()
        runner.state.begin({"target": "4090", "price_per_hour": "2", "machine_id": "m1"})
        runner.state.attach_instance("existing-id")
        self.api.exists = Mock(return_value=True)
        self.api.create = Mock(side_effect=AssertionError("must not retry"))
        resumed = self.runner()
        self.assertEqual(resumed.run(), 0)
        self.assertEqual(resumed.state.count(), 1)
        self.api.create.assert_not_called()

    def test_confirmation_timeout_keeps_id_and_never_submits_again(self):
        self.raw["poll"]["confirm_timeout_seconds"] = 3
        self.api.exists = Mock(return_value=False)
        runner = self.runner()
        with self.assertRaises(UncertainResult):
            runner.run()
        self.assertEqual(len(self.api.created), 1)
        self.assertEqual(State(runner.config).pending["instance_uuid"], "demo-instance-1")
        self.assertEqual(runner.state.count(), 0)

    def test_existing_complete_ledger_does_not_rent_again(self):
        first = self.runner()
        first.run()
        resumed = self.runner()
        self.assertEqual(resumed.run(), 0)
        self.assertEqual(len(self.api.created), 1)

    def test_retry_after_is_respected_beyond_configured_backoff(self):
        self.raw["max_wait_seconds"] = 70
        self.raw["poll"]["max_backoff_seconds"] = 5
        self.api.machines = Mock(side_effect=TransportError("HTTP429", transient=True, retry_after=100))
        runner = self.runner()
        self.assertEqual(runner.run(), 3)
        self.assertEqual(self.api.machines.call_count, 1)
        self.assertEqual(self.clock.value, 70)

    def test_read_transient_error_can_recover_without_duplicate_create(self):
        original = self.api.machines
        first = True

        def intermittent(*args, **kwargs):
            nonlocal first
            if first:
                first = False
                raise TransportError("HTTP503", transient=True)
            return original(*args, **kwargs)

        self.api.machines = intermittent
        runner = self.runner()
        self.assertEqual(runner.run(), 0)
        self.assertEqual(len(self.api.created), 1)
        self.assertGreaterEqual(self.clock.value, 20)

    def test_initial_catalog_transient_error_can_recover(self):
        regions = self.api.regions()
        self.api.regions = Mock(side_effect=[TransportError("HTTP503", transient=True), regions])
        runner = self.runner()
        self.assertEqual(runner.run(), 0)
        self.assertEqual(len(self.api.created), 1)
        self.assertGreaterEqual(self.clock.value, 20)

    def test_confirmation_does_not_retry_before_retry_after_when_deadline_expires(self):
        self.raw["poll"]["confirm_timeout_seconds"] = 30
        self.api.exists = Mock(side_effect=TransportError("HTTP429", transient=True, retry_after=100))
        runner = self.runner()
        with self.assertRaises(UncertainResult):
            runner.run()
        self.assertEqual(self.api.exists.call_count, 1)
        self.assertEqual(self.clock.value, 30)
        self.assertIsNotNone(runner.state.pending)

    def test_pages_after_first_are_searched(self):
        self.raw["poll"]["page_size"] = 1
        self.api.offers.insert(0, demo_machine(identifier="bad-memory"))
        self.api.offers[0]["gpu_memory"] = 8 * 1024**3
        self.raw["targets"][0]["min_gpu_memory_gb"] = 20
        runner = self.runner()
        self.assertEqual(runner.run(once=True), 0)
        self.assertEqual(self.api.created[0]["machine_id"], "demo-4090")
        self.assertIn(("4090", 2, 1, ""), self.api.queries)

    def test_affordable_sold_out_models_stay_on_fast_poll_path(self):
        self.raw["targets"] = self.raw["targets"][:1]
        self.raw["targets"][0].update(gpu_models=["CPU", "RTX 4090"], max_price_per_gpu_hour=0.999)
        host = demo_machine(model="CPU", identifier="cpu", price=200)
        host["gpu_idle_num"] = 0
        self.api.offers = [host, demo_machine()]
        runner = self.runner()
        runner.prepare()
        runner.deadline = 1000
        original = self.api.machines
        self.api.machines = Mock(wraps=original)
        self.assertEqual(list(runner.candidates()), [])
        self.api.offers[0]["gpu_idle_num"] = 1
        self.assertEqual(len(list(runner.candidates())), 1)
        self.assertEqual(self.api.machines.call_args.args[0].gpu_models, ("CPU",))

    def test_full_price_rescan_discovers_newly_affordable_models(self):
        self.raw["targets"] = self.raw["targets"][:1]
        self.raw["targets"][0].update(gpu_models=["CPU", "RTX 4090"], max_price_per_gpu_hour=0.999)
        cpu = demo_machine(model="CPU", identifier="cpu", price=200)
        cpu["gpu_idle_num"] = 0
        self.api.offers = [cpu, demo_machine()]
        runner = self.runner()
        runner.prepare()
        runner.deadline = 1000
        self.assertEqual(list(runner.candidates()), [])
        self.api.offers[1]["machine_sku_info"][0]["current_price"] = 800
        self.clock.sleep(121)
        self.assertEqual([offer[1]["gpu_model"] for offer in runner.candidates()], ["RTX 4090"])

    def test_config_changes_cannot_silently_reset_existing_job(self):
        runner = self.runner()
        runner.run()
        self.raw["targets"][0]["quantity"] = 2
        with self.assertRaises(ConfigError):
            State(self.config())

    def test_poll_timing_can_change_without_resetting_progress(self):
        runner = self.runner()
        runner.run()
        self.raw["poll"]["interval_seconds"] = 30
        self.assertEqual(State(self.config()).count(), 1)

    def test_lock_prevents_two_processes_using_same_ledger(self):
        config = self.config()
        with state_lock(config.state_file):
            with self.assertRaises(ConfigError):
                with state_lock(config.state_file):
                    self.fail("second lock must fail")

    def test_invalid_config_and_unknown_fields_are_rejected(self):
        for change in ({"selection": "fast"}, {"max_total_price_per_hour": -1},
                       {"stop": {"policy": "total", "total_instances": 99}},
                       {"poll": {"interval_seconds": 0.01}}, {"typo": True}):
            with self.subTest(change=change):
                self.raw = dict(example_config(), **change)
                with self.assertRaises(ConfigError):
                    self.config()

    def test_config_relative_state_path_and_private_permissions(self):
        config = self.config()
        self.assertEqual(config.state_file.parent, (self.path.parent / ".local").resolve())
        if os.name != "nt":
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_duplicate_success_id_cannot_be_counted_twice(self):
        runner = self.runner()
        runner.run()
        runner.state.begin({"target": "4090", "price_per_hour": "2"})
        runner.state.attach_instance("demo-instance-1")
        with self.assertRaises(ConfigError):
            runner.state.confirm()

    def test_resolve_not_created_requires_console_check_flag(self):
        config = self.config()
        state = State(config)
        state.begin({"target": "4090", "price_per_hour": "2", "machine_id": "m1"})
        self.assertEqual(main(["resolve", "--config", str(self.path), "--not-created"]), 2)
        self.assertIsNotNone(State(config).pending)
        self.assertEqual(main(["resolve", "--config", str(self.path), "--not-created", "--checked-console"]), 0)
        self.assertIsNone(State(config).pending)

    def test_status_does_not_require_authentication_or_network(self):
        self.config()
        with patch("autodl_rent.cli.make_api", side_effect=AssertionError("no network")):
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["status", "--config", str(self.path)]), 0)
            self.assertIn("4090: 0/1", output.getvalue())


class APITests(unittest.TestCase):
    def test_region_name_expands_all_underlying_partitions(self):
        catalog = [{"region_name": "西北B区", "region_sign": "west-B"},
                   {"region_name": "西北B区", "region_sign": "west-C"}]
        self.assertEqual(resolve_regions(("西北B区",), catalog), ["west-B", "west-C"])
        self.assertEqual(resolve_regions(("west-C",), catalog), ["west-C"])
        with self.assertRaises(ConfigError):
            resolve_regions(("西北",), catalog)

    def test_image_catalog_tree_is_flattened_with_exact_uuid_and_cuda(self):
        rows = [{"label_name": "PyTorch", "children": [{"label_name": "2.8", "children": [
            {"label_name": "3.12", "children": [{"label_name": {"uuid": "base-one", "v": "12.8", "chip_corp": "nvidia", "cpu_arch": "x86"}}]}]}]}]
        self.assertEqual(flatten_images(rows), [{"uuid": "base-one", "cuda_version": "12.8", "chip_corp": "nvidia", "cpu_arch": "x86", "label": "PyTorch / 2.8 / 3.12 / CUDA 12.8"}])

    def test_create_payload_matches_verified_web_schema(self):
        api = AutoDL("not-a-real-token", request_gap=0)
        api.request = Mock(return_value="instance-one")
        image = {"kind": "base", "uuid": "base-one"}
        self.assertEqual(api.create("host-one", 2, image, "job-one"), "instance-one")
        method, path, body = api.request.call_args.args
        self.assertEqual((method, path), ("POST", "/api/v1/order/instance/create/payg"))
        self.assertEqual(body["instance_info"]["req_gpu_amount"], 2)
        self.assertEqual(body["instance_info"]["base_image_uuid"], "base-one")
        self.assertEqual(body["instance_info"]["expand_data_disk"], 0)
        self.assertEqual(body["price_info"]["num"], 2)
        self.assertEqual(body["price_info"]["charge_type"], "payg")

    def test_private_image_uses_private_field_only(self):
        api = AutoDL(request_gap=0)
        api.request = Mock(return_value="instance-one")
        api.create("host-one", 1, {"kind": "private", "uuid": "private-one"}, "job-one")
        info = api.request.call_args.args[2]["instance_info"]
        self.assertEqual(info["private_image_uuid"], "private-one")
        self.assertEqual(info["base_image_uuid"], "")

    def test_success_without_instance_id_is_uncertain(self):
        api = AutoDL(request_gap=0)
        api.request = Mock(return_value={"uuid": "new-schema"})
        with self.assertRaises(TransportError):
            api.create("host", 1, {"kind": "base", "uuid": "image"}, "name")

    def test_price_unit_is_thousandths_of_yuan(self):
        api = AutoDL(request_gap=0)
        api.request = Mock(return_value={"origin_price": 2520})
        self.assertEqual(api.quote("host", 2), Decimal("2.52"))
        api.request = Mock(return_value={"origin_price": "2.52"})
        with self.assertRaises(APIError):
            api.quote("host", 2)

    def test_http_error_is_not_retried_in_client(self):
        api = AutoDL(request_gap=0)
        error = urllib.error.HTTPError("https://www.autodl.com/api/v1/order/instance/create/payg", 500, "error", {}, None)
        api._opener = Mock()
        api._opener.open.side_effect = error
        with self.assertRaises(TransportError):
            api.request("POST", "/api/v1/order/instance/create/payg", {})
        self.assertEqual(api._opener.open.call_count, 1)

    def test_token_is_redacted_from_api_message(self):
        token = "fictional-secret-token"
        api = AutoDL(token, request_gap=0)
        response = Mock()
        response.read.return_value = json.dumps({"code": "Error", "msg": token}).encode()
        api._opener = Mock()
        api._opener.open.return_value.__enter__ = Mock(return_value=response)
        api._opener.open.return_value.__exit__ = Mock(return_value=False)
        with self.assertRaises(APIError) as result:
            api.request("GET", "/api/v1/instance/exist")
        self.assertNotIn(token, str(result.exception))

    def test_token_validation_does_not_echo_invalid_secret(self):
        with self.assertRaises(ConfigError) as result:
            validate_token("secret\nheader-injection")
        self.assertNotIn("secret", str(result.exception))

    def test_token_crossing_error_message_limit_is_fully_redacted(self):
        for token, prefix in (("fictional-" + "x" * 400, ""),
                              ("fictional-boundary-token", "a" * 285)):
            with self.subTest(token_length=len(token), prefix_length=len(prefix)):
                api = AutoDL(token, request_gap=0)
                response = Mock()
                response.read.return_value = json.dumps({"code": "Error", "msg": prefix + token}).encode()
                api._opener = Mock()
                api._opener.open.return_value.__enter__ = Mock(return_value=response)
                api._opener.open.return_value.__exit__ = Mock(return_value=False)
                with self.assertRaises(APIError) as result:
                    api.request("GET", "/api/v1/instance/exist")
                self.assertNotIn(token[:15], str(result.exception))
                self.assertIn("[redacted]", str(result.exception))

    def test_retry_after_seconds(self):
        self.assertEqual(retry_delay("100"), 100)
        self.assertEqual(retry_delay("nonsense"), 0)

    def test_cpu_query_does_not_filter_out_cpu_chip_corp(self):
        from autodl_rent.config import Target
        api = AutoDL(request_gap=0)
        api.request = Mock(return_value={"list": []})
        target = Target("cpu", ("西北B区",), ("CPU",), 1, 1, Decimal("0.999"))
        api.machines(target, ["west-B"], 1, 90)
        self.assertEqual(api.request.call_args.args[2]["chip_corp"], [])


class QRLoginTests(unittest.TestCase):
    def test_login_exchanges_only_after_user_scan_and_keeps_token_in_memory(self):
        api = Mock()
        api.request.side_effect = [
            {"uuid": "test-login", "qrcode_url": "https://mp.weixin.qq.com/qrcode", "expire_seconds": 300},
            None, {"openId": "test-open-id", "ticket": "test-ticket"}, {"token": "test-token"},
        ]
        clock = FakeClock()
        with patch("autodl_rent.qr_auth.qr_image", return_value=Path("/tmp/test-qr.jpg")):
            token = login_with_qr(api, Path("/tmp/test-qr.jpg"), clock=clock.now, sleep=clock.sleep, show=lambda p: None)
        self.assertEqual(token, "test-token")
        self.assertEqual(clock.value, 3)
        self.assertEqual(api.request.call_args_list[-1].args[:2], ("POST", "/api/v1/passport"))
        self.assertEqual(api.request.call_args_list[-1].args[2]["ticket"], "test-ticket")

    def test_unbound_wechat_does_not_create_an_account(self):
        api = Mock()
        api.request.side_effect = [
            {"uuid": "test-login", "qrcode_url": "https://mp.weixin.qq.com/qrcode"}, {"openId": "unbound"},
        ]
        with patch("autodl_rent.qr_auth.qr_image", return_value=Path("/tmp/test-qr.jpg")):
            with self.assertRaisesRegex(ConfigError, "autodl_rent login --method token"):
                login_with_qr(api, Path("/tmp/test-qr.jpg"), show=lambda p: None)
        self.assertEqual(api.request.call_count, 2)

    def test_timeout_does_not_exchange_or_rent(self):
        api = Mock()
        api.request.side_effect = [
            {"uuid": "test-login", "qrcode_url": "https://mp.weixin.qq.com/qrcode"}, None,
        ]
        clock = FakeClock()
        with patch("autodl_rent.qr_auth.qr_image", return_value=Path("/tmp/test-qr.jpg")):
            with self.assertRaises(ConfigError):
                login_with_qr(api, Path("/tmp/test-qr.jpg"), timeout=2, clock=clock.now, sleep=clock.sleep, show=lambda p: None)
        self.assertEqual(api.request.call_count, 2)
        self.assertEqual(clock.value, 2)

    def test_qr_image_rejects_unexpected_hosts_or_plain_http(self):
        for url in ("http://mp.weixin.qq.com/code", "https://mp.weixin.qq.com.evil.example/code", "file:///tmp/code"):
            with self.subTest(url=url), self.assertRaises(ConfigError):
                qr_image(url, Path("/tmp/test-qr.jpg"))


class QuickImageTests(unittest.TestCase):
    def test_catalog_flavor_does_not_break_image_selection(self):
        images = [
            {"uuid": "gl", "cuda_version": "11.3(cudagl)", "label": "Miniconda / conda3 / 3.8(ubuntu20.04) / CUDA 11.3(cudagl)"},
            {"uuid": "old", "cuda_version": "11.8", "label": "Miniconda / conda3 / 3.8(ubuntu20.04) / CUDA 11.8"},
            {"uuid": "current", "cuda_version": "11.8", "label": "Miniconda / conda3 / 3.10(ubuntu22.04) / CUDA 11.8"},
        ]
        self.assertEqual(select_quick_image(images)["uuid"], "current")

    def test_unsupported_images_do_not_produce_an_arbitrary_uuid(self):
        with self.assertRaises(ConfigError):
            select_quick_image([{"uuid": "gl", "cuda_version": "11.3(cudagl)", "label": "Miniconda / CUDA variant"}])


if __name__ == "__main__":
    unittest.main()
