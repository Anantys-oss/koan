#!/usr/bin/env python3
"""Runnable entry point for the Kōan dashboard.

Usage:
    python3 app/dashboard/__main__.py [--port 5001] [--host 127.0.0.1]
    make dashboard
"""
import argparse
import sys

from app.dashboard import app, state


def main() -> None:
    parser = argparse.ArgumentParser(description="Kōan Dashboard")
    parser.add_argument("--port", type=int, default=5001)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--debug", action="store_true",
                        help="Enable Flask debug mode (NOT recommended)")
    args = parser.parse_args()

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(
            f"[dashboard] WARNING: Binding to {args.host} exposes the dashboard "
            f"to the network. No authentication or rate limiting is configured.",
            file=sys.stderr,
        )

    print(f"[dashboard] Starting on http://{args.host}:{args.port}")
    print(f"[dashboard] Instance: {state.INSTANCE_DIR}")
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
