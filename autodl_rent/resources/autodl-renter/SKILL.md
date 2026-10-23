---
name: autodl-renter
description: Operate the autodl-renter Python CLI to find AutoDL GPU or CPU resources, configure bounded rental jobs, preview inventory, rent within user-authorized limits, and reconcile pending creation results. Use for AutoDL rental tasks in a downloaded or cloned autodl-renter checkout.
---

# AutoDL renter

Run commands from the repository root using `python3 -m autodl_rent` (or the user's Python 3.9+ command). No installation or third-party package is needed. Read `python3 -m autodl_rent agent --json` to discover the current interface; use `agent schema` for configuration fields.

Use `--json --non-interactive` for programmatic calls. Stdout contains one JSON result with `schema_version`, `ok`, `exit_code`, `data`, and `error`; progress is on stderr. Parse JSON instead of terminal wording. `--help`, `--version`, and plain `agent skill` are text exports.

## Configure and preview

Inspect `doctor --json`, then fetch the directories needed for the user's request with `catalog regions`, `catalog gpus`, and `catalog images`. Values must match complete catalog names or region signs. Prices in example files are illustrative, not current offers.

Authentication remains local in `.local/auth.json`, an explicit `--auth-file`, or `AUTODL_WEB_TOKEN`. Do not print or ask the user to send tokens in chat. Accounts without a linked WeChat can authenticate using a browser's existing AutoDL web session: have the user run `python3 -m autodl_rent auth` in their own terminal and paste the web login token into the hidden input. This needs no WeChat binding or extra dependency. `auth --json` is noninteractive and requires an existing local `AUTODL_WEB_TOKEN`.

For accounts with a linked WeChat, run `login --no-open --json` with a long enough process timeout; its stderr emits a `login_qr` event with an image path. Show that local image to the user and leave the process running while they scan. If the QR flow reports an unlinked account, use local Token authentication instead of requiring the user to bind WeChat. Authentication does not authorize paid rental.

Generate a new task without prompts, substituting the user's actual constraints and a real catalog image:

```bash
python3 -m autodl_rent init --config config.job.local.json \
  --region '西北B区' --gpu 'RTX 4090' --max-price 3 --budget 3 \
  --quantity 1 --policy any --image IMAGE_UUID --cuda 12.8 --json
python3 -m autodl_rent validate --config config.job.local.json --json
python3 -m autodl_rent run --config config.job.local.json --json
```

Repeat `--region` or `--gpu` as needed. Each `--gpu` creates a separate target with the given per-model quantity and price cap. For different per-target caps, use `init --template`, edit the JSON, and validate. `any` stops after the first success; `all` meets every target; `total --total N` meets a combined count subject to each target's quota. Quantity counts instances; `--gpus-per-instance` counts resource units per instance.

## Execute and resume

Present the configured bounds and preview. Use `run --execute --config ... --json` once the user has authorized paid rental within those bounds. Existing explicit authorization remains valid; do not ask again for routine retries within the same task. Do not infer spending authorization from a request to inspect stock or configure a job.

Keep execution attached to a managed process and retain its session ID if the tool returns one. A tool-call timeout is not evidence that the rental process stopped. Use the same configuration and state file for resume; inspect `status --config ... --json` first. `--wait-seconds` limits one invocation and does not reset progress. Completed tasks do not need another create request.

`quick` is a fixed preset: 西北B区, any supported GPU/CPU, one resource unit, hourly price strictly below ¥1, stop after one success. It previews by default and only rents with `--execute`. Its default config is `config.west-under1.local.json`; supply that config explicitly when running `status` or `resolve`. Use it only when those conditions match the user's request.

## Handle results

| Exit | Meaning | Next action |
| --- | --- | --- |
| 0 | Preview ended or goal completed | Inspect `data.mode`, `data.done`, and instances |
| 2 | Invalid input, authentication, or definite API failure | Inspect `error`, correct the cause |
| 3 | Wait/single scan ended before completion | Inspect status; resume the same task if authorized |
| 4 | Creation may have succeeded | Stop creation and reconcile; never blindly retry |
| 130 | Interrupted | Inspect status before resuming |

Pending records prevent duplicate paid requests. If the pending record contains an instance ID, `resolve` can check it read-only. Otherwise reconcile its `grab-<attempt_id>` remark in the AutoDL console; use `resolve --instance ID` only for the corresponding instance. `resolve --not-created --checked-console` asserts that the console was actually checked and no instance was created. Never invent this evidence, clear pending automatically, or delete/change state paths to bypass it.

Report instance IDs, hourly prices, completion, and any pending request. Stopping the CLI does not shut down or release AutoDL instances. Hourly caps are not total-spend budgets; users manage instance lifetime in AutoDL.
