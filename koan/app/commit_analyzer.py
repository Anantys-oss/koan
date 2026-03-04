"""Smart commit notifications — LLM-powered analysis for citizen pushes.

Analyzes each webhook event and produces a rich Google Chat Card v2:
- Citizen push → full LLM analysis (functional, technical, org, recommendations)
- Tech/governor push → light notification (no LLM)
- Other events (PR, issues, create) → light notification

Uses the same LiteLLM proxy and circuit breaker as advisor/helpers.py.
"""

import json
import logging
from datetime import datetime, timezone

from app.utils import KOAN_ROOT, INSTANCE_DIR

logger = logging.getLogger("commit_analyzer")

ANALYSIS_PROMPT = """\
Tu es un assistant de gouvernance pour une organisation de développement logiciel.
Analyse ce commit et produis un JSON (sans markdown, juste le JSON brut).

Auteur: {author} ({author_type})
Repo: {repo} ({platform})
Message: {summary}

Contexte organisation:
{org_context}

Produis exactement ce JSON:
{{
  "resume_fonctionnel": "ce que ça fait côté produit (1-2 phrases)",
  "analyse_technique": "qualité, patterns, risques potentiels (1-2 phrases)",
  "contexte_orga": "qui d'autre travaille sur ce sujet ou ce repo (1 phrase)",
  "recommandations": ["reco1", "reco2"],
  "niveau_attention": "info|attention|alerte",
  "tags": ["feature", "bugfix", "refactoring", "infra", "data", "security", "docs"]
}}

Choisis les tags pertinents parmi la liste. niveau_attention:
- "info" = commit normal, rien de spécial
- "attention" = commit notable (nouvelle feature importante, pattern inhabituel)
- "alerte" = credential, force push, risque sécurité
"""


def analyze_and_notify(event, config: dict) -> None:
    """Main entry point — route citizen vs tech, build and send notification."""
    author_type = getattr(event, "author_type", "unknown")
    event_type = getattr(event, "type", "push")

    if author_type == "citizen" and event_type == "push":
        if config.get("citizen_full_analysis", True):
            _analyze_citizen_commit(event, config)
        else:
            _send_light_notification(event, config)
    elif author_type == "tech":
        if config.get("tech_light_notify", True):
            _send_light_notification(event, config)
    elif author_type == "governor":
        if config.get("governor_light_notify", False):
            _send_light_notification(event, config)
    else:
        _send_light_notification(event, config)


def _analyze_citizen_commit(event, config: dict) -> None:
    """Full LLM analysis for citizen commits → rich Card v2."""
    org_context = _get_org_context(
        getattr(event, "author", "unknown"),
        getattr(event, "repo", "unknown"),
    )

    org_text = ""
    if org_context.get("recent_authors"):
        authors = ", ".join(org_context["recent_authors"][:5])
        org_text = f"Contributeurs récents sur {org_context.get('repo', '?')}: {authors}"
    if not org_text:
        org_text = "Pas de contexte récent disponible."

    prompt = ANALYSIS_PROMPT.format(
        author=getattr(event, "author", "?"),
        author_type=getattr(event, "author_type", "?"),
        repo=getattr(event, "repo", "?"),
        platform=getattr(event, "platform", "?"),
        summary=getattr(event, "summary", "?"),
        org_context=org_text,
    )

    analysis = _call_llm_analysis(prompt, config)
    if analysis is None:
        _send_light_notification(event, config)
        return

    cards = _build_smart_card(analysis, event)
    _send_cards(cards, event, config)


def _call_llm_analysis(prompt: str, config: dict) -> dict | None:
    """Call LLM via summarize_with_llm (reuses advisor LiteLLM infra), parse JSON."""
    try:
        from app.advisor.helpers import summarize_with_llm
    except ImportError:
        logger.warning("advisor.helpers not available, skipping LLM analysis")
        return None

    llm_config = {"summary_model": config.get("analysis_model", "claude-haiku")}
    try:
        raw = summarize_with_llm(prompt, llm_config)
        return _parse_analysis_json(raw) if raw else None
    except Exception as e:
        logger.warning("Smart notification LLM error: %s", e)
        return None


