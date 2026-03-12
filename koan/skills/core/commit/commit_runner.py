"""Kōan -- Commit runner.

Analyzes git changes and creates a conventional commit via Claude CLI.

Usage:
    python -m skills.core.commit.commit_runner --project-path <path> [--hint <message>]
"""

import argparse
import sys
from pathlib import Path

from app.prompts import load_skill_prompt


SKILL_DIR = Path(__file__).parent


def run_commit(project_path: str, hint: str = "") -> int:
    """Run the commit pipeline.

    Returns:
        0 on success, 1 on failure.
    """
    from app.claude_step import run_claude
    from app.cli_provider import build_full_command
    from app.config import get_model_config

    project_path = str(Path(project_path).resolve())

    # Build prompt
    prompt = load_skill_prompt(
        SKILL_DIR, "commit",
        HINT=hint or "(none — auto-generate from diff analysis)",
    )

    # Build CLI command
    models = get_model_config()
    cmd = build_full_command(
        prompt=prompt,
        allowed_tools=["Bash", "Read", "Glob", "Grep"],
        model=models.get("mission", ""),
        fallback=models.get("fallback", ""),
        max_turns=15,
    )

    # Run Claude
    result = run_claude(cmd, project_path, timeout=300)

    if result["success"]:
        print("Commit completed successfully.")
        return 0

    print(f"Commit failed: {result.get('error', 'unknown error')}")
    return 1


def main():
    parser = argparse.ArgumentParser(description="Kōan commit runner")
    parser.add_argument("--project-path", required=True, help="Path to project")
    parser.add_argument("--hint", default="", help="Commit message hint")
    args = parser.parse_args()

    sys.exit(run_commit(args.project_path, args.hint))


if __name__ == "__main__":
    main()
