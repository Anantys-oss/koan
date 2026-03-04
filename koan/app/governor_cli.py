"""AI Governor CLI — Direct skill execution without Telegram bridge.

Bypasses awake.py and the messaging bridge. Loads the SkillRegistry,
constructs a SkillContext, and calls execute_skill() directly.

Two handler return patterns are supported:
  1. Return string (governor.watcher, governor.advisor, etc.)
  2. Side-effect outbox (governor.status) — writes to instance/outbox.md
"""

import argparse
import difflib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from app.skills import SkillContext, build_registry, execute_skill
from app.utils import KOAN_ROOT, INSTANCE_DIR, load_config


class CLIContext(SkillContext):
    """SkillContext subclass that also supports dict-style .get() access.

    Some handlers use ctx.args (attribute), others use ctx.get("args") (dict-style).
    This hybrid supports both patterns without modifying existing handlers.
    """

    def get(self, key: str, default=None):
        return getattr(self, key, default)

VERSION = "1.0.0"
OUTBOX_FILE = INSTANCE_DIR / "outbox.md"

# Exit codes per cli-interface.yaml contract
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_SKILL_NOT_FOUND = 2
EXIT_DOCKER_DOWN = 3
EXIT_CONFIG_MISSING = 4

# ANSI colors (disabled when piped or --json)
_USE_COLOR = sys.stdout.isatty()

def _c(code: str, text: str) -> str:
    if not _USE_COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"

def _green(t: str) -> str: return _c("32", t)
def _yellow(t: str) -> str: return _c("33", t)
def _red(t: str) -> str: return _c("31", t)
def _bold(t: str) -> str: return _c("1", t)
def _dim(t: str) -> str: return _c("2", t)


# ── Registry ────────────────────────────────────────────────────────

_registry = None

def _get_registry():
    global _registry
    if _registry is None:
        extra_dirs = []
        instance_skills = INSTANCE_DIR / "skills"
        if instance_skills.is_dir():
            extra_dirs.append(instance_skills)
        _registry = build_registry(extra_dirs)
    return _registry


# ── Skill dispatch ──────────────────────────────────────────────────

# Maps CLI commands to skill handler directory names
# Format: command -> (handler_dir, prepend_command_to_args)
SKILL_MAP = {
    "status":   ("governor.status", False),
    "watcher":  ("governor.watcher", False),
    "advisor":  ("governor.advisor", False),
    "autonomy": ("governor.autonomy", False),
    "rollout":  ("governor.rollout", False),
    "offboard": ("governor.offboard", False),
    "budget":   ("governor/budget", False),
    "keys":     ("governor/keys", False),
    "vault":    ("governor.vault", False),
    "env":      ("governor.env", False),
    "scan":     ("governor.scan", False),
    "report":   ("governor.report", False),
}


def _find_skill(command: str):
    """Find a skill by command name.

    Prefers direct handler.py path (most reliable for governor skills),
    then falls back to registry lookup.
    """
    entry = SKILL_MAP.get(command)
    if entry is None:
        return None, None

    handler_dir, prepend = entry

    # Strategy 1: direct handler.py path (reliable — ignores SKILL.md inconsistencies)
    handler_path = INSTANCE_DIR / "skills" / handler_dir / "handler.py"
    if handler_path.exists():
        from app.skills import Skill
        skill = Skill(
            name=command,
            scope="governor",
            handler_path=handler_path,
            skill_dir=handler_path.parent,
        )
        return skill, prepend

    # Strategy 2: registry lookup (for skills with proper SKILL.md)
    registry = _get_registry()
    qualified_name = handler_dir.replace("/", ".")
    skill = registry.get_by_qualified_name(qualified_name)
    if skill and skill.has_handler():
        return skill, prepend

    return None, None


