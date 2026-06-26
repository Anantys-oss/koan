"""Messaging provider abstraction layer.

Decouples Kōan's communication logic from any specific messaging platform.
Supports Telegram (default), Slack, Matrix, and Discord providers.

Usage:
    from app.messaging import get_messaging_provider
    provider = get_messaging_provider()
    provider.send_message("Hello from Kōan")
"""

import contextlib
import os
import sys
import threading
from typing import Dict, Optional, Type

from app.messaging.base import DEFAULT_MAX_MESSAGE_SIZE, MessagingProvider, Update, Message, Reaction

# Provider registry: name -> class
_providers: Dict[str, Type[MessagingProvider]] = {}

# Singleton instance (guarded by _instance_lock)
_instance: Optional[MessagingProvider] = None
_instance_lock = threading.Lock()

# Guards one-time module loading in _ensure_providers_loaded()
_load_lock = threading.Lock()

# Set to True after ``_ensure_providers_loaded`` walks ``_PROVIDER_MODULES``
# once.  Tracking loop completion explicitly — rather than inferring it from
# ``bool(_providers)`` — avoids skipping unloaded modules when something
# imported a single provider as a side-effect before the loader ran.
_modules_loaded: bool = False

# List of known provider modules for auto-loading
_PROVIDER_MODULES = [
    "app.messaging.telegram",
    "app.messaging.slack",
    "app.messaging.matrix",
    "app.messaging.discord",
]


def _write_error(message: str):
    """Write an error message to stderr."""
    print(f"[messaging] {message}", file=sys.stderr)


def register_provider(name: str):
    """Decorator to register a messaging provider class.

    Usage:
        @register_provider("telegram")
        class TelegramProvider(MessagingProvider):
            ...
    """
    def decorator(cls: Type[MessagingProvider]):
        _providers[name] = cls
        return cls
    return decorator


def _create_provider(name: str) -> MessagingProvider:
    """Create and configure a provider instance.

    Args:
        name: Provider identifier (must be registered)

    Returns:
        Configured MessagingProvider instance

    Raises:
        SystemExit: If provider unknown or configuration fails
    """
    # Ensure providers are imported (triggers @register_provider decorators)
    _ensure_providers_loaded()

    if name not in _providers:
        valid = ", ".join(sorted(_providers.keys())) or "(none loaded)"
        _write_error(f"Unknown messaging provider: {name!r}. Valid options: {valid}")
        raise SystemExit(1)

    instance = _providers[name]()
    if not instance.configure():
        raise SystemExit(1)

    return instance


def get_messaging_provider(provider_name_override: Optional[str] = None) -> MessagingProvider:
    """Get the active messaging provider (lazy singleton).

    Resolution order:
        1. provider_name_override parameter (for testing)
        2. KOAN_MESSAGING_PROVIDER env var
        3. messaging.provider from config.yaml
        4. Default: "telegram"

    Args:
        provider_name_override: Override provider name (bypasses singleton cache)

    Returns:
        Configured MessagingProvider instance

    Raises:
        SystemExit: If provider name is unknown or credentials are missing
    """
    global _instance

    if _instance is not None and provider_name_override is None:
        return _instance

    with _instance_lock:
        # Double-check under lock
        if _instance is not None and provider_name_override is None:
            return _instance

        name = provider_name_override or resolve_provider_name()
        instance = _create_provider(name)

        if provider_name_override is None:
            _instance = instance

    return instance


def reset_provider():
    """Reset the singleton (for testing)."""
    global _instance
    with _instance_lock:
        _instance = None


# Primary credential signal for each non-telegram provider. Slack credentials
# only come from env vars; matrix/discord also accept a config.yaml block under
# the named primary-credential key.
_NON_TELEGRAM_ENV_CREDENTIAL = {
    "slack": "KOAN_SLACK_BOT_TOKEN",
    "matrix": "KOAN_MATRIX_ACCESS_TOKEN",
    "discord": "KOAN_DISCORD_BOT_TOKEN",
}

# Primary credential key inside each provider's messaging.<name> config block.
_NON_TELEGRAM_CONFIG_CREDENTIAL = {
    "matrix": "access_token",
    "discord": "bot_token",
}


