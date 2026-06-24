#!/bin/bash
set -euo pipefail

# =========================================================================
# Kōan Docker Entrypoint
# =========================================================================
# Claude CLI is installed in the image via npm.
# Auth: ANTHROPIC_API_KEY (API billing) or CLAUDE_CODE_OAUTH_TOKEN (subscription).
# GitHub CLI auth (~/.config/gh) is mounted from the host.
#
# Commands:
#   start    — Run both agent loop and Telegram bridge (default)
#   agent    — Run agent loop only
#   bridge   — Run Telegram bridge only
#   auth     — Check Claude CLI auth status and show setup instructions
#   gh-auth  — Check GitHub CLI auth status
#   test     — Run the test suite
#   shell    — Drop into bash shell
# =========================================================================

KOAN_ROOT="${KOAN_ROOT:-/app}"
PYTHON="${KOAN_ROOT}/.venv/bin/python3"
INSTANCE="${KOAN_ROOT}/instance"

# Fall back to system Python if venv doesn't exist (Docker image uses system pip)
if [ ! -x "$PYTHON" ]; then
    PYTHON="python3"
fi

# --- ANSI Colors (disabled when stdout is not a TTY) ---
if [ -n "${KOAN_FORCE_COLOR:-}" ] || [ -t 1 ]; then
    BOLD='\033[1m' DIM='\033[2m'
    RED='\033[31m' GREEN='\033[32m' YELLOW='\033[33m' CYAN='\033[36m'
    RESET='\033[0m'
else
    BOLD='' DIM='' RED='' GREEN='' YELLOW='' CYAN='' RESET=''
fi

log()     { printf "${DIM}[koan-docker] $(date +%H:%M:%S)${RESET} %s\n" "$*"; }
warn()    { printf "${YELLOW}[koan-docker] $(date +%H:%M:%S) ⚠ %s${RESET}\n" "$*" >&2; }
error()   { printf "${RED}[koan-docker] $(date +%H:%M:%S) ✗ %s${RESET}\n" "$*" >&2; }
success() { printf "${GREEN}[koan-docker] $(date +%H:%M:%S) ✓ %s${RESET}\n" "$*"; }
section() { printf "\n${BOLD}${CYAN}--- %s ---${RESET}\n" "$*"; }

# -------------------------------------------------------------------------
# 1. Verify Mounted Binaries
# -------------------------------------------------------------------------
verify_binaries() {
    local missing=()
    local provider="${KOAN_CLI_PROVIDER:-claude}"

    # gh and git are installed in the image — just log versions
    success "gh $(gh --version 2>/dev/null | head -1 || echo '(unknown version)')"
    success "git $(git --version 2>/dev/null || echo '(unknown version)')"
    success "node $(node --version 2>/dev/null || echo '(unknown version)')"

    # Provider-specific CLI
    case "$provider" in
        claude)
            if ! command -v claude &>/dev/null; then
                missing+=("claude (Claude Code CLI) — npm install may have failed")
            else
                success "claude $(claude --version 2>/dev/null | head -1 || echo '(unknown version)')"
            fi
            ;;
        codex)
            if ! command -v codex &>/dev/null; then
                missing+=("codex (OpenAI Codex CLI) — npm install -g @openai/codex may have failed")
            else
                success "codex $(codex --version 2>/dev/null | head -1 || echo '(unknown version)')"
            fi
            ;;
        copilot)
            if ! command -v github-copilot-cli &>/dev/null && ! command -v copilot &>/dev/null; then
                missing+=("github-copilot-cli or copilot (GitHub Copilot CLI)")
            else
                success "copilot CLI"
            fi
            ;;
        local|ollama|ollama-launch)
            if ! command -v ollama &>/dev/null; then
                missing+=("ollama")
            else
                success "ollama $(ollama --version 2>/dev/null | head -1 || echo '(unknown version)')"
            fi
            ;;
    esac

    if [ ${#missing[@]} -gt 0 ]; then
        error "Missing binaries:"
        for bin in "${missing[@]}"; do
            printf "  ${RED}✗${RESET} %s\n" "$bin"
        done
        printf "\n"
        log "Run ./setup-docker.sh on the host to generate volume mounts."
        return 1
    fi

    success "All required binaries available (provider: $provider)"
    return 0
}

# -------------------------------------------------------------------------
# 2. Verify Auth State
# -------------------------------------------------------------------------

# Check Claude authentication (API key, OAuth token, or interactive login).
# Returns 0 if authenticated, 1 if not.
check_claude_auth() {
    # Option 1: API key (works with API billing accounts)
    if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
        success "Claude auth: API key"
        return 0
    fi

    # Option 2: OAuth token from setup-token (works with Claude subscriptions)
    if [ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
        success "Claude auth: OAuth token (setup-token)"
        return 0
    fi

    # Option 3: Interactive login (works with Claude subscriptions)
    if timeout 10 claude -p "ok" --max-turns 1 >/dev/null 2>&1; then
        success "Claude auth: interactive login"
        return 0
    fi

    # No method works
    error "Claude CLI is not authenticated"
    log "  Option 1: Run 'make docker-auth' on the HOST (subscription — generates OAuth token)"
    log "  Option 2: Set ANTHROPIC_API_KEY in .env (API billing)"
    return 1
}

# Check Codex authentication via a minimal probe.
# Returns 0 if authenticated, 1 if not.
check_codex_auth() {
    # Option 1: OpenAI API key
    if [ -n "${OPENAI_API_KEY:-}" ]; then
        success "Codex auth: OPENAI_API_KEY"
        return 0
    fi

    # Option 2: Device auth / interactive login — probe with a tiny prompt
    # --skip-git-repo-check: /app may not be a trusted git dir in Docker
    # --sandbox workspace-write: replaces deprecated --full-auto
    if timeout 15 codex exec --skip-git-repo-check --sandbox workspace-write "ok" >/dev/null 2>&1; then
        success "Codex auth: interactive login"
        return 0
    fi

    error "Codex CLI is not authenticated"
    log "  Option 1: Set OPENAI_API_KEY in .env"
    log "  Option 2: Run 'codex login --device-auth' inside the container"
    return 1
}

# Dispatch to the correct auth check based on configured provider.
check_provider_auth() {
    local provider="${KOAN_CLI_PROVIDER:-claude}"
    case "$provider" in
        claude)  check_claude_auth ;;
        codex)   check_codex_auth ;;
        # Other providers (copilot, local, ollama) don't have a standard
        # auth check — verify_auth() handles their warnings.
        *)       return 0 ;;
    esac
}