def dispatch_skill(command: str, action: str, extra_args: str,
                   flags: argparse.Namespace) -> tuple[int, str]:
    """Dispatch a CLI command to the appropriate skill handler.

    Returns (exit_code, result_text).
    """
    if command not in SKILL_MAP:
        return EXIT_SKILL_NOT_FOUND, _suggest_command(command)

    skill, prepend = _find_skill(command)
    if skill is None:
        return EXIT_SKILL_NOT_FOUND, f"Skill pour '{command}' non trouvé."

    # Build args string
    if prepend:
        args_str = f"{command} {action} {extra_args}".strip()
    else:
        args_str = f"{action} {extra_args}".strip()

    # Capture outbox state before execution (for outbox-pattern handlers)
    outbox_before = _read_outbox()

    ctx = CLIContext(
        koan_root=KOAN_ROOT,
        instance_dir=INSTANCE_DIR,
        command_name=command,
        args=args_str,
    )

    start = time.monotonic()
    result = execute_skill(skill, ctx)
    elapsed = time.monotonic() - start

    # Handle outbox pattern: if result is None, check outbox for new content
    if result is None:
        outbox_after = _read_outbox()
        if len(outbox_after) > len(outbox_before):
            result = outbox_after[len(outbox_before):].strip()
            # Clean up appended content
            _write_outbox(outbox_before)

    if result is None:
        result = f"Commande exécutée (pas de sortie). [{elapsed:.1f}s]"

    if flags.verbose:
        result += f"\n{_dim(f'[{elapsed:.1f}s | skill={qualified} | args={args_str!r}]')}"

    return EXIT_OK, result


def _read_outbox() -> str:
    try:
        return OUTBOX_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _write_outbox(content: str):
    OUTBOX_FILE.write_text(content, encoding="utf-8")


def _suggest_command(command: str) -> str:
    all_commands = list(SKILL_MAP.keys()) + ["simulate", "tunnel", "help"]
    matches = difflib.get_close_matches(command, all_commands, n=3, cutoff=0.5)
    msg = f"Commande inconnue : '{command}'"
    if matches:
        suggestions = ", ".join(matches)
        msg += f"\nCommandes similaires : {suggestions}"
    msg += f"\nTapez 'governor help' pour la liste complète."
    return msg


# ── Help system ────────────────────────────────────────────────────

