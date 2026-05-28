"""Shared notification polling configuration helpers."""

from typing import Any


def _section(config: dict, name: str) -> dict:
    value = config.get(name) or {}
    return value if isinstance(value, dict) else {}


def _coerce_int(value: Any, default: int, floor: int) -> int:
    try:
        return max(floor, int(value))
    except (ValueError, TypeError):
        return default


def get_notification_check_interval(
    config: dict,
    provider_key: str,
    default: int = 60,
    floor: int = 10,
) -> int:
    """Return the base polling interval for a notification provider.

    Provider-specific values override the shared ``notification_polling``
    section for backward compatibility.
    """
    provider = _section(config, provider_key)
    if "check_interval_seconds" in provider:
        return _coerce_int(provider.get("check_interval_seconds"), default, floor)

    shared = _section(config, "notification_polling")
    return _coerce_int(shared.get("check_interval_seconds", default), default, floor)


def get_notification_max_check_interval(
    config: dict,
    provider_key: str,
    default: int = 300,
    floor: int = 30,
) -> int:
    """Return the maximum idle backoff interval for a notification provider."""
    provider = _section(config, provider_key)
    if "max_check_interval_seconds" in provider:
        return _coerce_int(provider.get("max_check_interval_seconds"), default, floor)

    shared = _section(config, "notification_polling")
    return _coerce_int(
        shared.get("max_check_interval_seconds", default),
        default,
        floor,
    )
