#!/usr/bin/env python3
"""
Kōan — CLI Onboarding Wizard

Interactive terminal-based setup that walks a first-time user through
every configuration step. Resumable via checkpoint file.

Usage:
    python -m app.onboarding [--force]
    make onboard
"""

import contextlib
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# Paths — computed from file location, not KOAN_ROOT env var
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent
KOAN_ROOT = SCRIPT_DIR.parent.parent  # koan/app/.. → koan/.. → repo root
CHECKPOINT_FILE = KOAN_ROOT / ".koan-onboarding.json"

# ---------------------------------------------------------------------------
# Terminal helpers
# ---------------------------------------------------------------------------

_use_color = (
    os.environ.get("NO_COLOR") is None
    and hasattr(sys.stdout, "isatty")
    and sys.stdout.isatty()
)

_is_interactive = (
    hasattr(sys.stdin, "isatty") and sys.stdin.isatty()
) or os.environ.get("KOAN_ONBOARDING_FORCE_TTY") == "1"


def _col(code: str, text: str) -> str:
    if not _use_color:
        return text
    return f"\033[{code}m{text}\033[0m"


def bold(text: str) -> str:
    return _col("1", text)


def green(text: str) -> str:
    return _col("32", text)


def yellow(text: str) -> str:
    return _col("33", text)


def red(text: str) -> str:
    return _col("31", text)


def dim(text: str) -> str:
    return _col("2", text)


def cyan(text: str) -> str:
    return _col("36", text)


# ---------------------------------------------------------------------------
# Input helpers
# ---------------------------------------------------------------------------

_ABORT = object()
_RESET = object()


class OnboardingReset(Exception):
    """Signal that onboarding progress should be cleared and restarted."""


def _use_textual_prompts() -> bool:
    """Return True when onboarding prompts should use Textual screens."""
    if os.environ.get("KOAN_ONBOARDING_TEXTUAL") == "0":
        return False
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    return (
        _is_interactive
        and getattr(sys.stdin, "isatty", lambda: False)()
        and getattr(sys.stdout, "isatty", lambda: False)()
    )


def _read_line(prompt: str) -> str:
    """Read a line of input, falling back to /dev/tty when stdin is not a TTY.

    When the onboarding script is invoked through ``make`` or another wrapper,
    ``sys.stdin`` may be a pipe rather than the real terminal.  In that case
    ``input()`` returns EOF immediately and the user never gets to interact.
    Reading from ``/dev/tty`` bypasses the pipe and talks directly to the
    controlling terminal.
    """
    if hasattr(sys.stdin, "isatty") and sys.stdin.isatty():
        return input(prompt)
    try:
        with open("/dev/tty", "r") as tty_in, open("/dev/tty", "w") as tty_out:
            tty_out.write(prompt)
            tty_out.flush()
            return tty_in.readline().rstrip("\n")
    except (OSError, ValueError):
        # No controlling terminal available — fall back to regular input()
        return input(prompt)


def _textual_text(prompt: str, default: Optional[str] = None) -> Optional[str]:
    try:
        from textual.app import App, ComposeResult
        from textual.binding import Binding
        from textual.containers import Container, Vertical
        from textual.widgets import Button, Footer, Header, Input, Label
    except ImportError:
        return None

    class TextPrompt(App):
        CSS = """
        App { background: #0D1117; color: #DCE2E6; }
        #box {
            width: 80; height: auto; margin: 2 4; padding: 1 2;
            border: round #3ECF8E; background: #0D1117;
        }
        #title { color: #3ECF8E; text-style: bold; }
        #hint { color: #808C94; }
        Button { margin-right: 2; }
        """
        BINDINGS = [
            Binding("escape", "cancel", "Cancel"),
            Binding("ctrl+r", "reset", "Reset install", priority=True),
            Binding("ctrl+c", "abort", "Abort", priority=True),
            Binding("ctrl+q", "abort", "Abort", priority=True),
        ]

        def compose(self) -> ComposeResult:
            yield Header(show_clock=False)
            with Vertical(id="box"):
                yield Label(prompt, id="title")
                yield Label("Enter to save · Esc to cancel", id="hint")
                yield Input(value=default or "", id="answer")
                with Container():
                    yield Button("Save", variant="success", id="save")
                    yield Button("Cancel", id="cancel")
            yield Footer()

        def on_mount(self) -> None:
            self.query_one("#answer", Input).focus()

        def on_input_submitted(self, _event: Input.Submitted) -> None:
            self.exit(self.query_one("#answer", Input).value.strip())

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "save":
                self.exit(self.query_one("#answer", Input).value.strip())
            else:
                self.exit(None)

        def action_cancel(self) -> None:
            self.exit(None)

        def action_abort(self) -> None:
            self.exit(_ABORT)

        def action_reset(self) -> None:
            self.exit(_RESET)

    return TextPrompt().run()


def _textual_choice(prompt: str, options: list[str], default: int = 0) -> Optional[int]:
    try:
        from textual.app import App, ComposeResult
        from textual.binding import Binding
        from textual.containers import Vertical
        from textual.widgets import Footer, Header, Label
    except ImportError:
        return None

    class ChoicePrompt(App):
        CSS = """
        App { background: #0D1117; color: #DCE2E6; }
        #box {
            width: 88; height: auto; margin: 2 4; padding: 1 2;
            border: round #3ECF8E; background: #0D1117;
        }
        #title { color: #3ECF8E; text-style: bold; }
        #hint { color: #808C94; }
        .option { color: #DCE2E6; padding: 0 1; }
        .selected { color: #3ECF8E; text-style: bold; background: #111820; }
        """
        BINDINGS = [
            Binding("up", "cursor_up", "Up"),
            Binding("down", "cursor_down", "Down"),
            Binding("enter", "submit", "Select"),
            Binding("escape", "cancel", "Default"),
            Binding("ctrl+r", "reset", "Reset install", priority=True),
            Binding("ctrl+c", "abort", "Abort", priority=True),
            Binding("ctrl+q", "abort", "Abort", priority=True),
        ]

        def __init__(self):
            super().__init__()
            self.selected = min(max(default, 0), len(options) - 1)

        def compose(self) -> ComposeResult:
            yield Header(show_clock=False)
            with Vertical(id="box"):
                yield Label(prompt, id="title")
                yield Label(
                    "Use arrows to choose · Enter to continue · Ctrl-R to reset install",
                    id="hint",
                )
                for i, option in enumerate(options):
                    yield Label("", id=f"choice-{i}", classes="option")
            yield Footer()

        def on_mount(self) -> None:
            self._refresh_options()

        def action_cursor_up(self) -> None:
            self.selected = (self.selected - 1) % len(options)
            self._refresh_options()

        def action_cursor_down(self) -> None:
            self.selected = (self.selected + 1) % len(options)
            self._refresh_options()

        def action_submit(self) -> None:
            self.exit(self.selected)

        def action_cancel(self) -> None:
            self.exit(None)

        def action_abort(self) -> None:
            self.exit(_ABORT)

        def action_reset(self) -> None:
            self.exit(_RESET)

        def _refresh_options(self) -> None:
            for i, option in enumerate(options):
                label = self.query_one(f"#choice-{i}", Label)
                prefix = ">" if i == self.selected else " "
                suffix = "  [default]" if i == default else ""
                label.update(f"{prefix} {i + 1}. {option}{suffix}")
                label.set_class(i == self.selected, "selected")

    return ChoicePrompt().run()


