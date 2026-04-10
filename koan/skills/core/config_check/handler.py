"""Kōan /config_check skill — report drift between instance/config.yaml and the template."""

from pathlib import Path

from app.config_validator import detect_config_drift, find_extra_config_keys
from app.utils import load_config


def handle(ctx):
    """Compare the user's config.yaml against instance.example/config.yaml.

    Reports:
      - missing keys: in template, absent from user config (new features)
      - extra keys: in user config, absent from template (deprecated or typos)
    """
    koan_root = str(ctx.koan_root)
    instance_dir = Path(ctx.instance_dir)
    config_path = instance_dir / "config.yaml"
    template_path = Path(koan_root) / "instance.example" / "config.yaml"

    if not template_path.exists():
        return "❌ Template not found: instance.example/config.yaml"
    if not config_path.exists():
        return f"❌ config.yaml not found at {config_path}"

    try:
        user_config = load_config()
    except Exception as e:
        return f"❌ Could not load config.yaml: {e}"

    missing = detect_config_drift(koan_root, user_config=user_config)
    extra = find_extra_config_keys(koan_root, user_config=user_config)

    if not missing and not extra:
        return "✅ config.yaml is in sync with instance.example/config.yaml"

    lines = ["🔧 Config check"]

    if missing:
        lines.append("")
        lines.append(f"▸ Missing from your config ({len(missing)}):")
        for key in missing:
            lines.append(f"  ➕ {key}")
        lines.append("     ↳ New template keys — see instance.example/config.yaml")

    if extra:
        lines.append("")
        lines.append(f"▸ Extra in your config ({len(extra)}):")
        for key in extra:
            lines.append(f"  ⚠️ {key}")
        lines.append("     ↳ May be deprecated or typos")

    return "\n".join(lines)
