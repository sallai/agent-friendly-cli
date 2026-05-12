---
name: agent-friendly-cli
description: Design, review, or refactor command-line tools so they are reliable for LLMs and AI agents. Use when Codex is building a new CLI, adding machine-readable output, fixing stdout/stderr behavior, documenting exit codes, adding schemas, disabling prompts, or assessing whether a CLI is agent-friendly.
---

# Agent-Friendly CLI

## Core Rule

Optimize CLIs for automated callers first. An agent must be able to run a command, parse stdout deterministically, inspect stderr for diagnostics, and choose the next action from the exit code.

When reviewing an existing CLI, check behavior by running representative commands with stdout and stderr redirected separately.

## Output Contract

Keep streams strictly separated:

- `stdout` is for the requested payload only.
- `stderr` is for logs, progress, warnings, debug traces, setup guidance, and human status text.
- In JSON mode, stdout must contain exactly one valid JSON object and no leading, trailing, or interleaved text.
- Do not print `Loading...`, `Wrote ...`, progress bars, or setup walkthroughs to stdout unless that text is the explicit payload of a command like `setup`.

Every CLI intended for agents should provide a global or command-wide machine mode:

```sh
tool --output json ...
```

In JSON mode, use a stable envelope:

```json
{
  "status": "success",
  "metadata": {
    "execution_time_ms": 142,
    "records_processed": 500
  },
  "data": {}
}
```

For failures in JSON mode, emit the error object on stdout when the command intentionally selected machine output, and put human diagnostics on stderr:

```json
{
  "status": "error",
  "metadata": {
    "execution_time_ms": 21
  },
  "error": {
    "code": "missing_credentials",
    "message": "Credentials are not configured.",
    "retryable": false
  }
}
```

Keep human output available when useful, but do not require agents to parse tables, prose, grep output, or status lines.

## Exit Codes

Document exit codes in `--help` and schema output. Use this baseline unless the project already has a stronger convention:

- `0`: success.
- `1`: generic runtime, API, network, or fatal error.
- `2`: CLI syntax or argument error. Preserve argparse/click behavior where possible.
- `3`: missing or invalid credentials, auth setup required, or expired token.
- `4`: permission denied, access denied, or insufficient scopes.
- `5`: validation failure, not found, conflict, or domain-specific failure.

Make exit-code behavior testable. Do not collapse every failure into `1` if an agent can reasonably recover differently from the cases.

## Machine Discovery

Provide machine-readable discovery in addition to human `--help`:

```sh
tool schema
tool --schema
```

The schema output should be JSON and include:

- commands and subcommands.
- argument names, types, required flags, defaults, and allowed values.
- output envelope shape for each command.
- exit-code meanings.
- credential/setup requirements.
- examples that are safe to run or clearly marked as mutating.

Human `--help` should mention `--output json`, schema discovery, non-interactive flags, and exit codes.

## Non-Interactive Behavior

Agents must never hang on prompts:

- Do not call `input()` or prompt in non-TTY contexts.
- Detect `sys.stdin.isatty()` or equivalent before any interactive prompt.
- For destructive operations, prefer explicit `--yes` or `--force`.
- In non-TTY mode without required confirmation, fail fast with a clear error and exit code instead of waiting.
- Browser login and OAuth flows must be explicit commands such as `auth login`, never a surprise side effect of read commands.

Setup commands may print walkthroughs to stdout because the walkthrough is their payload. Failed data commands should print setup guidance to stderr or return a structured JSON error in JSON mode.

## Testing Checklist

For each new or changed CLI command, add or run tests that verify:

- JSON mode stdout parses as exactly one JSON object.
- stderr contains diagnostics without corrupting stdout.
- human status text is not printed to stdout during JSON mode.
- syntax errors exit `2`.
- missing credentials exit `3` when distinguishable.
- permission/auth failures map to documented codes.
- non-TTY execution never blocks on prompts.
- file-writing commands either return a JSON manifest on stdout or write status messages to stderr.

Useful shell pattern:

```sh
set +e
tool --output json command >stdout.json 2>stderr.log
code=$?
python -m json.tool stdout.json >/dev/null
printf 'exit=%s stderr_bytes=%s\n' "$code" "$(wc -c <stderr.log)"
```

## Implementation Notes

Prefer small shared helpers inside the CLI:

- `emit_json(data, metadata=None, status="success")`
- `emit_error(code, message, exit_code, metadata=None)`
- `log(message)` that always writes to stderr.
- one central exception-to-exit-code mapper.
- one central schema object used by both `schema` and documentation tests.

For Python CLIs in repositories that require `uv`, keep standalone scripts single-file with PEP 723 metadata. Avoid adding packages unless the user explicitly wants a package structure.