verify_auth() {
    local provider="${KOAN_CLI_PROVIDER:-claude}"
    local warnings=()

    # Check gh auth
    if [ -n "${GH_TOKEN:-}" ]; then
        success "GitHub auth: GH_TOKEN"
    elif [ ! -d "${HOME}/.config/gh" ]; then
        warnings+=("No GitHub auth — run 'make docker-gh-auth' on the host")
    fi

    # Check Copilot auth
    if [ "$provider" = "copilot" ] && [ ! -d "${HOME}/.copilot" ]; then
        warnings+=("No ~/.copilot/ mounted — Copilot CLI may not be authenticated")
    fi

    for w in "${warnings[@]}"; do
        warn "$w"
    done

    return 0
}

# -------------------------------------------------------------------------
# 3. SSH Agent Setup
# -------------------------------------------------------------------------
setup_ssh() {
    # Option 1: Mounted SSH agent socket from host
    if [ -S "/run/ssh-agent.sock" ]; then
        export SSH_AUTH_SOCK="/run/ssh-agent.sock"
        success "SSH auth: forwarded agent socket"
        return 0
    fi

    # Option 2: SSH keys mounted — start a local agent and load them
    if [ -d "$HOME/.ssh" ] && ls "$HOME/.ssh/id_"* >/dev/null 2>&1; then
        eval "$(ssh-agent -s)" > /dev/null
        ssh-add "$HOME/.ssh/id_"* 2>/dev/null || true
        success "SSH auth: local agent with mounted keys"
        return 0
    fi

    # Option 3: No SSH — git will use GH_TOKEN / HTTPS if configured
    log "No SSH agent or keys — git will use HTTPS/GH_TOKEN if available"
}

# -------------------------------------------------------------------------
# 4. Instance Directory
# -------------------------------------------------------------------------
setup_instance() {
    if [ ! -f "$INSTANCE/missions.md" ]; then
        log "Initializing instance/ from template"
        cp -r "$KOAN_ROOT/instance.example/"* "$INSTANCE/" 2>/dev/null || true
    fi

    # Ensure required subdirectories exist
    mkdir -p "$INSTANCE/journal" "$INSTANCE/memory" "$INSTANCE/memory/global" \
             "$INSTANCE/memory/projects"
}

