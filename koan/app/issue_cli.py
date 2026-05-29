"""Small provider-neutral issue tracker CLI for prompts and subprocesses."""

import argparse
import sys
from pathlib import Path

from app.issue_tracker import add_comment, create_issue, fetch_issue


def _read_body(path: str) -> str:
    p = Path(path)
    if not p.is_file():
        print(f"Error: body file not found: {path}", file=sys.stderr)
        raise SystemExit(1)
    return p.read_text(encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Koan issue tracker helper")
    sub = parser.add_subparsers(dest="command", required=True)

    fetch_p = sub.add_parser("fetch", help="Fetch an issue as plain text")
    fetch_p.add_argument("url")
    fetch_p.add_argument("--project", default="")
    fetch_p.add_argument("--project-path", default="")

    comment_p = sub.add_parser("comment", help="Post a comment")
    comment_p.add_argument("url")
    comment_p.add_argument("--body-file", required=True)
    comment_p.add_argument("--project", default="")
    comment_p.add_argument("--project-path", default="")

    create_p = sub.add_parser("create", help="Create an issue")
    create_p.add_argument("--project", required=True)
    create_p.add_argument("--project-path", default="")
    create_p.add_argument("--title", required=True)
    create_p.add_argument("--body-file", required=True)

    args = parser.parse_args(argv)

    try:
        if args.command == "fetch":
            content = fetch_issue(args.url, args.project, args.project_path)
            print(f"# {content.ref.label}: {content.title}\n")
            print(content.body)
            if content.comments:
                print("\n## Comments")
                for comment in content.comments:
                    author = comment.get("author", "unknown")
                    body = comment.get("body", "")
                    print(f"\n### {author}\n{body}")
            return 0

        if args.command == "comment":
            add_comment(
                args.url,
                _read_body(args.body_file),
                project_name=args.project,
                project_path=args.project_path,
            )
            return 0

        if args.command == "create":
            url = create_issue(
                args.project,
                args.project_path,
                args.title,
                _read_body(args.body_file),
            )
            print(url)
            return 0

    except SystemExit:
        raise
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