def _textual_pause(message: str) -> bool:
    try:
        from textual.app import App, ComposeResult
        from textual.binding import Binding
        from textual.containers import Vertical
        from textual.widgets import Button, Footer, Header, Label
    except ImportError:
        return False

    class PausePrompt(App):
        CSS = """
        App { background: #0D1117; color: #DCE2E6; }
        #box {
            width: 72; height: auto; margin: 2 4; padding: 1 2;
            border: round #3ECF8E; background: #0D1117;
        }
        #title { color: #3ECF8E; text-style: bold; }
        Button { margin-top: 1; }
        """
        BINDINGS = [
            Binding("enter", "continue", "Continue"),
            Binding("escape", "continue", "Continue"),
            Binding("ctrl+r", "reset", "Reset install", priority=True),
            Binding("ctrl+c", "abort", "Abort", priority=True),
            Binding("ctrl+q", "abort", "Abort", priority=True),
        ]

        def compose(self) -> ComposeResult:
            yield Header(show_clock=False)
            with Vertical(id="box"):
                yield Label(message, id="title")
                yield Button("Continue", variant="success", id="continue")
            yield Footer()

        def on_button_pressed(self, _event: Button.Pressed) -> None:
            self.exit(True)

        def action_continue(self) -> None:
            self.exit(True)

        def action_abort(self) -> None:
            self.exit(_ABORT)

        def action_reset(self) -> None:
            self.exit(_RESET)

    result = PausePrompt().run()
    if result is _ABORT:
        raise KeyboardInterrupt
    if result is _RESET:
        raise OnboardingReset
    return result is True


def ask(prompt: str, default: Optional[str] = None) -> str:
    """Prompt user for text input with optional default."""
    if not _is_interactive:
        return default or ""
    if _use_textual_prompts():
        value = _textual_text(prompt, default)
        if value is _ABORT:
            raise KeyboardInterrupt
        if value is _RESET:
            raise OnboardingReset
        if value is not None:
            return value if value else (default or "")
    suffix = f" [{default}]" if default else ""
    try:
        value = _read_line(f"  {prompt}{suffix}: ").strip()
    except KeyboardInterrupt:
        print()
        raise
    except (EOFError, OSError):
        print()
        return default or ""
    return value if value else (default or "")


def ask_yes_no(prompt: str, default: bool = True) -> bool:
    """Prompt user for yes/no answer."""
    if not _is_interactive:
        return default
    if _use_textual_prompts():
        idx = _textual_choice(prompt, ["Yes", "No"], default=0 if default else 1)
        if idx is _ABORT:
            raise KeyboardInterrupt
        if idx is _RESET:
            raise OnboardingReset
        if idx is not None:
            return idx == 0
    hint = "Y/n" if default else "y/N"
    try:
        value = _read_line(f"  {prompt} [{hint}]: ").strip().lower()
    except KeyboardInterrupt:
        print()
        raise
    except (EOFError, OSError):
        print()
        return default
    if not value:
        return default
    return value.startswith("y")


def ask_choice(prompt: str, options: list[str], default: int = 0) -> int:
    """Present numbered choices. Returns index of selected option."""
    if not _is_interactive:
        return default
    if _use_textual_prompts():
        idx = _textual_choice(prompt, options, default=default)
        if idx is _ABORT:
            raise KeyboardInterrupt
        if idx is _RESET:
            raise OnboardingReset
        if idx is not None:
            return idx
    print()
    for i, opt in enumerate(options):
        marker = bold("→") if i == default else " "
        print(f"  {marker} {i + 1}. {opt}")
    print()
    try:
        value = _read_line(f"  {prompt} [1-{len(options)}, default {default + 1}]: ").strip()
    except KeyboardInterrupt:
        print()
        raise
    except (EOFError, OSError):
        print()
        return default
    if not value:
        return default
    try:
        idx = int(value) - 1
        if 0 <= idx < len(options):
            return idx
    except ValueError:
        pass
    return default


def ask_path(prompt: str, must_exist: bool = True) -> str:
    """Prompt user for a filesystem path with ~ expansion."""
    raw = ask(prompt)
    if not raw:
        return ""
    expanded = str(Path(raw).expanduser())
    if must_exist and not Path(expanded).exists():
        print(f"  {red('✗')} Path does not exist: {expanded}")
        return ""
    return expanded


def pause(message: str = "Press Enter to continue →", *, plain: bool = False) -> None:
    """Wait for the user to press Enter before proceeding.

    Args:
        message: Prompt text to display.
        plain: When True, skip the Textual TUI and use a simple input() prompt.
               Use this when the terminal already contains content that must stay
               visible (e.g. the onboarding intro screen with the hero banner).
    """
    if not _is_interactive:
        return
    if not plain and _use_textual_prompts() and _textual_pause(message):
        return
    try:
        _read_line(f"\n  {dim(message)} ")
    except KeyboardInterrupt:
        print()
        raise
    except (EOFError, OSError):
        print()


# ---------------------------------------------------------------------------
# Onboarding state
# ---------------------------------------------------------------------------


@dataclass
class OnboardingState:
    """Persistent state for the onboarding wizard."""

    completed_steps: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    def mark_complete(self, step_name: str) -> None:
        if step_name not in self.completed_steps:
            self.completed_steps.append(step_name)

    def is_complete(self, step_name: str) -> bool:
        return step_name in self.completed_steps

    def save(self, path: Path) -> None:
        path.write_text(
            json.dumps(
                {
                    "completed_steps": self.completed_steps,
                    "data": self.data,
                },
                indent=2,
            )
        )

    @classmethod
    def load(cls, path: Path) -> "OnboardingState":
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text())
            return cls(
                completed_steps=raw.get("completed_steps", []),
                data=raw.get("data", {}),
            )
        except (json.JSONDecodeError, OSError):
            return cls()


# ---------------------------------------------------------------------------
# Step definitions
# ---------------------------------------------------------------------------


@dataclass
class Step:
    name: str
    description: str
    run: Callable[["OnboardingState"], "OnboardingState"]
    check: Optional[Callable[["OnboardingState"], bool]] = None