HELP_COMMANDS = {
    "status": {
        "desc": "Health check unifié de tous les modules",
        "usage": "governor status [action]",
        "actions": {
            "(défaut)": "Affiche rapport santé unifié (uptime, latence, modules, circuit breakers)",
            "report [période]": "Génère rapport périodique (format: YYYY-MM-DD:YYYY-MM-DD, défaut: 7 derniers jours)",
        },
        "examples": [
            "governor status",
            "governor status report",
            "governor status report 2026-02-25:2026-03-04",
        ],
    },
    "watcher": {
        "desc": "Surveillance des repos GitHub et GitLab",
        "usage": "governor watcher [action] [options]",
        "actions": {
            "status": "État watcher : webhooks GitHub, scans GitLab, repos actifs, événements du jour (défaut)",
            "log [flags]": "Query le journal d'audit unifié avec filtres",
            "repos [flags]": "Liste des repos surveillés avec filtres",
            "scan": "Force un scan GitLab immédiat (polling des projets du groupe)",
            "catch-up": "Rattrape les deliveries webhook GitHub manquées (redelivery API)",
            "alerts": "Liste les alertes non acquittées (auteurs inconnus, force-push, etc.)",
            "register <login> <platform> <type>": "Ajoute un utilisateur au registre watcher",
        },
        "flags": {
            "log": [
                ("--author <login>", "Filtrer par auteur (login GitHub/GitLab)"),
                ("--repo <name>", "Filtrer par nom de dépôt"),
                ("--type <type>", "Filtrer par type d'événement (push, pr, mr, issue, etc.)"),
                ("--platform <github|gitlab>", "Filtrer par plateforme"),
                ("--days <N>", "Nombre de jours à remonter (défaut: 30)"),
                ("--limit <N>", "Nombre max d'événements retournés (défaut: 20)"),
            ],
            "repos": [
                ("--platform <github|gitlab>", "Filtrer par plateforme"),
                ("--status <active|inactive>", "Filtrer par statut"),
            ],
            "register": [
                ("--name <prénom>", "Prénom/nom associé au login"),
                ("<platform>", "github ou gitlab"),
                ("<type>", "citizen, tech ou governor"),
            ],
        },
        "examples": [
            "governor watcher status",
            "governor watcher log --author vbLBB --days 7",
            "governor watcher log --platform gitlab --type push",
            "governor watcher repos --platform github",
            "governor watcher scan",
            "governor watcher catch-up",
            "governor watcher alerts",
            "governor watcher register dany-yourart github citizen --name Dany",
        ],
    },
    "advisor": {
        "desc": "Détection de duplications cross-plateforme et recommandations",
        "usage": "governor advisor [action] [options]",
        "actions": {
            "status": "État advisor : repos indexés, fichiers, catalogue MCP, détections 7j (défaut)",
            "scan [--full]": "Scan repos pour indexation sémantique (incrémental par défaut)",
            "analyze [--days N]": "Analyse commits citizens récents pour détecter duplications",
            "report [--days N]": "Génère rapport duplications détaillé pour gouverneurs",
            "catalog [search <query>]": "Affiche ou recherche dans le catalogue MCP ArtMajeur (19 ressources)",
            "repos": "Cartographie des repos indexés (GitHub + GitLab) avec stats",
            "feedback <id> <verdict>": "Marque une détection avec un verdict",
        },
        "flags": {
            "scan": [
                ("--full", "Scan complet (re-indexe tout). Sans ce flag: incrémental"),
            ],
            "analyze": [
                ("--days <N>", "Période d'analyse en jours (défaut: 7)"),
            ],
            "report": [
                ("--days <N>", "Période du rapport en jours (défaut: 30)"),
            ],
            "feedback": [
                ("<id>", "Identifiant de la détection (ex: DET-042)"),
                ("<verdict>", "relevant | false-positive | ignore | acknowledged"),
                ("--notes <texte>", "Notes/commentaire libre sur la détection"),
            ],
        },
        "examples": [
            "governor advisor status",
            "governor advisor scan",
            "governor advisor scan --full",
            "governor advisor analyze --days 14",
            "governor advisor report",
            "governor advisor catalog",
            "governor advisor catalog search email",
            "governor advisor repos",
            "governor advisor feedback DET-042 relevant --notes \"Duplication confirmée\"",
            "governor advisor feedback DET-043 false-positive",
        ],
    },
    "report": {
        "desc": "Rapports journaliers et hebdomadaires agrégés",
        "usage": "governor report [action] [options]",
        "actions": {
            "daily [flags]": "Rapport journalier agrégé watcher + advisor + budget (défaut: aujourd'hui)",
            "weekly [flags]": "Résumé hebdomadaire agrégé sur 7 jours",
            "status": "Informations sur le dernier rapport généré",
        },
        "flags": {
            "daily": [
                ("--date <YYYY-MM-DD>", "Date du rapport (défaut: aujourd'hui)"),
                ("--notify", "Envoie le rapport sur Google Chat"),
            ],
            "weekly": [
                ("--notify", "Envoie le résumé sur Google Chat"),
            ],
        },
        "examples": [
            "governor report daily",
            "governor report daily --date 2026-03-03",
            "governor report daily --notify",
            "governor report weekly --notify",
            "governor report status",
        ],
    },
    "budget": {
        "desc": "Gestion des budgets API LLM par citizen",
        "usage": "governor budget [action] [options]",
        "actions": {
            "status [user_id]": "Affiche budget global ou détail d'un citizen (défaut)",
            "set <user_id> <montant>": "Définit le budget mensuel d'un citizen (en EUR)",
            "request <montant> <justification>": "Citizen : demande une extension de budget (crée REQ-XXX)",
            "approve <REQ-ID> [message]": "Governor : approuve une demande d'extension",
            "reject <REQ-ID> [message]": "Governor : rejette une demande d'extension",
        },
        "examples": [
            "governor budget status",
            "governor budget status vbLBB",
            "governor budget set vbLBB 50",
            "governor budget request 30 \"Tests embeddings lourds ce mois\"",
            "governor budget approve REQ-001",
            "governor budget approve REQ-001 \"OK pour ce mois uniquement\"",
            "governor budget reject REQ-002 \"Budget déjà dépassé\"",
        ],
    },
    "keys": {
        "desc": "Gestion des clés virtuelles LiteLLM",
        "usage": "governor keys [action] [options]",
        "actions": {
            "list [user_id]": "Liste les clés virtuelles actives (filtrée par user optionnel) (défaut)",
            "create <user_id> [alias]": "Crée une nouvelle clé virtuelle (alias défaut: {user}-key)",
            "revoke <alias>": "Révoque une clé par son alias",
        },
        "examples": [
            "governor keys list",
            "governor keys list vbLBB",
            "governor keys create vbLBB laurence-key",
            "governor keys revoke laurence-key",
        ],
    },
    "vault": {
        "desc": "Gestion des credentials via Google Secret Manager",
        "usage": "governor vault [action] [options]",
        "actions": {
            "list": "Liste tous les secrets gérés dans GSM (défaut)",
            "store <secret_id>": "Initie la création d'un nouveau secret (collecte metadata + valeur)",
            "store-confirm <data>": "Finalise la création (utilisé en interne après store)",
            "rotate <secret_id> [new_value]": "Rotation : nouvelle version, désactive l'ancienne",
            "revoke <secret_id>": "Révoque un secret : désactive toutes les versions",
            "grant <citizen> <project>": "Accorde l'accès d'un citizen aux credentials d'un projet",
            "ungrant <citizen> <project>": "Révoque l'accès d'un citizen aux credentials d'un projet",
            "audit [citizen]": "Affiche l'audit GSM (Cloud Logging, optionnel: filtré par citizen)",
        },
        "examples": [
            "governor vault list",
            "governor vault store my-api-key",
            "governor vault rotate my-api-key",
            "governor vault revoke old-secret",
            "governor vault grant vbLBB emailfactory",
            "governor vault ungrant vbLBB emailfactory",
            "governor vault audit",
            "governor vault audit vbLBB",
        ],
    },
    "env": {
        "desc": "Injection temporaire de variables d'environnement depuis le vault",
        "usage": "governor env [action] [options]",
        "actions": {
            "inject <project>": "Génère un .env temporaire pour un projet (TTL 24h par défaut)",
            "status": "Liste les injections actives du caller (défaut)",
            "revoke <citizen> <project>": "Governor : révoque une injection active",
        },
        "examples": [
            "governor env status",
            "governor env inject emailfactory",
            "governor env revoke vbLBB emailfactory",
        ],
    },
    "scan": {
        "desc": "Détection de fuites de credentials dans les repos",
        "usage": "governor scan [action] [options]",
        "actions": {
            "repo <repo_name>": "Scan un repo spécifique (detect-secrets, compare baseline)",
            "all": "Scan tous les repos YourArtOfficial, résumé par sévérité",
            "baseline update": "Met à jour la baseline (findings actuels marqués comme connus)",
        },
        "examples": [
            "governor scan repo koan-fork",
            "governor scan repo emailfactory",
            "governor scan all",
            "governor scan baseline update",
        ],
    },
    "autonomy": {
        "desc": "Gestion des niveaux d'autonomie des modules",
        "usage": "governor autonomy [action] [options]",
        "actions": {
            "get [module]": "Affiche les niveaux d'autonomie actuels (défaut)",
            "set <module> <level>": "Change le niveau d'autonomie d'un module",
        },
        "flags": {
            "set": [
                ("<module>", "budget_controller | credential_vault | watcher | advisor"),
                ("<level>", "watch (surveillance seule) | notify (notifications actives) | supervise (validation humaine requise)"),
            ],
        },
        "examples": [
            "governor autonomy get",
            "governor autonomy get watcher",
            "governor autonomy set watcher notify",
            "governor autonomy set advisor supervise",
            "governor autonomy set budget_controller watch",
        ],
    },
    "rollout": {
        "desc": "Gestion du déploiement progressif par groupes",
        "usage": "governor rollout [action] [options]",
        "actions": {
            "list": "Affiche les groupes de rollout et leurs membres (défaut)",
            "activate <group>": "Active un groupe de rollout (commence à recevoir les notifications)",
            "add <group> <login>": "Ajoute un membre à un groupe",
            "remove <group> <login>": "Retire un membre d'un groupe",
        },
        "examples": [
            "governor rollout list",
            "governor rollout activate beta",
            "governor rollout add beta vbLBB",
            "governor rollout remove beta vbLBB",
        ],
    },
    "offboard": {
        "desc": "Offboarding complet d'un citizen",
        "usage": "governor offboard remove <login>",
        "actions": {
            "remove <login>": "Offboarde un citizen : révoque credentials, bloque clés LiteLLM, marque inactif dans le registre, retire des groupes rollout",
        },
        "examples": [
            "governor offboard remove ancien-citizen",
        ],
    },
    "simulate": {
        "desc": "Simulation d'événements pour tester le pipeline end-to-end",
        "usage": "governor simulate <action> [options]",
        "actions": {
            "commit": "Simule un commit citizen → injecte dans pipeline watcher → advisor",
            "credential": "Simule une détection de fuite de credential",
            "replay": "Rejoue les événements du journal d'une date spécifique",
            "demo": "Démo E2E complète : 6 scénarios enchaînés + rapport journalier",
        },
        "flags": {
            "commit": [
                ("--author <login>", "Login du citizen auteur (requis)"),
                ("--repo <repo>", "Nom du dépôt (requis)"),
                ("--message <msg>", "Message de commit (requis)"),
                ("--files <f1,f2,...>", "Liste de fichiers modifiés (optionnel)"),
                ("--dry-run", "Exécute sans envoyer de notifications"),
            ],
            "credential": [
                ("--repo <repo>", "Nom du dépôt (requis)"),
                ("--file <path>", "Chemin du fichier contenant la fuite (requis)"),
                ("--dry-run", "Exécute sans envoyer de notifications"),
            ],
            "replay": [
                ("--date <YYYY-MM-DD>", "Date à rejouer (requis)"),
                ("--dry-run", "Exécute sans envoyer de notifications"),
            ],
            "demo": [
                ("--dry-run", "Exécute sans envoyer de notifications"),
                ("--notify", "Envoie les notifications Google Chat"),
            ],
        },
        "examples": [
            "governor simulate commit --author vbLBB --repo emailfactory --message \"ajout template\"",
            "governor simulate commit --author dany-yourart --repo koan --message \"test\" --files main.py,utils.py",
            "governor simulate credential --repo fetching --file .env",
            "governor simulate replay --date 2026-03-03",
            "governor simulate demo",
            "governor simulate demo --notify",
            "governor simulate demo --dry-run",
        ],
    },
    "tunnel": {
        "desc": "Gestion du tunnel Cloudflare pour les webhooks GitHub",
        "usage": "governor tunnel [action]",
        "actions": {
            "status": "Vérifie si le tunnel cloudflared est actif (PID)",
            "start": "Lance le tunnel (cloudflared tunnel → http://localhost:5001)",
            "stop": "Arrête le tunnel (pkill cloudflared)",
        },
        "examples": [
            "governor tunnel status",
            "governor tunnel start",
            "governor tunnel stop",
        ],
    },
}


