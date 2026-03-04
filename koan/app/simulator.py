"""AI Governor Simulator — Generate synthetic events for pipeline testing.

Creates SimulatedEvents (WatcherEvent + source=simulation), injects them
into the existing pipeline (journal → check_and_notify → advisor), and
supports replay from historical journal data.

Usage via CLI:
    governor simulate commit --author dany-yourart --repo emailfactory --message "test"
    governor simulate credential --repo fetching --file .env
    governor simulate replay --date 2026-03-03
    governor simulate --dry-run commit ...
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from app.utils import KOAN_ROOT, INSTANCE_DIR, load_config
from app.watcher.normalizer import WatcherEvent, generate_event_id
SCENARIOS_FILE = INSTANCE_DIR / "simulate" / "scenarios.yaml"


def _load_scenarios() -> dict:
    if not SCENARIOS_FILE.exists():
        return {}
    with open(SCENARIOS_FILE) as f:
        data = yaml.safe_load(f) or {}
    return data.get("scenarios", {})


def build_event(scenario_key: str, overrides: Optional[dict] = None) -> WatcherEvent:
    """Build a WatcherEvent from a scenario template with CLI overrides."""
    scenarios = _load_scenarios()
    template = {}
    if scenario_key in scenarios:
        template = dict(scenarios[scenario_key].get("event_template", {}))

    if overrides:
        template.update(overrides)

    # Auto-fill defaults
    timestamp = template.get("timestamp", "auto")
    if timestamp == "auto" or not timestamp:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return WatcherEvent(
        id=generate_event_id(),
        platform=template.get("platform", "github"),
        type=template.get("type", "push"),
        repo=template.get("repo", "unknown"),
        author=template.get("author", "simulation"),
        author_type=template.get("author_type", "citizen"),
        author_name=template.get("author_name"),
        branch=template.get("branch", "main"),
        summary=template.get("summary", template.get("message", "simulated event")),
        commits_count=template.get("commits_count", 1),
        forced=template.get("forced", False),
        timestamp=timestamp,
        delivery_id=f"sim-{generate_event_id()}",
        raw_event_type="push",
    )


def inject_event(event: WatcherEvent, dry_run: bool = False) -> str:
    """Inject a simulated event into the watcher pipeline.

    1. Append to journal (tagged source=simulation)
    2. Run check_and_notify (detection pipeline)
    3. Return summary of what happened

    If dry_run=True, still runs pipeline but skips notifications.
    """
    from app.watcher.journal import append_event

    # Tag the event as simulation in the journal
    event_dict = event.to_dict()
    event_dict["source"] = "simulation"

    # Write to journal
    append_event(INSTANCE_DIR, event)

    lines = [
        f"{'[DRY RUN] ' if dry_run else ''}Événement simulé injecté dans le pipeline",
        f"  ID: {event.id}",
        f"  Type: {event.type} ({event.platform})",
        f"  Repo: {event.repo}",
        f"  Auteur: {event.author} ({event.author_type})",
        f"  Message: {event.summary[:80]}",
        "",
    ]

    # Run check_and_notify pipeline
    try:
        if not dry_run:
            from app.watcher.helpers import check_and_notify
            check_and_notify(INSTANCE_DIR, event)
            lines.append("Pipeline check_and_notify exécuté.")
        else:
            lines.append("Pipeline check_and_notify ignoré (dry-run).")
    except Exception as e:
        lines.append(f"Erreur pipeline : {e}")

    # Run advisor analysis for citizen pushes
    if event.author_type == "citizen" and event.type == "push":
        try:
            config = load_config()
            advisor_config = config.get("advisor", {})
            if advisor_config.get("enabled") and advisor_config.get("scan_on_event"):
                if not dry_run:
                    from app.advisor.analyzer import analyze_commit
                    analyze_commit(event_dict, advisor_config)
                    lines.append("Advisor analyze_commit exécuté.")
                else:
                    lines.append("Advisor analyze_commit ignoré (dry-run).")
            else:
                lines.append("Advisor désactivé ou scan_on_event=false.")
        except ImportError:
            lines.append("Module advisor non disponible.")
        except Exception as e:
            lines.append(f"Erreur advisor : {e}")

    return "\n".join(lines)


def simulate_commit(author: str, repo: str, message: str,
                    files: Optional[str] = None, dry_run: bool = False) -> str:
    """Simulate a citizen commit."""
    overrides = {
        "author": author,
        "repo": repo,
        "summary": message,
        "type": "push",
        "platform": "github",
        "author_type": "citizen",
    }
    event = build_event("commit_citizen", overrides)
    return inject_event(event, dry_run=dry_run)


def simulate_credential(repo: str, file: str, dry_run: bool = False) -> str:
    """Simulate a credential leak commit."""
    overrides = {
        "repo": repo,
        "summary": f"fix: config — credential détectée dans {file}",
        "type": "push",
    }
    event = build_event("credential_leak", overrides)
    return inject_event(event, dry_run=dry_run)


def simulate_replay(date_str: str, dry_run: bool = False) -> str:
    """Replay events from a specific date in the journal."""
    events_dir = INSTANCE_DIR / "watcher" / "events"
    journal_file = events_dir / f"{date_str}.jsonl"

    if not journal_file.exists():
        available = sorted(events_dir.glob("*.jsonl")) if events_dir.exists() else []
        if available:
            dates = [f.stem for f in available[-5:]]
            return f"Pas de journal pour {date_str}.\nDates disponibles : {', '.join(dates)}"
        return f"Pas de journal pour {date_str}. Aucun fichier d'événements trouvé."

    events = []
    with open(journal_file) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    if not events:
        return f"Journal {date_str} vide."

    lines = [
        f"{'[DRY RUN] ' if dry_run else ''}Replay du {date_str} — {len(events)} événements",
        "",
    ]

    replayed = 0
    errors = 0
    for evt_dict in events:
        try:
            event = WatcherEvent(
                id=generate_event_id(),
                platform=evt_dict.get("platform", "github"),
                type=evt_dict.get("type", "push"),
                repo=evt_dict.get("repo", "?"),
                author=evt_dict.get("author", "?"),
                author_type=evt_dict.get("author_type", "unknown"),
                author_name=evt_dict.get("author_name"),
                branch=evt_dict.get("branch"),
                summary=evt_dict.get("summary", ""),
                commits_count=evt_dict.get("commits_count"),
                forced=evt_dict.get("forced", False),
                timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                delivery_id=f"replay-{date_str}",
                raw_event_type=evt_dict.get("raw_event_type", ""),
            )
            inject_event(event, dry_run=dry_run)
            replayed += 1
        except Exception as e:
            errors += 1
            lines.append(f"  Erreur event {evt_dict.get('id', '?')}: {e}")

    lines.append(f"Résultat: {replayed} rejoués, {errors} erreurs")
    return "\n".join(lines)


# ── Demo scenarios (spec 009) ──────────────────────────────────────

DEMO_SCENARIOS = [
    {
        "name": "Citizen newsletter template",
        "event_template": {
            "type": "push", "platform": "github",
            "repo": "emailfactory",
            "author": "dany-yourart", "author_type": "citizen",
            "author_name": "Dany",
            "summary": "feat: template newsletter avec personnalisation dynamique des variables",
            "branch": "main", "commits_count": 1,
        },
    },
    {
        "name": "Citizen ticket classification",
        "event_template": {
            "type": "push", "platform": "github",
            "repo": "Tickets",
            "author": "benoitboutoille", "author_type": "citizen",
            "author_name": "Benoit",
            "summary": "feat: classification automatique des tickets avec embeddings voyage-3",
            "branch": "main", "commits_count": 1,
        },
    },
    {
        "name": "Credential leak detection",
        "event_template": {
            "type": "push", "platform": "github",
            "repo": "fetching",
            "author": "art236", "author_type": "citizen",
            "author_name": "Art236",
            "summary": "fix: ajout config Railway — sk-proj-abc123def456 dans .env",
            "branch": "main", "commits_count": 1,
        },
    },
    {
        "name": "Tech PR merged",
        "event_template": {
            "type": "push", "platform": "github",
            "repo": "emailfactory",
            "author": "JBocage", "author_type": "tech",
            "author_name": "Jean-Baptiste",
            "summary": "PR merged: fix retry SMTP avec backoff exponentiel",
            "branch": "main", "commits_count": 3,
        },
    },
    {
        "name": "Citizen taste vectors B2B",
        "event_template": {
            "type": "push", "platform": "github",
            "repo": "artmajeur-personalization",
            "author": "DPGYA", "author_type": "citizen",
            "author_name": "DPGYA",
            "summary": "feat: taste vectors B2B avec BigQuery pipeline batch quotidien",
            "branch": "main", "commits_count": 1,
        },
    },
    {
        "name": "New repo created",
        "event_template": {
            "type": "repo", "platform": "github",
            "repo": "artmajeur-chatbot",
            "author": "paolalevy", "author_type": "citizen",
            "author_name": "Paola",
            "summary": "created repository artmajeur-chatbot",
            "branch": "main", "commits_count": 0,
        },
    },
]


def simulate_demo(dry_run: bool = False, notify: bool = False) -> str:
    """Run the full demo: 6 scenarios + daily report.

    Injects each scenario into the pipeline sequentially, then generates
    the daily report.
    """
    lines = [
        f"{'[DRY RUN] ' if dry_run else ''}Démo E2E — 6 scénarios + rapport journalier",
        "",
    ]

    for i, scenario in enumerate(DEMO_SCENARIOS, 1):
        template = dict(scenario["event_template"])
        event = build_event("__demo__", template)

        lines.append(f"── Scénario {i}/6 : {scenario['name']} ──")
        result = inject_event(event, dry_run=dry_run)
        lines.append(result)
        lines.append("")

    lines.append("── Rapport journalier ──")
    try:
        from app.governor_daily_report import send_daily_report, generate_daily_report
        if dry_run:
            narrative = generate_daily_report()
            lines.append(narrative)
        else:
            narrative = send_daily_report(notify=notify)
            lines.append(narrative)
            if notify:
                lines.append("→ Rapport envoyé sur Google Chat")
    except ImportError:
        lines.append("Module governor_daily_report non disponible.")
    except Exception as e:
        lines.append(f"Erreur rapport : {e}")

    lines.append("")
    lines.append(f"Démo terminée — {len(DEMO_SCENARIOS)} scénarios traités.")

    return "\n".join(lines)


# ── CLI entry point ─────────────────────────────────────────────────

def handle_simulate(action: str, extra_args: str, flags) -> int:
    """Handle 'governor simulate' commands. Called from governor_cli.main()."""
    import shlex

    dry_run = flags.dry_run

    if not action or action == "--help":
        print(
            "Usage: governor simulate <action> [options]\n\n"
            "Actions:\n"
            "  commit       Simuler un commit citizen\n"
            "  credential   Simuler une détection de credential\n"
            "  replay       Rejouer les événements d'une date\n"
            "  demo         Démo E2E : 6 scénarios + rapport journalier\n\n"
            "Options commit:\n"
            "  --author <login>    Login GitHub de l'auteur simulé (requis)\n"
            "  --repo <repo>       Nom du repo (requis)\n"
            "  --message <msg>     Message du commit (requis)\n"
            "  --files <f1,f2>     Fichiers modifiés (optionnel)\n\n"
            "Options credential:\n"
            "  --repo <repo>       Nom du repo (requis)\n"
            "  --file <path>       Fichier contenant la credential (requis)\n\n"
            "Options replay:\n"
            "  --date <YYYY-MM-DD> Date à rejouer (requis)\n\n"
            "Flags globaux:\n"
            "  --dry-run           Exécuter sans notifications"
        )
        return 0

    # Parse extra_args into key-value pairs (supports multi-word values)
    try:
        parts = shlex.split(extra_args) if extra_args else []
    except ValueError:
        parts = extra_args.split() if extra_args else []

    opts = {}
    i = 0
    while i < len(parts):
        if parts[i].startswith("--") and i + 1 < len(parts):
            key = parts[i][2:]
            # Collect all tokens until next --flag as the value
            value_parts = []
            i += 1
            while i < len(parts) and not parts[i].startswith("--"):
                value_parts.append(parts[i])
                i += 1
            opts[key] = " ".join(value_parts)
        else:
            i += 1

    if action == "commit":
        author = opts.get("author")
        repo = opts.get("repo")
        message = opts.get("message")
        if not all([author, repo, message]):
            print("Erreur: --author, --repo et --message sont requis.", file=sys.stderr)
            print("Usage: governor simulate commit --author <login> --repo <repo> --message <msg>")
            return 1
        result = simulate_commit(author, repo, message, opts.get("files"), dry_run=dry_run)

    elif action == "credential":
        repo = opts.get("repo")
        file = opts.get("file")
        if not all([repo, file]):
            print("Erreur: --repo et --file sont requis.", file=sys.stderr)
            print("Usage: governor simulate credential --repo <repo> --file <path>")
            return 1
        result = simulate_credential(repo, file, dry_run=dry_run)

    elif action == "replay":
        date = opts.get("date")
        if not date:
            print("Erreur: --date est requis.", file=sys.stderr)
            print("Usage: governor simulate replay --date YYYY-MM-DD")
            return 1
        result = simulate_replay(date, dry_run=dry_run)

    elif action == "demo":
        result = simulate_demo(dry_run=dry_run, notify=flags.notify)

    else:
        print(f"Action inconnue : '{action}'. Actions : commit, credential, replay, demo")
        return 1

    # Output
    if flags.output_json:
        import json as json_mod
        print(json_mod.dumps({"action": action, "result": result, "dry_run": dry_run},
                             ensure_ascii=False, indent=2))
    else:
        print(result)

    # Notify if requested
    if flags.notify and not dry_run:
        from app.governor_cli import send_to_gchat
        send_to_gchat(f"Simulation {action}", result, thread_key="simulate")

    return 0
