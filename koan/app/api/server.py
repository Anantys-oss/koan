#!/usr/bin/env python3
"""Kōan REST API server entrypoint.

Usage:
    python3 app/api/server.py [--host HOST] [--port PORT]
    make api
    make start  # when api.enabled: true in config.yaml

Requires:
    - KOAN_ROOT environment variable
    - KOAN_API_TOKEN environment variable (or api.token in config.yaml)
    - waitress (pip install waitress)
"""

import argparse
import os
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Kōan REST API server")
    parser.add_argument("--host", default=None, help="Bind host (default: config or 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="Bind port (default: config or 8420)")
    args = parser.parse_args()

    koan_root = Path(os.environ.get("KOAN_ROOT", ""))
    if not koan_root or not koan_root.is_dir():
        print("ERROR: KOAN_ROOT must be set to a valid directory", file=sys.stderr)
        sys.exit(1)

    from app.config import get_api_host, get_api_port, get_api_token, get_api_threads

    host = args.host or get_api_host()
    port = args.port or get_api_port()
    threads = get_api_threads()
    token = get_api_token()

    # Fail closed: never serve without a token
    if not token:
        print(
            "ERROR: KOAN_API_TOKEN env var is not set and api.token is not configured.\n"
            "       The REST API refuses to start without a bearer token.\n"
            "       Set KOAN_API_TOKEN=<secret> in your environment or api.token in config.yaml.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Warn when not loopback
    import ipaddress
    try:
        addr = ipaddress.ip_address(host)
        if not addr.is_loopback:
            print(
                f"WARNING: API bound to non-loopback address {host}.\n"
                "         Use a reverse proxy with TLS for external exposure.",
                file=sys.stderr,
            )
    except ValueError:
        # hostname, not IP — skip check
        pass

    try:
        import waitress
    except ImportError:
        print(
            "ERROR: waitress is not installed. Run: pip install waitress",
            file=sys.stderr,
        )
        sys.exit(1)

    from app.api import create_app
    instance_dir = koan_root / "instance"
    app = create_app(koan_root=koan_root, instance_dir=instance_dir)

    # Register PID file so stop/status commands can track this process
    try:
        from app.pid_manager import acquire_pid
        acquire_pid(koan_root, "api", os.getpid())
    except Exception as e:
        print(f"WARNING: PID file error (non-fatal): {e}", file=sys.stderr)

    print(f"Kōan REST API listening on http://{host}:{port}/v1", flush=True)
    print(f"  threads={threads}  koan_root={koan_root}", flush=True)

    waitress.serve(app, host=host, port=port, threads=threads)


if __name__ == "__main__":
    main()