def _print_help_global():
    """Print full help with all commands."""
    lines = [
        _bold("AI Governor CLI v" + VERSION),
        "",
        "  Agent IA de gouvernance pour l'organisation GitHub YourArtOfficial.",
        "  Surveille les repos, détecte les duplications, gère les budgets API",
        "  et assiste les gouverneurs humains dans l'administration du code.",
        "",
        _bold("USAGE"),
        f"  governor <commande> [action] [options] [flags]",
        f"  governor help <commande>          Aide détaillée sur une commande",
        "",
        _bold("FLAGS GLOBAUX"),
        f"  {'--json':<20} Sortie en JSON brut (désactive les couleurs)",
        f"  {'--notify':<20} Envoie aussi le résultat sur Google Chat",
        f"  {'--dry-run':<20} Exécute sans envoyer de notifications",
        f"  {'--verbose':<20} Affiche les logs de debug (durée, skill, args)",
        f"  {'--version':<20} Affiche la version",
        "",
        _bold("COMMANDES"),
        "",
        _bold("  Surveillance & Analyse"),
        f"    {'watcher':<14} {HELP_COMMANDS['watcher']['desc']}",
        f"    {'advisor':<14} {HELP_COMMANDS['advisor']['desc']}",
        f"    {'scan':<14} {HELP_COMMANDS['scan']['desc']}",
        "",
        _bold("  Rapports & Monitoring"),
        f"    {'status':<14} {HELP_COMMANDS['status']['desc']}",
        f"    {'report':<14} {HELP_COMMANDS['report']['desc']}",
        "",
        _bold("  Budget & Clés API"),
        f"    {'budget':<14} {HELP_COMMANDS['budget']['desc']}",
        f"    {'keys':<14} {HELP_COMMANDS['keys']['desc']}",
        "",
        _bold("  Credentials & Secrets"),
        f"    {'vault':<14} {HELP_COMMANDS['vault']['desc']}",
        f"    {'env':<14} {HELP_COMMANDS['env']['desc']}",
        "",
        _bold("  Administration"),
        f"    {'autonomy':<14} {HELP_COMMANDS['autonomy']['desc']}",
        f"    {'rollout':<14} {HELP_COMMANDS['rollout']['desc']}",
        f"    {'offboard':<14} {HELP_COMMANDS['offboard']['desc']}",
        "",
        _bold("  Tests & Debug"),
        f"    {'simulate':<14} {HELP_COMMANDS['simulate']['desc']}",
        f"    {'tunnel':<14} {HELP_COMMANDS['tunnel']['desc']}",
        "",
        _bold("EXEMPLES RAPIDES"),
        f"  governor status                          Health check global",
        f"  governor watcher log --author vbLBB      Journal d'un citizen",
        f"  governor advisor scan --full             Indexation complète",
        f"  governor report daily --notify           Rapport du jour → Google Chat",
        f"  governor budget status                   Vue budgets",
        f"  governor --json advisor status           Sortie JSON",
        f"  governor simulate demo                   Démo E2E (6 scénarios)",
        "",
        _dim("Tapez 'governor help <commande>' pour l'aide détaillée d'une commande."),
    ]
    print("\n".join(lines))


