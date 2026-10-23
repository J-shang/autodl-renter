"""The CLI's versioned machine protocol and plain terminal rendering."""
from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass, field
from importlib import resources

from . import __version__

PROTOCOL_VERSION = 1
EXIT_CODES = {
    "0": "completed_or_previewed", "2": "invalid_input_or_api_error",
    "3": "not_completed", "4": "pending_reconciliation", "130": "interrupted",
}


@dataclass
class Result:
    data: dict = field(default_factory=dict)
    code: int = 0
    error: dict | None = None


def envelope(command, result: Result, error=None) -> dict:
    return {"schema_version": PROTOCOL_VERSION, "version": __version__,
            "command": command, "ok": result.code == 0, "exit_code": result.code,
            "data": result.data, "error": error or result.error}


def say(args, message=""):
    if not args.json:
        print(message, flush=True)


def table(headers, rows) -> str:
    rows = [[str(value).replace("\n", " ").replace("\r", " ") for value in row] for row in [headers, *rows]]

    def width(value):
        return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in value)

    widths = [max(width(row[i]) for row in rows) for i in range(len(headers))]
    return "\n".join("  ".join(cell + " " * (widths[i] - width(cell)) for i, cell in enumerate(row)).rstrip()
                     for row in rows)


def plan(config) -> dict:
    return {"config_file": str(config.path), "state_file": str(config.state_file),
            "fingerprint": config.fingerprint, "policy": config.policy,
            "total_instances": config.total_instances if config.policy == "total" else None,
            "selection": config.selection, "max_wait_seconds": config.max_wait_seconds,
            "max_total_price_per_hour": str(config.max_total_price_per_hour),
            "image": config.image,
            "targets": [{"name": t.name, "regions": list(t.regions), "gpu_models": list(t.gpu_models),
                         "quantity": t.quantity, "gpus_per_instance": t.gpus_per_instance,
                         "max_price_per_gpu_hour": str(t.max_price_per_gpu_hour)} for t in config.targets]}


def snapshot(config, state) -> dict:
    return {"policy": config.policy, "done": state.done(), "count": state.count(),
            "hourly_total": str(state.hourly_total), "state_file": str(state.path),
            "targets": [{"name": t.name, "count": state.count(t.name), "quantity": t.quantity}
                        for t in config.targets],
            "successes": state.data["successes"], "pending": state.pending}


def resource_text(name: str) -> str:
    return resources.files("autodl_rent").joinpath("resources").joinpath(name).read_text(encoding="utf-8")


def agent_manifest() -> dict:
    return {
        "name": "autodl-rent", "version": __version__, "schema_version": PROTOCOL_VERSION,
        "entrypoint": ["python3", "-m", "autodl_rent"], "cwd": "repository root",
        "machine_flags": ["--json", "--non-interactive"], "exit_codes": EXIT_CODES,
        "commands": [
            {"command": "doctor", "network": "only_with_--online", "effect": "diagnostics"},
            {"command": "catalog regions|gpus|images", "network": True, "effect": "read_only"},
            {"command": "init --template", "network": False, "effect": "write_new_config"},
            {"command": "init --region REGION --gpu MODEL --max-price YUAN --budget YUAN",
             "network": False, "effect": "write_new_config",
             "options": ["--quantity", "--gpus-per-instance", "--policy", "--total", "--image", "--cuda",
                         "--wait-seconds", "--selection", "--state-file"],
             "repeatable": ["--region", "--gpu"]},
            {"command": "validate", "network": "only_with_--online", "effect": "read_only"},
            {"command": "run", "network": True, "effect": "preview", "creates_instances": False},
            {"command": "run --execute", "network": True, "effect": "paid_instance_creation",
             "options": ["--once", "--wait-seconds"]},
            {"command": "quick", "network": True, "effect": "write_quick_config_and_preview",
             "default_config": "config.west-under1.local.json", "execute_flag": "--execute"},
            {"command": "status", "network": False, "effect": "read_local_ledger"},
            {"command": "resolve --instance ID", "network": True, "effect": "confirm_pending_instance"},
            {"command": "resolve --not-created --checked-console", "network": False,
             "effect": "clear_pending_after_console_check"},
            {"command": "auth", "network": True, "effect": "validate_and_save_local_web_token",
             "requires_wechat_binding": False, "input": "hidden_terminal_or_environment"},
            {"command": "login", "network": True, "effect": "human_qr_login_and_save_auth",
             "requires_wechat_binding": True},
            {"command": "demo", "network": False, "effect": "temporary_offline_simulation"},
        ],
        "auth": {"environment": "AUTODL_WEB_TOKEN", "file_flag": "--auth-file",
                 "home_environment": "AUTODL_RENT_HOME", "never_print_token": True},
        "config_flag": "--config", "config_schema": "python3 -m autodl_rent agent schema",
        "skill": "python3 -m autodl_rent agent skill",
        "rules": [
            "Use user-authorized regions, models, quantities, image and hourly caps; preview before execution.",
            "Use --execute only when the user has authorized paid rental within those bounds.",
            "Keep the same config and state_file when resuming a job; never delete pending records to retry.",
            "Exit 4 requires reconciliation, never blind retry or an automated --checked-console assertion.",
            "Stopping the tool does not stop or release instances; an hourly cap is not a total-spend budget.",
        ],
    }


def config_schema() -> dict:
    return json.loads(resource_text("config.schema.json"))
