"""ASCII art banners for Kōan startup sequences."""

import re
from pathlib import Path

# ANSI color codes
CYAN = "\033[36m"
BLUE = "\033[34m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"
WHITE = "\033[97m"
MAGENTA = "\033[35m"
GREEN = "\033[32m"
YELLOW = "\033[33m"

# ANSI escape pattern for visible-width calculation
_ANSI_RE = re.compile(r"\033\[[0-9;]*m")

# Layout constants
PADDING_GAP = 4  # Gap between logo and info columns

BANNERS_DIR = Path(__file__).parent


def _read_art(filename: str) -> str:
    """Read raw ASCII art from file."""
    art_file = BANNERS_DIR / filename
    if art_file.exists():
        return art_file.read_text()
    return ""


def _apply_replacements(line: str, replacements: dict, base_color: str) -> str:
    """Apply color replacements to a line with a base color.
    
    Args:
        line: The text line to colorize.
        replacements: Dict mapping text to replace -> replacement color.
        base_color: The base ANSI color for the entire line.
    
    Returns:
        Colorized line with base color and specific replacements.
    """
    for text, color in replacements.items():
        if text in line:
            line = line.replace(text, f"{color}{text}{RESET}{base_color}")
    return f"{base_color}{line}{RESET}"


def _colorize_art(art: str, replacements: dict, base_color: str) -> str:
    """Apply ANSI colors to ASCII art with given replacements and base color."""
    lines = art.split("\n")
    return "\n".join(_apply_replacements(line, replacements, base_color) for line in lines)


def colorize_agent(art: str) -> str:
    """Apply ANSI colors to the agent (run loop) banner."""
    return _colorize_art(art, {
        "◉": CYAN,
        "☢": YELLOW,
    }, f"{DIM}{BLUE}")


def colorize_bridge(art: str) -> str:
    """Apply ANSI colors to the bridge (awake) banner."""
    return _colorize_art(art, {
        "◇": CYAN,
        "◆": CYAN,
        "→": GREEN,
        "←": GREEN,
    }, f"{DIM}{MAGENTA}")


def _print_banner(art_file: str, colorizer: callable, version_info: str = "") -> None:
    """Print a banner with optional version info.
    
    Args:
        art_file: Name of the ASCII art file to read.
        colorizer: Function to apply colors to the art.
        version_info: Optional version string to display alongside the banner.
    """
    art = _read_art(art_file)
    if not art:
        return
    print()
    print(colorizer(art), end="")
    if version_info:
        print(f"  {DIM}{WHITE}{version_info}{RESET}")
    else:
        print()
    print()


def print_agent_banner(version_info: str = "") -> None:
    """Print the agent loop startup banner."""
    _print_banner("agent.txt", colorize_agent, version_info)


def print_bridge_banner(version_info: str = "") -> None:
    """Print the bridge (awake) startup banner."""
    _print_banner("bridge.txt", colorize_bridge, version_info)


def _visible_len(s: str) -> int:
    """Return the visible length of a string, ignoring ANSI escape codes."""
    return len(_ANSI_RE.sub("", s))


def colorize_startup(art: str) -> str:
    """Apply ANSI colors to the unified startup banner (Anantys mint theme)."""
    from app.banners.theme import MINT, MINT_DIM, _seq

    return _colorize_art(art, {
        "K Ō A N": _seq(MINT, bold=True),
        "cognitive sparring partner": f"{DIM}{WHITE}",
        "─────────────────────": _seq(MINT_DIM),
        "◉": _seq(MINT),
        "☢": YELLOW,
    }, f"{DIM}{BLUE}")


def colorize_hero(art: str) -> str:
    """Colorize the KŌAN hero banner (Anantys mint theme).

    The block-glyph wordmark and the katana blade glow mint, the guards (◈)
    are amber, and the tagline is dim white.
    """
    from app.banners.theme import MINT, _seq

    return _colorize_art(art, {
        "the agent proposes, the human decides": f"{DIM}{WHITE}",
        "◈": YELLOW,
    }, _seq(MINT, bold=True))


def print_hero_banner(system_info: dict = None) -> None:
    """Print the KŌAN hero banner with system info listed beneath it.

    Falls back to the compact two-column banner if the hero art is missing.
    """
    art = _read_art("koan_hero.txt")
    if not art:
        print_startup_banner(system_info)
        return

    print()
    print(colorize_hero(art.rstrip("\n")))
    print()
    for line in _format_info_lines(system_info) if system_info else []:
        # _format_info_lines already colors the value; just indent.
        print(f"   {line}")
    if system_info:
        print()


def _format_info_lines(system_info: dict) -> list:
    """Format system_info dict into display lines with labels.
    
    Args:
        system_info: Dictionary with system information keys.
    
    Returns:
        List of formatted info lines with ANSI colors.
    """
    label_map = [
        ("provider", "Provider"),
        ("projects", "Projects"),
        ("skills", "Skills"),
        ("soul", "Soul"),
        ("messaging", "Messaging"),
    ]
    from app.banners.theme import MINT, _seq

    max_value_len = 50
    mint = _seq(MINT)
    lines = []
    for key, label in label_map:
        if key in system_info:
            value = system_info[key]
            if len(value) > max_value_len:
                value = value[:max_value_len - 1] + "…"
            lines.append(f"{DIM}{WHITE}{label}: {RESET}{mint}{value}{RESET}")
    return lines


def print_startup_banner(system_info: dict = None) -> None:
    """Print the unified startup banner with optional system info.

    Renders the logo on the left and system information on the right
    in a two-column layout.
    """
    art = _read_art("startup.txt")
    if not art:
        return

    colored_art = colorize_startup(art)
    art_lines = colored_art.split("\n")
    # Remove trailing empty lines for cleaner output
    while art_lines and not art_lines[-1].strip():
        art_lines.pop()

    # Determine visible width of the art for alignment
    max_art_width = max((_visible_len(l) for l in art_lines), default=0)
    pad_width = max_art_width + PADDING_GAP

    info_lines = _format_info_lines(system_info) if system_info else []

    print()
    for i, art_line in enumerate(art_lines):
        visible_w = _visible_len(art_line)
        padding = " " * (pad_width - visible_w)
        if i < len(info_lines):
            print(f"{art_line}{padding}{info_lines[i]}")
        else:
            print(art_line)

    # Print remaining info lines below the art if more info than art lines
    for j in range(len(art_lines), len(info_lines)):
        print(f"{' ' * pad_width}{info_lines[j]}")

    print()
