"""Show resolved model config for the active CLI provider."""


def _cli_label(flavor: str, path: str, global_provider: str) -> str:
    """Per-role CLI annotation, or '' when the role uses the global default.

    Returns a short label to append to a model line when the role is routed to
    a custom CLI (a different provider flavor and/or a pinned binary path). The
    binary basename is preferred so ``claude:/root/.local/bin/claude-deep``
    surfaces as ``claude-deep``; a plain flavor override (no path) shows the
    flavor name. Empty string when flavor matches the global provider and no
    path is set — keeping the default setup's output unchanged.
    """
    if flavor == global_provider and not path:
        return ""
    if path:
        return path.rstrip("/").rsplit("/", 1)[-1]
    return flavor


def handle(ctx):
    try:
        from app.provider import get_provider_name
        provider_name = get_provider_name()
    except Exception as e:
        return f"Error resolving provider: {e}"

    try:
        from app.config import get_model_config
        models = get_model_config()
    except Exception as e:
        return f"Error loading model config: {e}"

    project_name = getattr(ctx, "project_name", "") or ""

    # Per-role CLI overrides (cli: config section). Best-effort — a config
    # problem here must never break the model listing.
    cli_roles: dict = {}
    cli_fallback = ("", "")
    try:
        from app.config import get_cli_config, get_cli_fallback
        cli_roles = get_cli_config(project_name)
        cli_fallback = get_cli_fallback(project_name)
    except Exception:
        cli_roles = {}
        cli_fallback = ("", "")

    lines = [f"Models for provider: {provider_name}"]
    slot_order = ["mission", "chat", "lightweight", "fallback", "review_mode", "reflect"]
    for slot in slot_order:
        value = models.get(slot, "")
        display = value if value else "(provider default)"
        if slot == "fallback":
            flavor, path = cli_fallback
        else:
            flavor, path = cli_roles.get(slot, (provider_name, ""))
        cli = _cli_label(flavor, path, provider_name)
        suffix = f"  [cli: {cli}]" if cli else ""
        lines.append(f"  {slot}: {display}{suffix}")

    return "\n".join(lines)
