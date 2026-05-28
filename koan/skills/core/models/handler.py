"""Show resolved model config for the active CLI provider."""


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

    lines = [f"Models for provider: {provider_name}"]
    slot_order = ["mission", "chat", "lightweight", "fallback", "review_mode", "reflect"]
    for slot in slot_order:
        value = models.get(slot, "")
        display = value if value else "(provider default)"
        lines.append(f"  {slot}: {display}")

    return "\n".join(lines)