# -------------------------------------------------------------------------
# 5. Workspace Setup
# -------------------------------------------------------------------------
setup_workspace() {
    local workspace="$KOAN_ROOT/workspace"

    if [ ! -d "$workspace" ]; then
        mkdir -p "$workspace"
    fi

    # Initialize projects.yaml from template (avoids EBUSY from atomic rename over a bind-mount point)
    if [ -f "$KOAN_ROOT/projects.docker.yaml" ] && [ ! -f "$KOAN_ROOT/projects.yaml" ]; then
        cp "$KOAN_ROOT/projects.docker.yaml" "$KOAN_ROOT/projects.yaml"
        log "projects.yaml initialized from projects.docker.yaml"
    fi

    # Count projects
    local count
    count=$(find "$workspace" -maxdepth 1 -mindepth 1 -type d -o -type l 2>/dev/null | wc -l | tr -d ' ')
    log "Workspace: $count project(s) mounted"

    # Check for projects.yaml
    if [ ! -f "$KOAN_ROOT/projects.yaml" ]; then
        if [ "$count" -gt 0 ]; then
            log "No projects.yaml — $count workspace project(s) will be auto-discovered"
        else
            warn "No projects.yaml and no workspace projects"
            log "  Run setup-docker.sh or mount projects in workspace/"
        fi
    fi
}


# -------------------------------------------------------------------------
# Railway (hosted single-container) bootstrap — idempotent, re-run each deploy
# -------------------------------------------------------------------------
railway_setup_git() {
    [ "${KOAN_DEPLOY:-}" = "railway" ] || return 0
    if [ -n "${GH_TOKEN:-}" ]; then
        gh auth setup-git 2>/dev/null \
            && success "git credential helper configured via gh" \
            || warn "gh auth setup-git failed (continuing)"
        git config --global url."https://github.com/".insteadOf "git@github.com:" || true
        git config --global url."https://github.com/".insteadOf "ssh://git@github.com/" || true
    else
        warn "GH_TOKEN unset — git push/clone over HTTPS may prompt"
    fi
}

railway_bootstrap() {
    [ "${KOAN_DEPLOY:-}" = "railway" ] || return 0
    section "Railway bootstrap"

    # 1. Normalize volume ownership to the *running* UID.
    local uid gid
    uid="$(id -u)"; gid="$(id -g)"
    mkdir -p "$INSTANCE" 2>/dev/null || true
    chown -R "${uid}:${gid}" "$INSTANCE" 2>/dev/null \
        && success "volume owned by ${uid}:${gid}" \
        || warn "could not chown $INSTANCE (continuing)"
    mkdir -p "$INSTANCE/workspace" 2>/dev/null || true

    # 2. Regenerate /app/.env as a mirror of the service env vars (#2076).
    if (cd "$KOAN_ROOT/koan" && $PYTHON -c 'import sys; from app.railway import required_env_present; sys.exit(0 if required_env_present() else 1)'); then
        if (cd "$KOAN_ROOT/koan" && $PYTHON -c "from pathlib import Path; from app.railway import write_env_from_environment as w; w(Path('$KOAN_ROOT/.env'))"); then
            success ".env mirrored from environment"
        else
            warn ".env mirror failed — container may lack credentials"
        fi
    else
        warn "Required env vars missing — .env mirror skipped"
    fi

    # 3. Drop any stale ephemeral onboarding checkpoint.
    rm -f "$KOAN_ROOT/.koan-onboarding.json" 2>/dev/null || true

    # 4. Token-only Git.
    railway_setup_git
}

railway_provision() {
    [ "${KOAN_DEPLOY:-}" = "railway" ] || return 0
    # Seed instance/projects.yaml from template if absent (resolved with
    # priority by load_projects_config — see Phase 2).
    if [ ! -e "$INSTANCE/projects.yaml" ] && [ -f "$KOAN_ROOT/projects.example.yaml" ]; then
        cp "$KOAN_ROOT/projects.example.yaml" "$INSTANCE/projects.yaml"
        log "instance/projects.yaml seeded from template"
    fi
}


# -------------------------------------------------------------------------
# Privilege model
# -------------------------------------------------------------------------
# A volume mounted at /app/instance (Railway and similar PaaS) mounts as
# root:root, so the unprivileged koan user cannot write to it. The image now
# boots as root: we normalize the volume ownership here, then drop privileges
# and re-exec the whole entrypoint as the koan user so every long-running
# process (agent, bridge, supervisord) stays non-root.
KOAN_USER="${KOAN_USER:-koan}"