def _print_help_command(command: str):
    """Print detailed help for a specific command."""
    info = HELP_COMMANDS.get(command)
    if not info:
        # Try fuzzy match
        all_cmds = list(HELP_COMMANDS.keys())
        matches = difflib.get_close_matches(command, all_cmds, n=3, cutoff=0.4)
        print(_red(f"Commande inconnue : '{command}'"))
        if matches:
            print(f"Commandes similaires : {', '.join(matches)}")
        print(f"\nTapez 'governor help' pour la liste complète.")
        return

    lines = [
        _bold(f"governor {command}") + f" — {info['desc']}",
        "",
        _bold("USAGE"),
        f"  {info['usage']}",
        "",
        _bold("ACTIONS"),
    ]

    for action_name, action_desc in info["actions"].items():
        lines.append(f"  {_green(action_name)}")
        lines.append(f"      {action_desc}")

        # Show flags for this action if any
        if "flags" in info:
            base_action = action_name.split()[0].rstrip("[]")
            if base_action in info["flags"]:
                for flag, flag_desc in info["flags"][base_action]:
                    lines.append(f"      {_yellow(flag):<30} {flag_desc}")
        lines.append("")

    lines.append(_bold("EXEMPLES"))
    for ex in info["examples"]:
        lines.append(f"  {_dim('$')} {ex}")

    print("\n".join(lines))