def _telegram_credentials_present() -> bool:
    """True when Telegram is already set up (token + chat id present).

    Checks only the KOAN_TELEGRAM_TOKEN / KOAN_TELEGRAM_CHAT_ID env vars — the
    exact source the Telegram provider reads (see telegram.py configure()). The
    provider never consults a messaging.telegram config block, so neither does
    this guard.
    """
    token = os.environ.get("KOAN_TELEGRAM_TOKEN", "").strip()
    chat_id = os.environ.get("KOAN_TELEGRAM_CHAT_ID", "").strip()
    return bool(token and chat_id)


def _detect_provider_from_credentials(config: dict) -> str:
    """Infer a non-telegram provider from credentials already configured.

    When the user sets up e.g. Slack (KOAN_SLACK_* env vars) but leaves
    messaging.provider unset, the default would be telegram — producing a
    spurious "set telegram credentials" warning and a bridge that can't connect.
    If exactly one non-telegram provider is configured, honor it. Ambiguous
    setups (zero or multiple) return "" and fall back to the telegram default.

    Telegram already being configured is itself ambiguous: auto-detecting away
    from a working Telegram setup would silently swap providers. In that case we
    keep the telegram default and never auto-switch.
    """
    if _telegram_credentials_present():
        return ""
    found = {
        name for name, var in _NON_TELEGRAM_ENV_CREDENTIAL.items()
        if os.environ.get(var, "").strip()
    }
    messaging = config.get("messaging", {}) if isinstance(config, dict) else {}
    if isinstance(messaging, dict):
        for name, key in _NON_TELEGRAM_CONFIG_CREDENTIAL.items():
            block = messaging.get(name)
            if isinstance(block, dict) and str(block.get(key, "")).strip():
                found.add(name)
    return next(iter(found)) if len(found) == 1 else ""


def resolve_provider_name() -> str:
    """Resolve provider name from env var, config, or detected credentials."""
    name = os.environ.get("KOAN_MESSAGING_PROVIDER", "")
    if name:
        return name.lower().strip()

    config = {}
    try:
        from app.utils import load_config
        config = load_config()
        messaging = config.get("messaging", {})
        if isinstance(messaging, dict):
            name = messaging.get("provider", "")
            if name:
                return name.lower().strip()
    except (ImportError, AttributeError):
        pass
    except Exception as e:
        _write_error(f"Error reading messaging config: {e}")

    # No explicit provider chosen: if exactly one non-telegram platform is
    # already set up, use it rather than defaulting to telegram.
    detected = _detect_provider_from_credentials(config)
    if detected:
        _write_error(
            f"auto-detected messaging provider {detected!r} from credentials "
            "(no explicit messaging.provider set)"
        )
        return detected

    return "telegram"


def _ensure_providers_loaded():
    """Import provider modules to trigger registration.

    Short-circuits on the explicit ``_modules_loaded`` flag rather than
    ``bool(_providers)``.  The latter looks like the same check but is
    wrong: if anything imports e.g. ``app.messaging.telegram`` first
    (which the default startup path does), ``_providers`` becomes
    ``{"telegram": ...}`` without this loader ever running, and a later
    request for ``matrix`` / ``slack`` would skip the import loop and
    leave them unregistered.
    """
    global _modules_loaded
    if _modules_loaded:
        return
    with _load_lock:
        if _modules_loaded:
            return
        for module_name in _PROVIDER_MODULES:
            with contextlib.suppress(ImportError):
                __import__(module_name)
        _modules_loaded = True


# Backward-compatible private alias. ``resolve_provider_name`` is the public
# API (used by awake.py); the underscore-prefixed name is retained so existing
# callers/tests that imported it keep working.
_resolve_provider_name = resolve_provider_name


__all__ = [
    "DEFAULT_MAX_MESSAGE_SIZE",
    "MessagingProvider",
    "Update",
    "Message",
    "Reaction",
    "get_messaging_provider",
    "register_provider",
    "reset_provider",
    "resolve_provider_name",
]