maybe_drop_privileges() {
    [ "$(id -u)" = "0" ] || return 0   # already unprivileged — nothing to do
    local uid gid
    uid="$(id -u "$KOAN_USER" 2>/dev/null || echo "${KOAN_UID:-1000}")"
    gid="$(id -g "$KOAN_USER" 2>/dev/null || echo "${KOAN_GID:-1000}")"
    mkdir -p "$INSTANCE" 2>/dev/null || true
    if chown -R "${uid}:${gid}" "$INSTANCE" 2>/dev/null; then
        success "instance volume owned by ${KOAN_USER} (${uid}:${gid})"
    else
        warn "could not chown $INSTANCE (continuing)"
    fi
    export HOME="/home/${KOAN_USER}"
    exec gosu "$KOAN_USER" "$0" "$@"
}

# Bot GitHub identity: a Kōan-specific token always wins over a
# platform-injected GH_TOKEN (e.g. Railway's GitHub App ghu_* token for the
# logged-in operator). Exported here so the whole process tree inherits it.
if [ -n "${KOAN_GH_TOKEN:-}" ]; then
    export GH_TOKEN="${KOAN_GH_TOKEN}"
fi

# =========================================================================
# Main
# =========================================================================
COMMAND="${1:-start}"

# Normalize the instance volume as root, then re-exec as koan (no-op if
# already unprivileged). Everything below this line runs as the koan user.
maybe_drop_privileges "$@"

case "$COMMAND" in
    start)
        printf "${BOLD}${CYAN}Kōan Docker — initializing${RESET}\n"
        railway_bootstrap                 # no-op unless KOAN_DEPLOY=railway
        verify_binaries || exit 1
        check_provider_auth || exit 1
        verify_auth
        setup_ssh
        railway_provision                 # no-op unless KOAN_DEPLOY=railway
        setup_instance
        setup_workspace

        # Touch heartbeat so HEALTHCHECK doesn't fail during boot
        date +%s > "$KOAN_ROOT/.koan-heartbeat"

        log "Handing off to supervisord"
        exec supervisord -c /etc/supervisord.conf
        ;;

    agent)
        log "Kōan Docker — agent only"
        railway_bootstrap                 # no-op unless KOAN_DEPLOY=railway
        verify_binaries || exit 1
        check_provider_auth || exit 1
        verify_auth
        setup_ssh
        railway_provision                 # no-op unless KOAN_DEPLOY=railway
        setup_instance
        setup_workspace

        cd "$KOAN_ROOT/koan" && exec $PYTHON app/run.py
        ;;

    bridge)
        log "Kōan Docker — bridge only"
        setup_instance

        cd "$KOAN_ROOT/koan" && exec $PYTHON app/awake.py
        ;;

    test)
        log "Running test suite"
        setup_instance
        cd "$KOAN_ROOT/koan" && \
            exec $PYTHON -m pytest tests/ -v
        ;;

    auth)
        section "Claude CLI Authentication"
        if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
            success "Already authenticated via API key — no login needed"
            exit 0
        fi
        if [ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
            success "Already authenticated via OAuth token — no login needed"
            exit 0
        fi
        log "Checking existing auth state..."
        if claude auth status >/dev/null 2>&1; then
            success "Already authenticated — no action needed"
            exit 0
        fi
        error "Not authenticated."
        log ""
        log "Browser-based login does not work reliably inside Docker."
        log "Instead, generate an OAuth token on your HOST machine:"
        log ""
        log "  make docker-auth"
        log ""
        log "This runs 'claude setup-token' interactively, captures the"
        log "token from its output, and saves it to .env as CLAUDE_CODE_OAUTH_TOKEN."
        log ""
        log "Alternatively, set ANTHROPIC_API_KEY in .env (API billing accounts)."
        exit 1
        ;;

    gh-auth)
        section "GitHub CLI Authentication"
        if [ -n "${GH_TOKEN:-}" ]; then
            success "Authenticated via GH_TOKEN environment variable"
            gh auth status 2>&1 || true
        elif gh auth status >/dev/null 2>&1; then
            success "Authenticated via mounted ~/.config/gh"
            gh auth status 2>&1 || true
        else
            error "Not authenticated."
            log ""
            log "GitHub CLI tokens stored in macOS Keychain are not accessible"
            log "inside Docker. Instead, inject the token as an env var:"
            log ""
            log "  make docker-gh-auth"
            log ""
            log "This extracts your host's gh token and saves it to .env as GH_TOKEN."
            log "The gh CLI natively uses GH_TOKEN when set."
            exit 1
        fi
        ;;

    shell)
        exec /bin/bash
        ;;

    *)
        echo "Usage: docker run koan [start|agent|bridge|auth|gh-auth|test|shell]"
        exit 1
        ;;
esac