# ── Google Chat notify ──────────────────────────────────────────────

def send_to_gchat(title: str, body: str, thread_key: Optional[str] = None) -> bool:
    """Send a message to Google Chat via webhook. Returns True on success."""
    import requests

    webhook_url = _get_gchat_url()
    if not webhook_url:
        return False

    card = {
        "cardsV2": [{
            "cardId": f"governor-{thread_key or 'default'}",
            "card": {
                "header": {
                    "title": f"AI Governor — {title}",
                    "imageUrl": "https://fonts.gstatic.com/s/i/short-term/release/googlesymbols/security/default/24px.svg",
                    "imageType": "CIRCLE",
                },
                "sections": [{
                    "widgets": [{
                        "textParagraph": {"text": body[:3000]}
                    }]
                }]
            }
        }]
    }

    params = {}
    if thread_key:
        params["threadKey"] = thread_key
        params["messageReplyOption"] = "REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD"

    try:
        url = webhook_url
        if params:
            url += "&" if "?" in url else "?"
            url += "&".join(f"{k}={v}" for k, v in params.items())
        resp = requests.post(url, json=card, timeout=10)
        if resp.status_code == 429:
            time.sleep(2)
            resp = requests.post(url, json=card, timeout=10)
        return resp.status_code == 200
    except Exception:
        return False


