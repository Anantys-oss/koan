"""Top-level orchestration for generated and built-in REST CLI commands."""

import getpass
import sys
from pathlib import Path

import requests

from app.cli import EXIT_LOCAL, EXIT_OK, CliError
from app.cli.commands import (
    build_operation_request,
    build_parser,
    build_raw_request,
    destructive_targets,
    expand_alias,
)
from app.cli.config import (
    CONFIG_PATH,
    load_settings,
    read_profile,
    resolve_base_url,
    resolve_profile,
    resolve_timeout,
    write_profile,
)
from app.cli.http import confirm_destructive, execute, verify_configuration
from app.cli.spec import load_operations, load_server_default, load_spec

DEFAULT_SPEC = Path(__file__).resolve().parents[2] / "openapi.yaml"


def main(
    argv=None,
    *,
    spec_path: Path | None = None,
    config_path: Path | None = None,
    session=requests,
) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        spec = load_spec(spec_path or DEFAULT_SPEC)
        operations = load_operations(spec)
        arguments = expand_alias(arguments, operations)
        args = build_parser(operations, spec).parse_args(arguments)
        server_default = load_server_default(spec)
        path = config_path or CONFIG_PATH

        if getattr(args, "_builtin", None) == "configure":
            # configure resolves the profile and URL on the same ladder as
            # every other command, so an exported KOAN_PROFILE does not write
            # production credentials into [default].
            profile = resolve_profile(cli_profile=args.profile)
            # Re-running configure edits a profile; it does not reset one. The
            # stored values are the defaults, so pressing Enter twice on a
            # remote profile keeps its URL and its token.
            stored = read_profile(path, profile)
            default_url = resolve_base_url(
                cli_base_url=args.base_url,
                section=stored,
                fallback=server_default,
            )
            entered_url = input(f"Base URL [{default_url}]: ").strip()
            base_url = (entered_url or default_url).rstrip("/")
            stored_token = str(stored.get("token", "")).strip()
            prompt = (
                "Bearer token [keep existing]: " if stored_token else "Bearer token: "
            )
            token = getpass.getpass(prompt).strip() or stored_token
            write_profile(path, profile, base_url, token)
            verification = verify_configuration(
                base_url,
                token,
                session,
                timeout=resolve_timeout(cli_timeout=args.timeout, section=stored),
            )
            # stdout carries valid JSON only; a failed probe is a diagnostic.
            stream = sys.stdout if verification.exit_code == EXIT_OK else sys.stderr
            print(verification.message, file=stream)
            return verification.exit_code

        settings = load_settings(
            path,
            server_default,
            cli_profile=args.profile,
            cli_base_url=args.base_url,
            cli_timeout=args.timeout,
        )
        if getattr(args, "_builtin", None) == "raw":
            plan = build_raw_request(
                args.raw_method,
                args.raw_path,
                settings.base_url,
                data=args.data,
                query=args.query,
                destructive=destructive_targets(operations),
            )
        else:
            plan = build_operation_request(
                args._operation,
                args,
                settings.base_url,
            )

        confirm_destructive(plan, yes=args.yes)
        return execute(
            plan,
            settings,
            session,
            compact=args.compact,
            pretty=args.pretty,
        )
    except CliError as exc:
        print(f"koan-cli: {exc}", file=sys.stderr)
        return EXIT_LOCAL


if __name__ == "__main__":
    raise SystemExit(main())
