#!/bin/sh
# Supervisor launcher for the web dashboard.
#
# The dashboard is only started on hosted (Railway) deploys, where it is the
# primary UI. On every other deploy it stays disabled — we keep the supervisor
# program alive with `sleep infinity` so supervisord does not treat the missing
# dashboard as a crash-loop.
#
# On Railway the dashboard binds to 0.0.0.0:5000 so the platform router can
# reach it. Point the Railway public domain at port 5000 (Settings →
# Networking). It MUST be gated by KOAN_DASHBOARD_PWD (a shared passphrase)
# before it is exposed publicly — refuse to start otherwise.
#
# NOTE: we deliberately ignore Railway's injected $PORT and pin 5000 (override
# with KOAN_DASHBOARD_PORT). $PORT is unpredictable across deploys, which makes
# the public domain's target port a moving target.

if [ "${KOAN_DEPLOY:-}" != "railway" ]; then
    echo "[dashboard] KOAN_DEPLOY != railway — dashboard disabled"
    exec sleep infinity
fi

if [ -z "${KOAN_DASHBOARD_PWD:-}" ]; then
    echo "[dashboard] refusing to start: KOAN_DASHBOARD_PWD is not set." >&2
    echo "[dashboard] Set a passphrase to expose the dashboard on Railway." >&2
    exec sleep infinity
fi

DASH_PORT="${KOAN_DASHBOARD_PORT:-5000}"
echo "[dashboard] starting on 0.0.0.0:${DASH_PORT} (passphrase-gated)"
exec /app/koan/docker/supervised-run.sh python3 app/dashboard.py --host 0.0.0.0 --port "${DASH_PORT}"