def _get_gchat_url() -> Optional[str]:
    url = os.environ.get("GCHAT_WEBHOOK_URL")
    if url:
        return url
    config = load_config()
    env_key = config.get("go_live", {}).get("gchat_webhook_url_env", "GCHAT_WEBHOOK_URL")
    return os.environ.get(env_key)


# ── Docker check ────────────────────────────────────────────────────

def _check_docker() -> Optional[str]:
    """Check if Docker is running. Returns error message or None."""
    try:
        subprocess.run(
            ["docker", "info"],
            capture_output=True, timeout=5,
        )
        return None
    except FileNotFoundError:
        return "Docker non installé. Installez Docker Desktop : https://docker.com"
    except subprocess.TimeoutExpired:
        return "Docker ne répond pas. Ouvrez Docker Desktop et relancez."
    except Exception:
        return "Docker non démarré. Ouvrez Docker Desktop, puis relancez."


# ── Argparse ────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="governor",
        description="AI Governor CLI — Exécution directe des skills sans Telegram",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commandes disponibles:
  status              Health check unifié de tous les modules
  watcher             Surveillance des repos GitHub et GitLab
  advisor             Détection de duplications et recommandations
  autonomy            Gestion des niveaux d'autonomie
  rollout             Gestion du déploiement progressif
  budget              Gestion des budgets API
  vault               Gestion des credentials
  env                 Injection de variables d'environnement
  scan                Scan des credentials dans le code
  report              Rapport journalier/hebdomadaire AI Governor
  simulate            Simuler des événements pour tester le pipeline
  tunnel              Gestion du tunnel pour les webhooks GitHub

Exemples:
  governor status
  governor watcher scan
  governor advisor analyze https://github.com/org/repo/commit/abc123
  governor advisor feedback DET-042 relevant --notes "Bonne détection"
  governor simulate commit --author dany-yourart --repo emailfactory --message "test"
  governor --json status
