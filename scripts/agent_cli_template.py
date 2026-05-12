#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""Agent-friendly single-file CLI template.

Copy this file to scripts/<service>-cli, then edit the provider constants in
the "Provider configuration" section. Keep it single-file unless the user asks
for a package.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# Provider configuration: replace these values for the target service.
TOOL_NAME = "example-cli"
APP_NAME = "example"
API_BASE_URL = "https://api.example.com/v1"
AUTH_TEST_PATH = "/me"
TOKEN_ENV_VARS = ("EXAMPLE_TOKEN", "EXAMPLE_API_KEY")
TOKEN_LABEL = "API token"
DEFAULT_HEADERS = {
    "Accept": "application/json",
}
AUTH_HEADER_PREFIX = "Bearer"

APP_DIR = Path.home() / f".{APP_NAME}"
ENV_FILES = (
    APP_DIR / "env",
    APP_DIR / f"{APP_NAME}.env",
    APP_DIR / ".env",
)

EXIT_RUNTIME = 1
EXIT_USAGE = 2
EXIT_AUTH = 3
EXIT_PERMISSION = 4
EXIT_DOMAIN = 5


class CliError(RuntimeError):
    exit_code = EXIT_RUNTIME
    code = "runtime_error"
    retryable = False

    def __init__(self, message: str, *, code: str | None = None, exit_code: int | None = None, retryable: bool | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        if exit_code is not None:
            self.exit_code = exit_code
        if retryable is not None:
            self.retryable = retryable


class AuthError(CliError):
    exit_code = EXIT_AUTH
    code = "missing_credentials"


class PermissionCliError(CliError):
    exit_code = EXIT_PERMISSION
    code = "permission_denied"


class DomainCliError(CliError):
    exit_code = EXIT_DOMAIN
    code = "domain_error"


@dataclass(frozen=True)
class AppContext:
    output: str
    started_at: float
    env_file: list[str] | None

    @property
    def json_output(self) -> bool:
        return self.output == "json"


@dataclass(frozen=True)
class ApiClient:
    token: str

    def request(self, method: str, target: str, body: Any | None = None, query: list[str] | None = None) -> Any:
        url = build_url(target, query or [])
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {
            **DEFAULT_HEADERS,
            "Authorization": f"{AUTH_HEADER_PREFIX} {self.token}",
        }
        if data is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
        request = urllib.request.Request(url, data=data, method=method, headers=headers)

        try:
            with urllib.request.urlopen(request) as response:
                payload = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            payload = error.read().decode("utf-8", errors="replace")
            raise api_error_from_http(error.code, error.reason, payload) from error
        except urllib.error.URLError as error:
            raise CliError(f"Network error: {error.reason}", code="network_error", retryable=True) from error

        if not payload:
            return {}
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return {"raw": payload}


def main() -> int:
    started_at = time.perf_counter()
    parser = build_parser()
    args = parser.parse_args()
    ctx = AppContext(output=args.output, started_at=started_at, env_file=args.env_file)

    try:
        if getattr(args, "schema", False):
            return emit_success(ctx, schema(), records_processed=1)
        if not hasattr(args, "func"):
            parser.print_usage(sys.stderr)
            return EXIT_USAGE

        if not getattr(args, "requires_auth", True):
            return args.func(None, args, ctx)

        token = load_token(args.env_file)
        client = ApiClient(token)
        return args.func(client, args, ctx)
    except CliError as error:
        if isinstance(error, AuthError):
            log(setup_text())
        return emit_failure(ctx, error)
    except (ValueError, OSError, json.JSONDecodeError) as error:
        return emit_failure(ctx, CliError(str(error), code="invalid_input", exit_code=EXIT_DOMAIN))


def build_parser() -> argparse.ArgumentParser:
    description = (
        "Agent-friendly single-file API CLI template. "
        "Exit codes: 0 success, 1 runtime/API, 2 usage, 3 auth, 4 permission, 5 validation/domain."
    )
    parser = argparse.ArgumentParser(prog=TOOL_NAME, description=description)
    parser.add_argument("--env-file", action="append", help=f"Credential env file. Defaults to {APP_DIR}/env and compatible fallbacks.")
    parser.add_argument("--output", choices=["human", "json"], default="human", help="Output format. JSON mode emits one envelope object.")
    parser.add_argument("--schema", action="store_true", help="Print machine-readable command schema as JSON.")

    subparsers = parser.add_subparsers()
    add_setup_parser(subparsers)
    add_me_parser(subparsers)
    add_config_parser(subparsers)
    add_schema_parser(subparsers)
    add_api_parser(subparsers)
    add_search_parser(subparsers)
    return parser


def add_setup_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("setup", help="Print credential setup instructions.")
    focused = parser.add_mutually_exclusive_group()
    focused.add_argument("--env-only", action="store_true", help="Print only local credential-file instructions.")
    focused.add_argument("--auth-note", action="store_true", help="Print only provider auth notes.")
    parser.set_defaults(func=cmd_setup, requires_auth=False)


def add_me_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("me", help="Validate active credentials and show current identity.")
    parser.set_defaults(func=cmd_me)


def add_config_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("config", help="Show resolved configuration paths without secrets.")
    parser.set_defaults(func=cmd_config, requires_auth=False)


def add_schema_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("schema", help="Print machine-readable command schema.")
    parser.set_defaults(func=cmd_schema, requires_auth=False)


def add_api_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("api", help="Call any authenticated provider API endpoint.")
    parser.add_argument("method", choices=["GET", "POST", "PATCH", "PUT", "DELETE"])
    parser.add_argument("target", help="API path such as /resource, or a full https URL.")
    parser.add_argument("--data", help="JSON request body, @file.json, or - for stdin.")
    parser.add_argument("--query", action="append", default=[], help="Append query params as key=value.")
    parser.add_argument("--paginate", action="store_true", help="Collect paginated results when implemented.")
    parser.add_argument("--limit", type=int, help="Maximum total paginated results.")
    parser.set_defaults(func=cmd_api)


def add_search_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("search", help="Provider-wide search placeholder.")
    parser.add_argument("query")
    parser.add_argument("--limit", type=int, default=25)
    parser.set_defaults(func=cmd_search)


def cmd_setup(_client: ApiClient | None, args: argparse.Namespace, ctx: AppContext) -> int:
    if args.env_only:
        text = env_setup_text()
    elif args.auth_note:
        text = auth_note_text()
    else:
        text = setup_text()

    if ctx.json_output:
        return emit_success(ctx, {"text": text}, records_processed=1)
    print(text.rstrip())
    return 0


def cmd_me(client: ApiClient | None, _args: argparse.Namespace, ctx: AppContext) -> int:
    assert client is not None
    data = client.request("GET", AUTH_TEST_PATH)
    return emit_success(ctx, data, records_processed=1)


def cmd_config(_client: ApiClient | None, _args: argparse.Namespace, ctx: AppContext) -> int:
    data = {
        "app_name": APP_NAME,
        "app_dir": str(APP_DIR),
        "env_files": [{"path": str(path), "exists": path.exists()} for path in ENV_FILES],
        "token_env_vars": list(TOKEN_ENV_VARS),
        "token_configured": token_configured(),
        "api_base_url": API_BASE_URL,
    }
    return emit_success(ctx, data, records_processed=1)


def cmd_schema(_client: ApiClient | None, _args: argparse.Namespace, ctx: AppContext) -> int:
    return emit_success(ctx, schema(), records_processed=1)


def cmd_api(client: ApiClient | None, args: argparse.Namespace, ctx: AppContext) -> int:
    assert client is not None
    if args.paginate:
        raise DomainCliError("Pagination is provider-specific; implement collect_paginated before enabling --paginate.", code="pagination_not_implemented")
    body = read_json_arg(args.data) if args.data else None
    data = client.request(args.method, args.target, body, args.query)
    return emit_success(ctx, data, records_processed=count_records(data))


def cmd_search(client: ApiClient | None, args: argparse.Namespace, ctx: AppContext) -> int:
    assert client is not None
    data = client.request("GET", "/search", query=[f"q={args.query}", f"limit={args.limit}"])
    return emit_success(ctx, data, records_processed=count_records(data))


def setup_text() -> str:
    return f"""
{TOOL_NAME} access is not set up.

This template stores credentials outside the repository under:

```sh
{APP_DIR}/env
```

{auth_note_text().rstrip()}

{env_setup_text().rstrip()}

Validate access:

```sh
uv run scripts/{TOOL_NAME} me
uv run scripts/{TOOL_NAME} --output json schema
```
"""


def env_setup_text() -> str:
    primary = TOKEN_ENV_VARS[0]
    return f"""
Create the local credential directory:

```sh
mkdir -p {APP_DIR}
chmod 700 {APP_DIR}
```

Save credentials in:

```sh
$EDITOR {APP_DIR}/env
chmod 600 {APP_DIR}/env
```

Example {APP_DIR}/env:

```sh
{primary}=replace-with-token
```
"""


def auth_note_text() -> str:
    return f"""
Create or obtain a provider {TOKEN_LABEL}. Never commit credentials. Never ask
the user to paste real tokens into chat. If OAuth is required, implement an
explicit `auth login` command rather than launching browser auth from read
commands.
"""


def schema() -> dict[str, Any]:
    envelope = {
        "success": {"status": "success", "metadata": "object", "data": "any"},
        "error": {"status": "error", "metadata": "object", "error": {"code": "string", "message": "string", "retryable": "boolean"}},
    }
    return {
        "tool": TOOL_NAME,
        "output": {
            "formats": ["json", "human"],
            "default": "human",
            "json_envelope": envelope,
            "stdout": "payload only",
            "stderr": "diagnostics, progress, setup guidance for failed data commands",
        },
        "credentials": {
            "env_files": [str(path) for path in ENV_FILES],
            "token_env_vars": list(TOKEN_ENV_VARS),
            "precedence": "env files, then process environment",
        },
        "exit_codes": {
            "0": "success",
            "1": "runtime/API/network/fatal error",
            "2": "CLI syntax or argument error",
            "3": "missing or invalid credentials",
            "4": "permission denied or insufficient scopes",
            "5": "validation, not found, conflict, or domain-specific failure",
        },
        "commands": {
            "setup": {"auth": False, "args": ["--env-only", "--auth-note"]},
            "me": {"auth": True, "purpose": "validate credentials and show current identity"},
            "config": {"auth": False, "purpose": "show paths and booleans without secrets"},
            "schema": {"auth": False, "purpose": "machine-readable help"},
            "api": {"auth": True, "args": ["method", "target", "--data", "--query", "--paginate", "--limit"]},
            "search": {"auth": True, "args": ["query", "--limit"], "placeholder": True},
        },
    }


def emit_success(ctx: AppContext, data: Any, *, records_processed: int | None = None) -> int:
    metadata = build_metadata(ctx)
    if records_processed is not None:
        metadata["records_processed"] = records_processed
    if ctx.json_output:
        print(json.dumps({"status": "success", "metadata": metadata, "data": data}, indent=2, ensure_ascii=False))
    else:
        print_human(data)
    return 0


def emit_failure(ctx: AppContext, error: CliError) -> int:
    metadata = build_metadata(ctx)
    payload = {
        "status": "error",
        "metadata": metadata,
        "error": {
            "code": error.code,
            "message": str(error),
            "retryable": error.retryable,
        },
    }
    if ctx.json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    log(f"{error.code}: {error}")
    return error.exit_code


def build_metadata(ctx: AppContext) -> dict[str, Any]:
    return {"execution_time_ms": int((time.perf_counter() - ctx.started_at) * 1000)}


def print_human(data: Any) -> None:
    if isinstance(data, str):
        print(data)
    else:
        print(json.dumps(data, indent=2, ensure_ascii=False))


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def load_token(env_file_args: list[str] | None) -> str:
    env_values = load_env_files([Path(path).expanduser() for path in env_file_args] if env_file_args else list(ENV_FILES))
    for name in TOKEN_ENV_VARS:
        value = env_values.get(name)
        if value:
            return value
    for name in TOKEN_ENV_VARS:
        value = os.environ.get(name)
        if value:
            return value
    raise AuthError(f"{TOKEN_LABEL} is missing. Run: uv run scripts/{TOOL_NAME} setup")


def token_configured() -> bool:
    try:
        load_token(None)
    except AuthError:
        return False
    return True


def load_env_files(paths: list[Path]) -> dict[str, str]:
    values: dict[str, str] = {}
    for path in paths:
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def build_url(target: str, pairs: list[str]) -> str:
    if target.startswith("http://") or target.startswith("https://"):
        base = target
    else:
        base = f"{API_BASE_URL.rstrip('/')}/{target.lstrip('/')}"
    if not pairs:
        return base
    query = urllib.parse.urlencode(parse_key_value_pairs(pairs), doseq=True)
    separator = "&" if "?" in base else "?"
    return f"{base}{separator}{query}"


def parse_key_value_pairs(pairs: list[str]) -> list[tuple[str, str]]:
    parsed: list[tuple[str, str]] = []
    for pair in pairs:
        if "=" not in pair:
            raise DomainCliError(f"Invalid key=value pair: {pair!r}", code="invalid_parameter")
        key, value = pair.split("=", 1)
        parsed.append((key, value))
    return parsed


def read_json_arg(value: str) -> Any:
    if value == "-":
        return json.loads(sys.stdin.read())
    if value.startswith("@"):
        return json.loads(Path(value[1:]).expanduser().read_text(encoding="utf-8"))
    return json.loads(value)


def api_error_from_http(status: int, reason: str, payload: str) -> CliError:
    message = http_error_message(status, reason, payload)
    if status in {401, 403}:
        return PermissionCliError(message, code="permission_denied")
    if status in {404, 409, 422}:
        return DomainCliError(message, code="api_domain_error")
    return CliError(message, code="api_error", retryable=status in {429, 500, 502, 503, 504})


def http_error_message(status: int, reason: str, payload: str) -> str:
    if not payload:
        return f"HTTP {status} {reason}"
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return f"HTTP {status} {reason}: {payload}"
    if isinstance(parsed, dict):
        error = parsed.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error)
        if isinstance(error, str):
            return error
        message = parsed.get("message")
        if message:
            return str(message)
    return f"HTTP {status} {reason}: {payload}"


def count_records(data: Any) -> int:
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        for key in ("results", "items", "files", "messages", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return len(value)
        return 1
    return 1 if data is not None else 0


if __name__ == "__main__":
    raise SystemExit(main())
