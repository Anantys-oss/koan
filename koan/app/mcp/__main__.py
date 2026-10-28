"""Launch Kōan's optional MCP server over stdio or Streamable HTTP."""

import ipaddress
import os
import sys
from pathlib import Path

from app.config import (
    get_api_token,
    get_mcp_enabled,
    get_mcp_host,
    get_mcp_port,
    get_mcp_tools_allow_destructive,
    get_mcp_transport,
)
from app.utils import load_dotenv


def _load_server():
    from app.mcp.server import create_server

    return create_server(
        allow_destructive=get_mcp_tools_allow_destructive(),
    )


def _probe_api() -> None:
    from app.apiclient import ApiClientError, RestApiClient
    from app.config import get_api_token
    from app.mcp.config import get_api_base_url
    from app.mcp.server import DEFAULT_SPEC

    try:
        RestApiClient(
            DEFAULT_SPEC,
            get_api_base_url(),
            get_api_token(),
            timeout=1,
        ).execute_operation("health_get")
    except ApiClientError as exc:
        print(f"Kōan MCP warning: {exc}", file=sys.stderr)


def _warn_non_loopback(host: str) -> None:
    try:
        is_loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        # A hostname, not a literal IP: we cannot prove it is loopback, so
        # treat it as non-loopback rather than silently skipping the warning.
        is_loopback = False
    if not is_loopback:
        print(
            f"WARNING: MCP HTTP bound to non-loopback address {host}.\n"
            "         Use a reverse proxy with TLS for external exposure.",
            file=sys.stderr,
        )


def _installed_mcp_version() -> str:
    """Version of the installed MCP SDK, or 'unknown'."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("mcp")
    except PackageNotFoundError:
        return "unknown"


def _build_server() -> "object | None":
    """Build the server, or print the SDK-missing hint and return None."""
    try:
        return _load_server()
    except ModuleNotFoundError as exc:
        # Only a missing top-level `mcp` means "not installed". A missing
        # submodule means an installed-but-incompatible SDK, and `make
        # mcp-setup` is a no-op for it — so name the module and re-raise
        # rather than print a repair that cannot work.
        if exc.name == "mcp":
            print(
                "Kōan MCP SDK missing; run `make mcp-setup`",
                file=sys.stderr,
            )
            return None
        if (exc.name or "").startswith("mcp."):
            print(
                f"Kōan MCP SDK incompatible: {exc.name} not found "
                f"(installed mcp version: {_installed_mcp_version()})",
                file=sys.stderr,
            )
        raise


def _run_http(koan_root: Path) -> int:
    if not koan_root.is_dir():
        print("ERROR: KOAN_ROOT must be set to a valid directory", file=sys.stderr)
        return 1
    if not get_api_token():
        print(
            "ERROR: Kōan MCP HTTP refuses to start without a bearer token",
            file=sys.stderr,
        )
        return 1

    from app.mcp.config import get_mcp_http_url
    from app.mcp.http_transport import bind_http_socket, serve_http
    from app.pid_manager import acquire_pidfile, release_pidfile
    from app.signals import ready_file

    host = get_mcp_host()
    port = get_mcp_port()
    _warn_non_loopback(host)
    # Bind before the pidfile exists. The launcher treats the pidfile as proof
    # of a successful start, so a bind that fails afterwards (port in use,
    # unavailable address) would be reported as a healthy boot. Binding is two
    # syscalls, so doing it first costs the verify timeout nothing.
    try:
        sock = bind_http_socket(host, port)
    except OSError as exc:
        print(
            f"ERROR: Kōan MCP HTTP cannot bind {host}:{port}: {exc}",
            file=sys.stderr,
        )
        return 1
    # Claim the pidfile before the SDK import, the spec parse and the API
    # probe: the process manager only waits a few seconds for it to appear,
    # and a slow startup must not be reported as a launch failure.
    lock = acquire_pidfile(koan_root, "mcp")
    ready = koan_root / ready_file("mcp")
    try:
        server = _build_server()
        if server is None:
            return 1
        _probe_api()
        print(f"Kōan MCP HTTP listening on {get_mcp_http_url()}", flush=True)
        # The pidfile says "this process exists"; the launcher needs "this
        # process serves". Everything that can still fail after the pidfile is
        # claimed — the SDK import, tool registration, an SDK signature change
        # inside serve_http — would otherwise exit while the launcher had
        # already reported success, so readiness is signalled separately, from
        # the last point at which nothing is left to fail.
        serve_http(
            server,
            host=host,
            port=port,
            audit_path=koan_root / "logs" / "mcp.log",
            sock=sock,
            on_ready=ready.touch,
        )
    finally:
        ready.unlink(missing_ok=True)
        sock.close()
        release_pidfile(lock, koan_root, "mcp")
    return 0


def main() -> int:
    # MCP clients spawn this process themselves with a minimal env, so nothing
    # has sourced `.env` the way `make` does — and `.env` is where the docs tell
    # operators to keep KOAN_API_TOKEN. Load it before any token is read.
    load_dotenv()
    if not get_mcp_enabled():
        print(
            "Kōan MCP server disabled; set mcp.enabled: true in instance/config.yaml",
            file=sys.stderr,
        )
        return 1

    koan_root = Path(os.environ.get("KOAN_ROOT", ""))
    if get_mcp_transport() == "http":
        return _run_http(koan_root)

    server = _build_server()
    if server is None:
        return 1
    _probe_api()
    server.run("stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