""",
    )
    parser.add_argument("--version", action="version", version=f"governor {VERSION}")
    parser.add_argument("--json", dest="output_json", action="store_true",
                        help="Sortie en JSON brut")
    parser.add_argument("--notify", action="store_true",
                        help="Envoyer aussi le résultat sur Google Chat")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="Exécuter sans envoyer de notifications")
    parser.add_argument("--verbose", action="store_true",
                        help="Afficher les logs de debug")
    parser.add_argument("command", nargs="?", help="Commande governor (status, watcher, advisor, ...)")
    parser.add_argument("rest", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)

    return parser


# ── Main ────────────────────────────────────────────────────────────

def main() -> int:
    global _USE_COLOR

    parser = _build_parser()
    args = parser.parse_args()

    if args.output_json:
        _USE_COLOR = False

    if not args.command:
        _print_help_global()
        return EXIT_OK

    command = args.command.lower()
    rest = args.rest or []

    # Extract action (first positional after command) and remaining args
    action = rest[0] if rest else ""
    extra_args = " ".join(rest[1:]) if len(rest) > 1 else ""

    # Handle help command
    if command == "help":
        if action:
            _print_help_command(action)
        else:
            _print_help_global()
        return EXIT_OK

    # Handle simulate and tunnel separately (not standard skills)
    if command == "simulate":
        from app.simulator import handle_simulate
        return handle_simulate(action, extra_args, args)

    if command == "tunnel":
        return _handle_tunnel(action, args)

    # Standard skill dispatch
    exit_code, result = dispatch_skill(command, action, extra_args, args)

    if exit_code == EXIT_SKILL_NOT_FOUND:
        print(_red(result), file=sys.stderr)
        return exit_code

    # Output formatting
    if args.output_json:
        output = json.dumps({
            "command": command,
            "action": action,
            "result": result,
            "exit_code": exit_code,
        }, ensure_ascii=False, indent=2)
        print(output)
    else:
        if args.dry_run:
            print(_yellow("[DRY RUN] ") + result)
        else:
            print(result)

    # --notify: also send to Google Chat
    if args.notify and not args.dry_run:
        title = f"{command} {action}".strip()
        ok = send_to_gchat(title, result, thread_key=command)
        if ok:
            print(_dim("→ Notification envoyée sur Google Chat"))
        else:
            print(_yellow("→ Échec envoi Google Chat (vérifiez GCHAT_WEBHOOK_URL)"))

    return exit_code


# ── Tunnel commands (US5) ───────────────────────────────────────────

def _handle_tunnel(action: str, flags: argparse.Namespace) -> int:
    if action == "status":
        return _tunnel_status()
    elif action == "start":
        return _tunnel_start()
    elif action == "stop":
        return _tunnel_stop()
    else:
        print("Usage: governor tunnel [status|start|stop]")
        return EXIT_ERROR


def _tunnel_status() -> int:
    try:
        result = subprocess.run(
            ["pgrep", "-f", "cloudflared.*tunnel"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            pids = result.stdout.strip().split("\n")
            print(_green("Tunnel actif") + f" (PID: {', '.join(pids)})")
        else:
            print(_yellow("Tunnel inactif") + " — lancez 'governor tunnel start'")
    except Exception as e:
        print(_red(f"Erreur vérification tunnel : {e}"))
    return EXIT_OK


def _tunnel_start() -> int:
    try:
        subprocess.Popen(
            ["cloudflared", "tunnel", "--url", "http://localhost:5001"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(_green("Tunnel cloudflared démarré") + " → http://localhost:5001")
        print(_dim("L'URL publique apparaîtra dans les logs cloudflared."))
        return EXIT_OK
    except FileNotFoundError:
        print(_red("cloudflared non installé.") + " Installez avec : brew install cloudflared")
        return EXIT_ERROR


def _tunnel_stop() -> int:
    try:
        subprocess.run(["pkill", "-f", "cloudflared.*tunnel"], check=True)
        print(_green("Tunnel arrêté."))
        return EXIT_OK
    except subprocess.CalledProcessError:
        print(_yellow("Aucun tunnel actif à arrêter."))
        return EXIT_OK