def _check_tool(name: str) -> Optional[str]:
    """Return tool path if found, None otherwise."""
    return shutil.which(name)


def _run_cmd(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a subprocess, capturing output."""
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30, **kwargs)


# ---------------------------------------------------------------------------
# Step 1: Prerequisites
# ---------------------------------------------------------------------------


def step_prerequisites(state: OnboardingState) -> OnboardingState:
    print(f"\n  {bold('Checking prerequisites...')}\n")

    # Python version
    py_ver = platform.python_version()
    py_ok = sys.version_info >= (3, 11)
    status = green("✓") if py_ok else red("✗")
    print(f"  {status} Python {py_ver}" + ("" if py_ok else f" {red('(3.11+ required)')}"))

    # Git
    git = _check_tool("git")
    print(f"  {green('✓') if git else red('✗')} git" + (f" ({git})" if git else " (required — install git)"))

    # Supported CLI providers
    installed_providers = _detect_installed_providers()
    provider_tools = {
        "claude": "claude",
        "cline": "cline",
        "codex": "codex",
        "copilot": "gh",
        "ollama-launch": "ollama",
    }
    for provider, tool in provider_tools.items():
        if tool is not None:
            found = _check_tool(tool)
            print(f"  {green('✓') if found else yellow('○')} {provider} provider" + (
                f" ({found})" if found else f" {dim(f'({tool} not found)')}"
            ))

    # gh CLI (optional)
    gh = _check_tool("gh")
    print(f"  {green('✓') if gh else yellow('○')} gh CLI" + (
        f" ({gh})" if gh else f" {dim('(optional — https://cli.github.com)')}"
    ))

    # Node/npm (optional)
    node = _check_tool("node")
    print(f"  {green('✓') if node else yellow('○')} node" + (
        f" ({node})" if node else f" {dim('(optional)')}"
    ))

    print()

    if not py_ok:
        print(f"  {red('Python 3.11 or later is required. Please upgrade.')}")
        sys.exit(1)

    if not git:
        print(f"  {red('git is required. Please install it.')}")
        sys.exit(1)

    state.data["installed_providers"] = installed_providers
    state.data["has_claude"] = "claude" in installed_providers
    state.data["has_gh"] = bool(gh)

    return state


# ---------------------------------------------------------------------------
# Step 2: Provider selection
# ---------------------------------------------------------------------------

PROVIDERS = [
    ("claude", "Claude Code CLI"),
    ("cline", "Cline CLI"),
    ("codex", "OpenAI Codex CLI"),
    ("copilot", "GitHub Copilot CLI"),
    ("ollama-launch", "Ollama Launch (local models via ollama)"),
]


def _provider_ready(provider: str) -> tuple[bool, str]:
    tool_by_provider = {
        "claude": "claude",
        "cline": "cline",
        "codex": "codex",
        "copilot": "gh",
        "ollama-launch": "ollama",
    }
    tool = tool_by_provider.get(provider)
    if provider == "ollama-launch":
        if not tool:
            return False, f"Unknown CLI provider: {provider}"
        if not _check_tool(tool):
            return False, f"{provider} provider selected but `{tool}` is not installed"
        return True, f"{provider} provider selected"
    if not tool:
        return False, f"Unknown CLI provider: {provider}"
    if not _check_tool(tool):
        return False, f"{provider} provider selected but `{tool}` is not installed"
    return True, f"{provider} provider ready"


def _detect_installed_providers() -> list[str]:
    """Return the list of CLI providers whose binaries are on PATH."""
    provider_tools = {
        "claude": "claude",
        "cline": "cline",
        "codex": "codex",
        "copilot": "gh",
        "ollama-launch": "ollama",
    }
    return [p for p, t in provider_tools.items() if _check_tool(t)]


def _get_config_cli_provider() -> str:
    """Read cli_provider from instance/config.yaml, return empty string if unset."""
    import yaml

    config_file = _instance_dir() / "config.yaml"
    if not config_file.exists():
        return ""
    try:
        config = yaml.safe_load(config_file.read_text()) or {}
        if not isinstance(config, dict):
            return ""
        return str(config.get("cli_provider", "")).strip().lower()
    except yaml.YAMLError:
        print(f"  {yellow('⚠')} config.yaml is malformed — treating cli_provider as unset")
        return ""


def step_provider(state: OnboardingState) -> OnboardingState:
    existing = _get_config_cli_provider()
    if existing:
        state.data["cli_provider"] = existing
        print(f"  {green('✓')} CLI provider already configured: {existing}")
        return state

    labels = [label for _key, label in PROVIDERS]
    installed = state.data.get("installed_providers")
    if installed is None:
        installed = _detect_installed_providers()
    default = 0
    for i, (key, _label) in enumerate(PROVIDERS):
        if key in installed:
            default = i
            break

    idx = ask_choice("Which CLI provider should Kōan use?", labels, default=default)
    provider = PROVIDERS[idx][0]
    state.data["cli_provider"] = provider
    config_file = _instance_dir() / "config.yaml"
    if not config_file.exists():
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text("")
    _update_config_yaml_preserving_comments(config_file, ["cli_provider"], provider)
    print(f"  {green('✓')} CLI provider: {provider}")
    return state


def check_provider(state: OnboardingState) -> bool:
    return bool(_get_config_cli_provider())


# ---------------------------------------------------------------------------
# Step 3: Model configuration
# ---------------------------------------------------------------------------

MODEL_FIELDS = [
    ("mission", "Main mission execution"),
    ("chat", "Telegram / dashboard chat responses"),
    ("lightweight", "Low-cost calls (pick_mission, contemplative, format_outbox)"),
    ("fallback", "Fallback when primary model is overloaded"),
    ("review_mode", "Override model for REVIEW mode (cheaper audits)"),
    ("reflect", "Model for review reflection pass"),
]

_PROVIDER_MODEL_DEFAULTS: dict[str, dict[str, str]] = {
    "claude": {
        "mission": "",
        "chat": "",
        "lightweight": "haiku",
        "fallback": "sonnet",
        "review_mode": "",
        "reflect": "",
    },
    "cline": {
        "mission": "",
        "chat": "",
        "lightweight": "",
        "fallback": "",
        "review_mode": "",
        "reflect": "",
    },
    "codex": {
        "mission": "gpt-5.3-codex",
        "chat": "gpt-5.5",
        "lightweight": "gpt-5.5",
        "fallback": "",
        "review_mode": "gpt-5.3-codex",
        "reflect": "gpt-5.5",
    },
    "copilot": {
        "mission": "",
        "chat": "",
        "lightweight": "haiku",
        "fallback": "sonnet",
        "review_mode": "",
        "reflect": "",
    },
    "ollama-launch": {
        "mission": "qwen2.5-coder:14b",
        "chat": "qwen2.5-coder:14b",
        "lightweight": "qwen2.5-coder:7b",
        "fallback": "",
        "review_mode": "qwen2.5-coder:14b",
        "reflect": "qwen2.5-coder:7b",
    },
}


def _update_config_yaml_preserving_comments(
    config_file: Path,
    keys: list[str],
    value,
) -> None:
    """Set a nested key in config_file while preserving comments and formatting.

    Uses ruamel.yaml to round-trip the file; falls back to plain pyyaml
    when ruamel is unavailable. Delegates to the shared
    :func:`app.utils.update_config_yaml` implementation.
    """
    from app.utils import update_config_yaml

    update_config_yaml(config_file, keys, value)


def _update_config_yaml_models(provider: str, models: dict[str, str]) -> None:
    """Update the models.{provider} section in config.yaml, preserving comments."""
    config_file = _instance_dir() / "config.yaml"
    if not config_file.exists():
        return

    _update_config_yaml_preserving_comments(
        config_file,
        ["models", provider],
        models,
    )


def step_models(state: OnboardingState) -> OnboardingState:
    import yaml

    provider = state.data.get("cli_provider") or _get_config_cli_provider() or "claude"
    config_file = _instance_dir() / "config.yaml"

    # Skip if provider-specific models already configured in config.yaml
    if config_file.exists():
        try:
            config = yaml.safe_load(config_file.read_text()) or {}
            existing = config.get("models", {}).get(provider)
            if isinstance(existing, dict) and existing:
                print(f"  {green('✓')} Model configuration for {provider} already set.")
                return state
        except yaml.YAMLError:
            pass

    defaults = _PROVIDER_MODEL_DEFAULTS.get(provider, _PROVIDER_MODEL_DEFAULTS["claude"]).copy()

    print(f"\n  {bold('Recommended models for')} {bold(provider)}:")
    print()
    for key, desc in MODEL_FIELDS:
        val = defaults[key]
        display = f'"{val}"' if val else "(provider default)"
        print(f"    {key:<14} {display:<22}  {dim(desc)}")
    print()

    if ask_yes_no(f"Accept recommended models for {provider}?", default=True):
        _update_config_yaml_models(provider, defaults)
        print(f"  {green('✓')} Saved recommended models for {provider}.")
        state.data["models"] = defaults
        return state

    # User wants to customize — walk through each field
    print(f"\n  {bold('Customize models')} — press Enter to keep the default, or type a new value.")
    print()
    customized: dict[str, str] = {}
    for key, desc in MODEL_FIELDS:
        default_val = defaults[key]
        hint = f' [{default_val}]' if default_val else " [provider default]"
        val = ask(f"  {key}{hint}")
        customized[key] = val if val else default_val

    _update_config_yaml_models(provider, customized)
    print(f"\n  {green('✓')} Saved custom models for {provider}.")
    state.data["models"] = customized
    return state


def check_models(state: OnboardingState) -> bool:
    import yaml

    provider = state.data.get("cli_provider") or _get_config_cli_provider() or "claude"
    config_file = _instance_dir() / "config.yaml"
    if not config_file.exists():
        return False
    try:
        config = yaml.safe_load(config_file.read_text()) or {}
        existing = config.get("models", {}).get(provider)
        return isinstance(existing, dict) and bool(existing)
    except yaml.YAMLError:
        return False


# ---------------------------------------------------------------------------
# Step 2: Instance initialization
# ---------------------------------------------------------------------------


def _instance_dir() -> Path:
    return KOAN_ROOT / "instance"


def _env_file() -> Path:
    return KOAN_ROOT / ".env"


def _get_env_for_root(key: str) -> Optional[str]:
    from app.onboarding_helpers import get_env_var

    return get_env_var(key, KOAN_ROOT / ".env")


def step_instance_init(state: OnboardingState) -> OnboardingState:
    from app.onboarding_helpers import create_env_file, create_instance_dir, update_env_var

    instance_dir = _instance_dir()
    env_file = _env_file()

    if instance_dir.exists() and env_file.exists():
        print(f"  {green('✓')} Instance directory and .env already exist.")
        return state

    print("  Creating instance directory and .env file...")

    if not instance_dir.exists():
        ok = create_instance_dir(KOAN_ROOT)
        if ok:
            print(f"  {green('✓')} Created instance/")
        else:
            print(f"  {red('✗')} Failed to create instance/ — is instance.example/ present?")
            sys.exit(1)

    if not env_file.exists():
        ok = create_env_file(KOAN_ROOT)
        if not ok:
            print(f"  {red('✗')} Failed to create .env — is env.example present?")
            sys.exit(1)
        from app.onboarding_helpers import paths_for_root

        if not paths_for_root(KOAN_ROOT)["env_example"].exists():
            print(
                f"  {green('✓')} No env.example — created .env "
                f"(configuration supplied via environment variables)"
            )
        else:
            print(f"  {green('✓')} Created .env")

    update_env_var("KOAN_ROOT", str(KOAN_ROOT), env_file)
    print(f"  {green('✓')} Set KOAN_ROOT={KOAN_ROOT}")

    return state


def check_instance_init(state: OnboardingState) -> bool:
    return _instance_dir().exists() and _env_file().exists()


# ---------------------------------------------------------------------------
# Step 3: Virtual environment
# ---------------------------------------------------------------------------


def step_venv(state: OnboardingState) -> OnboardingState:
    venv_marker = KOAN_ROOT / ".venv" / ".installed"
    if venv_marker.exists():
        print(f"  {green('✓')} Virtual environment already set up.")
        return state

    print(f"  Running {bold('make setup')} to create virtual environment...")
    print(f"  {dim('(this may take a minute)')}")
    print()

    try:
        result = subprocess.run(
            ["make", "setup"],
            cwd=str(KOAN_ROOT),
            timeout=300,
        )
        if result.returncode == 0:
            print(f"\n  {green('✓')} Virtual environment ready.")
        else:
            print(f"\n  {red('✗')} make setup failed (exit code {result.returncode}).")
            print(f"  {dim('You can retry by running: make setup')}")
    except subprocess.TimeoutExpired:
        print(f"\n  {red('✗')} make setup timed out.")
    except FileNotFoundError:
        print(f"\n  {red('✗')} make not found. Run: pip install -r koan/requirements.txt")

    return state


def check_venv(state: OnboardingState) -> bool:
    return (KOAN_ROOT / ".venv").exists()


# ---------------------------------------------------------------------------
# Step 4: Messaging configuration
# ---------------------------------------------------------------------------


def step_messaging(state: OnboardingState) -> OnboardingState:
    from app.onboarding_helpers import (
        get_chat_id_from_updates,
        get_env_var,
        update_env_var,
        verify_telegram_token,
    )

    # Check if already configured (any supported provider)
    env_file = KOAN_ROOT / ".env"
    token = get_env_var("KOAN_TELEGRAM_TOKEN", env_file)
    chat_id = get_env_var("KOAN_TELEGRAM_CHAT_ID", env_file)
    if token and "your-bot-token" not in token and chat_id and "your-chat-id" not in chat_id:
        print(f"  {green('✓')} Messaging already configured.")
        return state
    if get_env_var("KOAN_SLACK_BOT_TOKEN", env_file) and get_env_var("KOAN_SLACK_CHANNEL_ID", env_file):
        print(f"  {green('✓')} Messaging already configured.")
        return state
    if get_env_var("KOAN_MATRIX_ACCESS_TOKEN", env_file) and get_env_var("KOAN_MATRIX_ROOM_ID", env_file):
        print(f"  {green('✓')} Messaging already configured.")
        return state

    provider_idx = ask_choice(
        "Which messaging platform?",
        ["Telegram (default)", "Slack", "Matrix"],
        default=0,
    )

    if provider_idx == 1:
        # Slack setup
        print(f"\n  {bold('Slack setup')}")
        print(f"  {dim('See docs/messaging/slack.md for setup instructions.')}")
        print()

        bot_token = ask("Slack Bot Token (xoxb-...)")
        app_token = ask("Slack App Token (xapp-...)")
        channel_id = ask("Slack Channel ID (C01234ABCD)")

        if bot_token and app_token and channel_id:
            update_env_var("KOAN_SLACK_BOT_TOKEN", bot_token, env_file)
            update_env_var("KOAN_SLACK_APP_TOKEN", app_token, env_file)
            update_env_var("KOAN_SLACK_CHANNEL_ID", channel_id, env_file)
            update_env_var("KOAN_MESSAGING_PROVIDER", "slack", env_file)
            state.data["messaging_provider"] = "slack"
            print(f"\n  {green('✓')} Slack configuration saved.")
        else:
            print(f"\n  {yellow('○')} Incomplete Slack config — skipping for now.")
    elif provider_idx == 2:
        # Matrix setup
        print(f"\n  {bold('Matrix setup')}")
        print(f"  {dim('See docs/messaging/matrix.md for setup instructions.')}")
        print()

        homeserver = ask("Matrix Homeserver URL (https://matrix.org)")
        access_token = ask("Matrix access token (syt_...)")
        user_id = ask("Bot Matrix user ID (@koan:matrix.org)")
        room_id = ask("Room ID (!abcdef:matrix.org)")

        if homeserver and access_token and user_id and room_id:
            update_env_var("KOAN_MATRIX_HOMESERVER", homeserver, env_file)
            update_env_var("KOAN_MATRIX_ACCESS_TOKEN", access_token, env_file)
            update_env_var("KOAN_MATRIX_USER_ID", user_id, env_file)
            update_env_var("KOAN_MATRIX_ROOM_ID", room_id, env_file)
            update_env_var("KOAN_MESSAGING_PROVIDER", "matrix", env_file)
            state.data["messaging_provider"] = "matrix"
            print(f"\n  {green('✓')} Matrix configuration saved.")
        else:
            print(f"\n  {yellow('○')} Incomplete Matrix config — skipping for now.")
    else:
        # Telegram setup
        print(f"\n  {bold('Telegram setup')}")
        print(f"  {dim('1. Open Telegram, search for @BotFather')}")
        print(f"  {dim('2. Send /newbot and follow the instructions')}")
        print(f"  {dim('3. Copy the bot token (format: 123456789:ABC-DEF1234...)')}")
        print()

        bot_token = ask("Bot token")
        if not bot_token:
            print(f"  {yellow('○')} No token provided — skipping messaging setup.")
            return state

        # Verify token
        print("  Verifying token...", end="", flush=True)
        result = verify_telegram_token(bot_token)
        if result.get("valid"):
            print(f" {green('✓')} Bot: @{result.get('username', '?')}")
        else:
            print(f" {red('✗')} Invalid token: {result.get('error', 'unknown error')}")
            return state

        update_env_var("KOAN_TELEGRAM_TOKEN", bot_token, env_file)

        # Try to auto-detect chat ID
        print(f"\n  {dim('Send any message to your bot on Telegram, then press Enter.')}")
        if _is_interactive:
            with contextlib.suppress(EOFError, KeyboardInterrupt):
                input(f"  {dim('Press Enter when ready...')}")

        chat_id_detected = get_chat_id_from_updates(bot_token)
        if chat_id_detected:
            print(f"  {green('✓')} Detected chat ID: {chat_id_detected}")
            update_env_var("KOAN_TELEGRAM_CHAT_ID", chat_id_detected, env_file)
        else:
            print(f"  {yellow('○')} Could not auto-detect chat ID.")
            manual_id = ask("Enter chat ID manually")
            if manual_id:
                update_env_var("KOAN_TELEGRAM_CHAT_ID", manual_id, env_file)
            else:
                print(f"  {yellow('○')} No chat ID — you can set it later in .env")
                return state

        state.data["messaging_provider"] = "telegram"
        print(f"\n  {green('✓')} Telegram configuration saved.")

    return state


def check_messaging(state: OnboardingState) -> bool:
    from app.onboarding_helpers import get_env_var

    # Telegram check
    env_file = KOAN_ROOT / ".env"
    token = get_env_var("KOAN_TELEGRAM_TOKEN", env_file)
    chat_id = get_env_var("KOAN_TELEGRAM_CHAT_ID", env_file)
    if token and "your-bot-token" not in token and chat_id and "your-chat-id" not in chat_id:
        return True
    # Slack check
    slack_token = get_env_var("KOAN_SLACK_BOT_TOKEN", env_file)
    if slack_token:
        return True
    return False


# ---------------------------------------------------------------------------
# Step 5: Language preference
# ---------------------------------------------------------------------------

LANGUAGES = [
    "English (default)",
    "French",
    "Spanish",
    "German",
    "Japanese",
    "Portuguese",
    "Italian",
    "Chinese",
    "Korean",
    "Dutch",
]


def step_language(state: OnboardingState) -> OnboardingState:
    idx = ask_choice("What language should Kōan reply in?", LANGUAGES, default=0)

    if idx == 0:
        print(f"  {green('✓')} Language: English (default)")
        state.data["language"] = "english"
    else:
        lang = LANGUAGES[idx].lower()
        # Set KOAN_ROOT for language_preference module
        os.environ.setdefault("KOAN_ROOT", str(KOAN_ROOT))
        from app.language_preference import set_language

        set_language(lang)
        print(f"  {green('✓')} Language set to {LANGUAGES[idx]}.")
        print(f"  {dim('Change later with /language')}")
        state.data["language"] = lang

    return state


# ---------------------------------------------------------------------------
# Step 6: Personality / soul preset
# ---------------------------------------------------------------------------

SOUL_PRESETS = {
    "sparring": {
        "label": "Sparring partner (default)",
        "desc": "Analytical, direct, dry humor — challenges your thinking",
        "file": "soul-sparring.md",
    },
    "mentor": {
        "label": "Mentor",
        "desc": "Patient, pedagogic, encouraging — guides and teaches",
        "file": "soul-mentor.md",
    },
    "pragmatist": {
        "label": "Pragmatist",
        "desc": "Minimal, efficient, no-nonsense — gets things done",
        "file": "soul-pragmatist.md",
    },
    "creative": {
        "label": "Creative",
        "desc": "Playful, exploratory, lateral thinking — suggests unexpected angles",
        "file": "soul-creative.md",
    },
    "butler": {
        "label": "Butler",
        "desc": "Formal, polished, deferential — professional and respectful",
        "file": "soul-butler.md",
    },
}

PRESET_KEYS = list(SOUL_PRESETS.keys())


def step_personality(state: OnboardingState) -> OnboardingState:
    options = [f"{p['label']} — {dim(p['desc'])}" for p in SOUL_PRESETS.values()]
    idx = ask_choice("Choose a personality for your agent:", options, default=0)
    preset_key = PRESET_KEYS[idx]
    preset = SOUL_PRESETS[preset_key]

    # Apply preset
    preset_dir = KOAN_ROOT / "instance.example" / "soul-presets"
    preset_file = preset_dir / preset["file"]
    soul_dest = _instance_dir() / "soul.md"

    if preset_file.exists():
        shutil.copy(preset_file, soul_dest)
        print(f"  {green('✓')} Personality: {preset['label']}")
    elif preset_key == "sparring":
        # Default soul.md is already the sparring partner
        default_soul = KOAN_ROOT / "instance.example" / "soul.md"
        if default_soul.exists() and not soul_dest.exists():
            shutil.copy(default_soul, soul_dest)
        print(f"  {green('✓')} Personality: {preset['label']} (default)")
    else:
        # Preset file missing — fall back to default
        print(f"  {yellow('○')} Preset file not found, using default personality.")

    # Address style
    print()
    address_idx = ask_choice(
        "How should the agent address you?",
        ['"my human" (default)', "By first name", "Boss", "Custom"],
        default=0,
    )

    address_style = "my human"
    if address_idx == 1:
        name = ask("Your first name")
        if name:
            address_style = name
    elif address_idx == 2:
        address_style = "boss"
    elif address_idx == 3:
        custom = ask("Custom address")
        if custom:
            address_style = custom

    state.data["personality"] = preset_key
    state.data["address_style"] = address_style

    # If an address style other than default was chosen, append it to soul.md
    if address_style != "my human" and soul_dest.exists():
        current = soul_dest.read_text()
        if "## Address Style" not in current:
            addition = (
                f"\n\n---\n\n## Address Style\n\n"
                f'When addressing the human directly, use "{address_style}".\n'
            )
            soul_dest.write_text(current + addition)

    print(f"  {green('✓')} Address style: {address_style}")

    return state


# ---------------------------------------------------------------------------
# Step 7: Kōan workspace project
# ---------------------------------------------------------------------------


def step_workspace_koan(state: OnboardingState) -> OnboardingState:
    from app.onboarding_helpers import setup_workspace_koan

    print(f"  Ensuring Kōan is available as {bold('workspace/koan')}...")
    ok, message = setup_workspace_koan(KOAN_ROOT)
    if not ok:
        print(f"  {red('✗')} {message}")
        raise RuntimeError(message)
    print(f"  {green('✓')} {message}")
    state.data["workspace_koan"] = True
    return state


def check_workspace_koan(state: OnboardingState) -> bool:
    from app.onboarding_helpers import _has_koan_remote

    path = KOAN_ROOT / "workspace" / "koan"
    return path.is_dir() and _has_koan_remote(path)


# ---------------------------------------------------------------------------
# Step 8: Project registration
# ---------------------------------------------------------------------------


def step_projects(state: OnboardingState) -> OnboardingState:
    projects_yaml = KOAN_ROOT / "projects.yaml"
    if projects_yaml.exists():
        print(f"  {green('✓')} projects.yaml already exists.")
        return state

    import yaml

    projects = []
    print(f"  {dim('Register at least one project for the agent to work on.')}")
    print(f"  {dim('Enter the full path to each project directory.')}")
    print()

    max_attempts = 50 if _is_interactive else 1
    attempts = 0
    while attempts < max_attempts:
        attempts += 1
        path = ask_path("Project path (or empty to finish)", must_exist=False)
        if not path:
            if not projects:
                if not _is_interactive:
                    break
                print(f"  {yellow('○')} At least one project is required.")
                continue
            break

        expanded = Path(path).expanduser()
        if not expanded.is_dir():
            print(f"  {red('✗')} Not a directory: {expanded}")
            continue

        name = expanded.name
        is_git = (expanded / ".git").exists()

        if not is_git:
            print(f"  {yellow('○')} Warning: {expanded} is not a git repository.")
            if not ask_yes_no("Add anyway?", default=False):
                continue

        projects.append({"name": name, "path": str(expanded)})
        print(f"  {green('✓')} Added: {name} ({expanded})")
        print()

        if not ask_yes_no("Add another project?", default=False):
            break

    if not projects:
        print(f"  {yellow('○')} No projects configured — you can add them later in projects.yaml")
        return state

    # Save projects.yaml
    config = {
        "defaults": {
            "git_auto_merge": {
                "enabled": False,
                "base_branch": "main",
                "strategy": "squash",
            }
        },
        "projects": {},
    }
    for p in sorted(projects, key=lambda x: x["name"].lower()):
        config["projects"][p["name"]] = {"path": p["path"]}

    header = (
        "# projects.yaml — Project configuration for Kōan\n"
        "#\n"
        "# See projects.example.yaml for full documentation.\n\n"
    )
    projects_yaml.write_text(
        header + yaml.dump(config, default_flow_style=False, sort_keys=False)
    )

    # Try to populate GitHub URLs
    os.environ.setdefault("KOAN_ROOT", str(KOAN_ROOT))
    try:
        from app.projects_config import ensure_github_urls

        msgs = ensure_github_urls(str(KOAN_ROOT))
        for m in msgs:
            print(f"  {dim(m)}")
    except (ImportError, OSError, ValueError):
        pass

    state.data["project_count"] = len(projects)
    print(f"\n  {green('✓')} Saved {len(projects)} project(s) to projects.yaml")
    return state


def check_projects(state: OnboardingState) -> bool:
    return (KOAN_ROOT / "projects.yaml").exists() or (KOAN_ROOT / "workspace" / "koan").is_dir()


# ---------------------------------------------------------------------------
# Step 9: GitHub identity
# ---------------------------------------------------------------------------


def step_github(state: OnboardingState) -> OnboardingState:
    if not state.data.get("has_gh"):
        print(f"  {dim('gh CLI not found — skipping GitHub setup.')}")
        print(f"  {dim('Install gh from https://cli.github.com to enable GitHub features.')}")
        return state

    # Check auth status
    try:
        result = _run_cmd(["gh", "auth", "status"])
        authed = result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        authed = False

    if not authed:
        print(f"  {yellow('○')} gh is not authenticated.")
        if ask_yes_no("Run gh auth login now?", default=True):
            # Interactive — inherit stdio
            subprocess.run(["gh", "auth", "login"])
            # Re-check
            try:
                result = _run_cmd(["gh", "auth", "status"])
                authed = result.returncode == 0
            except (OSError, subprocess.SubprocessError):
                authed = False
    else:
        print(f"  {green('✓')} gh is authenticated.")

    # GitHub @mentions
    if ask_yes_no("Enable Kōan to respond to GitHub @mentions?", default=False):
        # Detect nickname
        nickname = ""
        try:
            result = _run_cmd(["gh", "api", "user", "--jq", ".login"])
            if result.returncode == 0:
                nickname = result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass

        nickname = ask("GitHub bot nickname", default=nickname)
        auth_users_str = ask("Authorized users (comma-separated, or * for all)", default="*")

        if auth_users_str == "*":
            auth_users = ["*"]
        else:
            auth_users = [u.strip() for u in auth_users_str.split(",") if u.strip()]

        state.data["github_nickname"] = nickname
        state.data["github_authorized_users"] = auth_users
        state.data["github_commands_enabled"] = True

        # Update config.yaml
        _update_config_yaml_github(nickname, auth_users)
        print(f"  {green('✓')} GitHub @mention support configured.")
    else:
        state.data["github_commands_enabled"] = False

    # Git email
    git_email = ""
    try:
        result = _run_cmd(["git", "config", "user.email"])
        if result.returncode == 0:
            git_email = result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass

    email = ask("Git email for Kōan's commits", default=git_email)
    if email:
        from app.onboarding_helpers import update_env_var

        update_env_var("KOAN_EMAIL", email, KOAN_ROOT / ".env")
        print(f"  {green('✓')} Git email: {email}")

    return state


def _update_config_yaml_github(nickname: str, auth_users: list[str]) -> None:
    """Update the github section in config.yaml, preserving comments."""
    config_file = _instance_dir() / "config.yaml"
    if not config_file.exists():
        return

    _update_config_yaml_preserving_comments(
        config_file,
        ["github"],
        {
            "nickname": nickname,
            "commands_enabled": True,
            "authorized_users": auth_users,
        },
    )


# ---------------------------------------------------------------------------
# Step 9: Deployment method
# ---------------------------------------------------------------------------


def step_deployment(state: OnboardingState) -> OnboardingState:
    is_linux = platform.system() == "Linux"

    options = ["Terminal dashboard — make koan (default)"]
    option_keys = ["terminal"]

    if is_linux:
        options.append("Systemd — automatic service management")
        option_keys.append("systemd")

    idx = ask_choice("How do you want to run Kōan?", options, default=0)
    method = option_keys[idx]

    if method == "systemd":
        print(f"\n  {dim('Systemd service will be installed on first `make start`.')}")
        print(f"  {dim('Or run: make install-systemctl-service')}")

    else:
        print(f"\n  {dim('Start with: make koan')}")
        print(f"  {dim('Detach or quit from the terminal dashboard')}")

    state.data["deployment_method"] = method
    print(f"\n  {green('✓')} Deployment method: {method}")
    return state


# ---------------------------------------------------------------------------
# Step 10: Final verification
# ---------------------------------------------------------------------------


def step_final(state: OnboardingState) -> OnboardingState:
    from app.onboarding_helpers import get_env_var

    print(f"\n  {bold('Configuration Summary')}")
    print(f"  {'─' * 40}")

    # Instance
    inst_ok = _instance_dir().exists()
    print(f"  Instance directory:  {green('✓') if inst_ok else red('✗')}")

    # .env
    env_ok = _env_file().exists()
    print(f"  Environment file:    {green('✓') if env_ok else red('✗')}")

    # Messaging
    provider = state.data.get("messaging_provider", "telegram")
    msg_ok = check_messaging(state)
    print(f"  Messaging ({provider}):  {green('✓') if msg_ok else yellow('○ not configured')}")

    # Language
    lang = state.data.get("language", "english")
    print(f"  Language:            {lang}")

    # Personality
    personality = state.data.get("personality", "sparring")
    preset = SOUL_PRESETS.get(personality, {})
    print(f"  Personality:         {preset.get('label', personality)}")

    # Projects
    proj_ok = check_projects(state)
    project_hint = "workspace/koan" if (KOAN_ROOT / "workspace" / "koan").is_dir() else "not configured"
    print(f"  Default project:     {green('✓') if proj_ok else yellow('○')} {project_hint}")

    # GitHub
    gh_enabled = state.data.get("github_commands_enabled", False)
    print(f"  GitHub @mentions:    {'enabled' if gh_enabled else dim('disabled')}")

    # Deployment
    deploy = state.data.get("deployment_method", "terminal")
    print(f"  Deployment:          {deploy}")

    # Claude CLI
    has_claude = state.data.get("has_claude", bool(_check_tool("claude")))
    print(f"  Claude CLI:          {green('✓') if has_claude else yellow('○ not found')}")

    provider = state.data.get("cli_provider") or _get_config_cli_provider() or "claude"
    provider_ok, provider_msg = _provider_ready(provider)
    print(f"  CLI provider:        {green('✓') if provider_ok else red('✗')} {provider}")

    print(f"  {'─' * 40}")
    print()

    # Validation
    issues = []
    blocking_issues = []
    if not inst_ok:
        issues.append("Instance directory missing")
        blocking_issues.append("Instance directory missing")
    if not env_ok:
        issues.append(".env file missing")
        blocking_issues.append(".env file missing")
    if not msg_ok:
        issues.append("Messaging not configured")
    if not proj_ok:
        issues.append("Default workspace project not configured")
    if not provider_ok:
        issues.append(provider_msg)
        blocking_issues.append(provider_msg)

    if issues:
        print(f"  {yellow('Warnings:')}")
        for issue in issues:
            print(f"    {yellow('○')} {issue}")
        print()

    if blocking_issues:
        raise RuntimeError("Setup incomplete: " + "; ".join(blocking_issues))

    print(f"\n  {bold('Next steps:')}")
    print(f"  {dim('1. Start Kōan:           make koan')}")
    print(f"  {dim('2. See commands:         /help')}")
    print(f"  {dim('3. Add your first repo:  /add_project <github-url>')}")
    print(f"  {dim('4. Watch logs:           make logs')}")

    return state


# ---------------------------------------------------------------------------
# Step registry
# ---------------------------------------------------------------------------


STEPS = [
    Step("prerequisites", "Check prerequisites", step_prerequisites),
    Step("instance_init", "Initialize instance", step_instance_init, check_instance_init),
    Step("provider", "Choose CLI provider", step_provider, check_provider),
    Step("models", "Configure models", step_models, check_models),
    Step("venv", "Set up virtual environment", step_venv, check_venv),
    Step("messaging", "Configure messaging", step_messaging, check_messaging),
    Step("language", "Set language preference", step_language),
    Step("personality", "Choose agent personality", step_personality),
    Step("workspace_koan", "Set up Kōan workspace project", step_workspace_koan, check_workspace_koan),
    Step("github", "Configure GitHub", step_github),
    Step("deployment", "Choose deployment method", step_deployment),
    Step("final", "Verify and launch", step_final),
]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def _osc8_link(url: str, text: str) -> str:
    """Return an OSC 8 hyperlink when color is enabled, otherwise plain text."""
    if not _use_color:
        return f"{text}  {dim(f'({url})')}"
    return f"\033]8;;{url}\033\\{text}\033]8;;\033\\"


def _get_version_info() -> tuple[Optional[str], Optional[str]]:
    """Read version from pyproject.toml and git HEAD."""
    version: Optional[str] = None
    pyproject = KOAN_ROOT / "pyproject.toml"
    if pyproject.exists():
        try:
            for line in pyproject.read_text().splitlines():
                if line.startswith("version"):
                    version = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
        except Exception as e:
            print(f"[onboarding] warning reading version: {e}", file=sys.stderr)

    commit: Optional[str] = None
    try:
        result = subprocess.run(
            ["git", "-C", str(KOAN_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            commit = result.stdout.strip()
    except Exception as e:
        print(f"[onboarding] warning reading commit: {e}", file=sys.stderr)

    return version, commit


def _print_intro_screen() -> None:
    """Print the onboarding intro screen with hero banner and welcome info."""
    from app.banners import print_hero_banner

    # Clear screen for a clean slate (home first, then clear display + scrollback)
    sys.stdout.write("\033[H\033[2J\033[3J")
    sys.stdout.flush()

    print_hero_banner()

    print(f"  {bold('Welcome to Kōan')} — your autonomous coding companion")
    print()

    version, commit = _get_version_info()
    info_parts: list[str] = []
    if version:
        info_parts.append(f"version {version}")
    if commit:
        info_parts.append(f"commit {commit}")
    if info_parts:
        print(f"  {dim(' · '.join(info_parts))}")
        print()

    website_url = "https://koan.anantys.com"
    print(f"  {dim('Website:')}  {_osc8_link(website_url, 'koan.anantys.com')}")
    print()

    docs_url = "https://koan.anantys.com/docs"
    print(f"  {dim('Docs:')}     {_osc8_link(docs_url, 'koan.anantys.com/docs')}")
    print()

    total = len(STEPS)
    print(
        f"  {dim(f'{total} steps')}  {dim('·')}  "
        f"{dim('~5 minutes')}  {dim('·')}  "
        f"{dim('progress saved automatically')}"
    )
    print()

    pause("Press Enter to start setup  ·  Ctrl-C to abort", plain=True)


def run_onboarding(force: bool = False) -> None:
    """Run the interactive onboarding wizard."""
    while True:
        if force and CHECKPOINT_FILE.exists():
            CHECKPOINT_FILE.unlink()
            print(f"  {dim('Cleared previous progress (--force)')}")
            print()
        force = False

        state = OnboardingState.load(CHECKPOINT_FILE)

        try:
            # Intro screen on first run (and after reset)
            if not state.completed_steps:
                _print_intro_screen()
                print(f"  {bold('Welcome!')} This wizard will walk you through setting up Kōan.")
                print("  It takes about 5 minutes. Progress is saved after each step.")
                print()
                print(f"  {dim('Navigation: follow the prompts at each step.')}")
                print(f"  {dim('Ctrl-C aborts. Ctrl-R resets onboarding progress.')}")
            else:
                completed = len(state.completed_steps)
                print(f"  {dim(f'Resuming from step {completed + 1} (progress loaded)')}")
                print()

            total = len(STEPS)
            for i, step in enumerate(STEPS, 1):
                # Skip if already completed (and file-based check passes too)
                already_done = state.is_complete(step.name)
                if already_done and step.check and step.check(state):
                    continue
                if already_done and not step.check:
                    continue

                print(f"\n{'─' * 50}")
                print(f"  {bold(f'Step {i}/{total}')} — {step.description}")
                print(f"{'─' * 50}")

                try:
                    state = step.run(state)
                    state.mark_complete(step.name)
                    state.save(CHECKPOINT_FILE)
                except OnboardingReset:
                    raise
                except KeyboardInterrupt:
                    print(f"\n\n  {yellow('Interrupted.')} Progress saved — run again to resume.")
                    state.save(CHECKPOINT_FILE)
                    sys.exit(130)
                except Exception as e:
                    print(f"\n  {red(f'Error in step {step.name}:')} {e}")
                    print(f"  {dim('Progress saved — run again to resume from this step.')}")
                    state.save(CHECKPOINT_FILE)
                    sys.exit(1)

        except OnboardingReset:
            if CHECKPOINT_FILE.exists():
                CHECKPOINT_FILE.unlink()
            # Wipe provider choice from config.yaml so the wizard re-prompts on restart
            config_file = _instance_dir() / "config.yaml"
            if config_file.exists():
                _update_config_yaml_preserving_comments(config_file, ["cli_provider"], "")
            print(f"\n  {yellow('Install reset.')} {dim('Onboarding progress cleared; restarting.')}")
            print()
            continue
        except KeyboardInterrupt:
            print(f"\n\n  {yellow('Interrupted.')} Progress saved — run again to resume.")
            state.save(CHECKPOINT_FILE)
            sys.exit(130)

        # Cleanup checkpoint on success
        if CHECKPOINT_FILE.exists():
            CHECKPOINT_FILE.unlink()

        print(f"\n  {green(bold('Setup complete!'))}\n")
        return


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Kōan Onboarding Wizard")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Restart onboarding from scratch",
    )
    args = parser.parse_args()

    run_onboarding(force=args.force)


if __name__ == "__main__":
    main()
