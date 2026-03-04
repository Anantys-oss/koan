"""Google Chat Cards v2 builders for command responses.

Provides card formatting for all Chat App responses: command results,
errors, ack (loading), advisor feedback, and permission denied.

Uses snake_case keys required by the google-apps-chat Python SDK.
"""

STATUS_ICONS = {
    "success": "https://fonts.gstatic.com/s/i/short-term/release/googlesymbols/check_circle/default/24px.svg",
    "error": "https://fonts.gstatic.com/s/i/short-term/release/googlesymbols/error/default/24px.svg",
    "warning": "https://fonts.gstatic.com/s/i/short-term/release/googlesymbols/warning/default/24px.svg",
    "info": "https://fonts.gstatic.com/s/i/short-term/release/googlesymbols/info/default/24px.svg",
    "loading": "https://fonts.gstatic.com/s/i/short-term/release/googlesymbols/hourglass_empty/default/24px.svg",
    "denied": "https://fonts.gstatic.com/s/i/short-term/release/googlesymbols/block/default/24px.svg",
}


def truncate_for_gchat(text: str, max_chars: int = 3000) -> str:
    """Truncate text for Google Chat limits (32KB total payload)."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars - 30] + "\n\n[résultat tronqué]"


def _build_card(card_id: str, title: str, icon_key: str, widgets: list,
                subtitle: str = "", sections: list | None = None) -> list[dict]:
    """Build a Card v2 skeleton. All public builders delegate to this."""
    return [{
        "card_id": card_id,
        "card": {
            "header": {
                "title": title,
                "subtitle": subtitle,
                "image_url": STATUS_ICONS.get(icon_key, STATUS_ICONS["info"]),
                "image_type": "CIRCLE",
            },
            "sections": sections or [{"widgets": widgets}],
        },
    }]


def build_command_response_card(
    command_name: str,
    result_text: str,
    status: str = "success",
    subtitle: str = "",
) -> list[dict]:
    """Build a Card v2 for a governor command response."""
    truncated = truncate_for_gchat(result_text)
    return _build_card(
        card_id=f"cmd-{command_name.replace(' ', '-')}",
        title=f"governor {command_name}",
        icon_key=status,
        subtitle=subtitle or status,
        widgets=[{"text_paragraph": {"text": truncated}}],
    )


def build_error_card(
    error_message: str,
    suggestions: list[str] | None = None,
) -> list[dict]:
    """Build a Card v2 for an error message with optional suggestions."""
    widgets = [{"text_paragraph": {"text": f"<b>Erreur</b> : {error_message}"}}]
    if suggestions:
        suggestion_text = "\n".join(f"• <b>{s}</b>" for s in suggestions)
        widgets.append({
            "text_paragraph": {"text": f"\n<b>Commandes similaires :</b>\n{suggestion_text}"}
        })
    widgets.append({"text_paragraph": {"text": "Tapez <b>help</b> pour la liste complète."}})

    return _build_card(
        card_id="cmd-error",
        title="Commande inconnue",
        icon_key="error",
        widgets=widgets,
    )


def build_ack_card(command_name: str) -> list[dict]:
    """Build a Card v2 acknowledgement for long-running commands."""
    return _build_card(
        card_id=f"ack-{command_name.replace(' ', '-')}",
        title=f"governor {command_name}",
        icon_key="loading",
        subtitle="En cours...",
        widgets=[{"text_paragraph": {"text": "Exécution en cours, veuillez patienter..."}}],
    )


def build_detection_feedback_card(
    detection_id: str,
    description: str,
    source_repo: str,
    target_repo: str,
    similarity: float,
) -> list[dict]:
    """Build a Card v2 for advisor detection with feedback buttons."""
    pct = f"{similarity * 100:.0f}%"

    def _feedback_button(label: str, value: str) -> dict:
        return {
            "text": label,
            "on_click": {
                "action": {
                    "function": "advisor_feedback",
                    "parameters": [
                        {"key": "detectionId", "value": detection_id},
                        {"key": "feedback", "value": value},
                    ],
                }
            },
        }

    return _build_card(
        card_id=f"detection-{detection_id}",
        title="Duplication détectée",
        icon_key="warning",
        subtitle=f"Similarité : {pct}",
        widgets=[],
        sections=[
            {"widgets": [{
                "text_paragraph": {
                    "text": (
                        f"<b>Source</b> : {source_repo}\n"
                        f"<b>Cible</b> : {target_repo}\n\n"
                        f"{truncate_for_gchat(description, 2000)}"
                    )
                }
            }]},
            {"widgets": [{
                "button_list": {
                    "buttons": [
                        _feedback_button("Pertinent", "relevant"),
                        _feedback_button("Faux positif", "false-positive"),
                        _feedback_button("Ignorer", "ignore"),
                    ]
                }
            }]},
        ],
    )


def build_permission_denied_card(
    sender_name: str,
    command_name: str,
    sender_type: str,
) -> list[dict]:
    """Build a Card v2 for permission denied."""
    if sender_type == "unknown":
        message = f"Utilisateur non reconnu ({sender_name}). Contactez un governor."
    else:
        message = f"La commande <b>{command_name}</b> est réservée aux governors."

    return _build_card(
        card_id="cmd-denied",
        title="Accès refusé",
        icon_key="denied",
        subtitle=sender_type,
        widgets=[{"text_paragraph": {"text": message}}],
    )
