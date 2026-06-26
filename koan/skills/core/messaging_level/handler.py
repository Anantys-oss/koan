"""Show or set the bridge verbosity level (debug / normal)."""
from app import messaging_level as ml


def handle(ctx):
    arg = (ctx.args or "").strip().lower()
    if not arg:
        current = ml.get_messaging_level()
        hint = "debug = full firehose, normal = quiet (default)"
        return (
            f"🔉 Messaging level: *{current}*\n{hint}\n"
            "Use /messaging_level debug|normal to change."
        )
    if arg not in ml.VALID_LEVELS:
        return (
            f"❌ Unknown level '{arg}'. "
            "Use /messaging_level debug or /messaging_level normal."
        )
    stored = ml.set_messaging_level(arg)
    if stored == "debug":
        return "🔊 Messaging level set to *debug* — full lifecycle narration restored."
    return (
        "🔉 Messaging level set to *normal* — quiet bridge "
        "(failures and command replies still shown)."
    )
