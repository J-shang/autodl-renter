# Working with autodl-renter

This is a small, source-run Python 3.9+ tool. From this repository root, use `python3 -m autodl_rent`. Do not require pip, virtual environments, a global command, an MCP service, or a Skill installation for normal use.

## Operate the tool for a user

Read [the operating Skill](autodl_rent/resources/autodl-renter/SKILL.md), then use `python3 -m autodl_rent agent --json` to discover the current command contract. [docs/agents.md](docs/agents.md) has examples. These files guide use; they do not authorize paid instance creation.

Prefer `--json --non-interactive`. Preview with `run` before executing an authorized rental with `run --execute`. Preserve the same config and state file for a task. Exit 4 means creation may have succeeded: reconcile instead of retrying or deleting state. Login tokens remain local; never request them in chat. Interactive `login` offers QR or Token; programmatic calls must specify `--method`. Accounts without linked WeChat should use `login --method token` in the user's local terminal to import a web login token. For linked accounts, `login --method qr` reports an image path that can be shown to the user while the process waits for their scan.

## Develop the tool

The runtime uses only the Python standard library. Playwright is an optional browser login helper; QR and Token login do not need it. Keep the module entry point and existing config/state format compatible. Keep JSON stdout separate from progress on stderr, and preserve documented exit codes.

Run `python3 -m unittest discover -s tests -v` and `git diff --check` for changes. Add behavior tests for meaningful CLI/runner changes, using temporary paths and API doubles. Never run paid rental as a development test without the user's explicit authorization.

Creation intent must be durable before the paid API request. Unknown responses, timeouts, and interrupted creates retain pending records. Keep the state lock, config fingerprint, quota/budget checks, and token redaction intact. Do not move, clear, or overwrite `.local/` or live user task files during development. Do not commit authentication, logs, QR challenges, instance records, browser captures, or personal configurations.

Keep the README's first path download-and-run. When the CLI contract changes, update help, the agent manifest, the configuration schema where relevant, and the operating Skill. See [CONTRIBUTING.md](CONTRIBUTING.md) for module responsibilities and validation.