def _parse_analysis_json(content: str) -> dict | None:
    """Parse LLM response as JSON, handling markdown code blocks."""
    text = content.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)
    try:
        data = json.loads(text)
        required = ["resume_fonctionnel", "analyse_technique", "niveau_attention"]
        if all(k in data for k in required):
            return data
        logger.warning("LLM response missing required fields: %s", list(data.keys()))
        return None
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse LLM JSON: %s", e)
        return None


def _build_smart_card(analysis: dict, event) -> list:
    """Build a Google Chat Card v2 from LLM analysis."""
    author = getattr(event, "author", "?")
    repo = getattr(event, "repo", "?")
    niveau = analysis.get("niveau_attention", "info")
    tags = analysis.get("tags", [])

    icon_map = {"info": "INFO", "attention": "BOOKMARK", "alerte": "URGENCY"}
    color_map = {"info": "#3fb950", "attention": "#d29922", "alerte": "#f85149"}

    tag_str = " ".join(f"[{t}]" for t in tags) if tags else ""

    widgets = []

    widgets.append({
        "decoratedText": {
            "topLabel": "Commit",
            "text": f"<b>{author}</b> sur <b>{repo}</b>",
            "bottomLabel": tag_str,
        }
    })

    widgets.append({
        "textParagraph": {
            "text": (
                f"<b>Résumé fonctionnel</b>\n{analysis.get('resume_fonctionnel', '—')}\n\n"
                f"<b>Analyse technique</b>\n{analysis.get('analyse_technique', '—')}\n\n"
                f"<b>Contexte orga</b>\n{analysis.get('contexte_orga', '—')}"
            )
        }
    })

    recos = analysis.get("recommandations", [])
    if recos:
        reco_text = "\n".join(f"• {r}" for r in recos[:3])
        widgets.append({
            "textParagraph": {
                "text": f"<b>Recommandations</b>\n{reco_text}"
            }
        })

    return [{
        "cardId": f"smart-{repo}-{author}",
        "card": {
            "header": {
                "title": f"Smart Notification — {repo}",
                "subtitle": f"{author} • {niveau}",
                "imageUrl": "https://fonts.gstatic.com/s/i/short-term/release/googlesymbols/analytics/default/24px.svg",
                "imageType": "CIRCLE",
            },
            "sections": [{"widgets": widgets}],
        },
    }]


def _send_light_notification(event, config: dict) -> None:
    """Send a simple notification without LLM analysis."""
    author = getattr(event, "author", "?")
    repo = getattr(event, "repo", "?")
    summary = getattr(event, "summary", "")[:200]
    event_type = getattr(event, "type", "push")
    author_type = getattr(event, "author_type", "unknown")

    text = f"[{event_type}] <b>{author}</b> ({author_type}) sur <b>{repo}</b>\n{summary}"

    cards = [{
        "cardId": f"light-{repo}-{author}",
        "card": {
            "header": {
                "title": f"{event_type.capitalize()} — {repo}",
                "subtitle": f"{author} ({author_type})",
                "imageUrl": "https://fonts.gstatic.com/s/i/short-term/release/googlesymbols/code/default/24px.svg",
                "imageType": "CIRCLE",
            },
            "sections": [{
                "widgets": [{"textParagraph": {"text": text}}]
            }],
        },
    }]

    _send_cards(cards, event, config)


def _send_cards(cards: list, event, config: dict) -> bool:
    """Send cards to Google Chat via watcher notifier (circuit breaker + retry)."""
    repo = getattr(event, "repo", "unknown")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    thread_key = f"smart-{repo}-{today}"

    try:
        from app.watcher.notifier import send_notification
        return send_notification(text="", thread_key=thread_key, cards=cards)
    except ImportError:
        try:
            from app.governor_cli import send_to_gchat
            return send_to_gchat(f"Smart — {repo}", "", thread_key=thread_key)
        except ImportError:
            logger.warning("No notification channel available")
            return False


def _get_org_context(author: str, repo: str) -> dict:
    """Get organizational context: recent contributors on this repo (7 days)."""
    try:
        from app.watcher.journal import read_events
        events = read_events(INSTANCE_DIR, days=7, repo=repo, limit=100)
        authors = set()
        for evt in events:
            a = evt.get("author", "")
            if a and a != author:
                authors.add(a)
        return {"repo": repo, "recent_authors": sorted(authors)}
    except Exception:
        return {"repo": repo, "recent_authors": []}


