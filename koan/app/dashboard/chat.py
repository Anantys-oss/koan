"""Chat blueprint: chat handler, live progress, and SSE state streams."""
import json
import logging
import os
import subprocess
import sys
import time
from datetime import date

from flask import Blueprint, Response, jsonify, render_template, request

from app.cli_provider import build_full_command
from app.config import get_allowed_tools, get_model_config, get_tools_description
from app.conversation_history import (
    format_conversation_history,
    load_recent_history,
    save_conversation_message,
)
from app.dashboard import state
from app.dashboard_service import missions as missions_svc
from app.dashboard_service import read_file
from app.dashboard_service import stats as stats_svc
from app.dashboard_service.stats import _EMPTY_FORECAST
from app.utils import insert_pending_mission, parse_project

chat_bp = Blueprint("chat", __name__)


def _build_dashboard_prompt(text: str, *, lite: bool = False) -> str:
    """Build the prompt for a dashboard chat response.

    Args:
        text: The user's message.
        lite: If True, strip heavy context (journal, summary) to reduce prompt size.
    """
    from app.journal import read_all_journals

    history = load_recent_history(state.CONVERSATION_HISTORY_FILE, max_messages=10)
    history_context = format_conversation_history(history)

    soul = read_file(state.SOUL_FILE)

    summary = ""
    if not lite:
        summary = read_file(state.SUMMARY_FILE)[:1500]

    journal_context = ""
    if not lite:
        journal_content = read_all_journals(state.INSTANCE_DIR, date.today())
        if journal_content:
            journal_context = journal_content[-2000:] if len(journal_content) > 2000 else journal_content

    from app.prompts import load_prompt

    tools_desc = get_tools_description()
    summary_block = f"Summary of past sessions:\n{summary}" if summary else ""
    journal_block = f"Today's journal (excerpt):\n{journal_context}" if journal_context else ""

    return load_prompt(
        "dashboard-chat",
        SOUL=soul,
        TOOLS_DESC=tools_desc or "",
        SUMMARY=summary_block,
        JOURNAL=journal_block,
        HISTORY=history_context or "",
        TEXT=text,
    )


@chat_bp.route("/chat")
def chat_page():
    """Chat interface."""
    return render_template("chat.html")


@chat_bp.route("/chat/send", methods=["POST"])
def chat_send():
    """Send a message — either as mission or direct outbox message."""
    from app.cli_exec import run_cli

    text = request.form.get("message", "").strip()
    mode = request.form.get("mode", "chat")  # chat or mission

    if not text:
        return jsonify({"ok": False, "error": "Empty message"})

    if mode == "mission":
        # Queue as mission (same logic as awake.py)
        from app.missions import sanitize_mission_text

        text = sanitize_mission_text(text)
        project, mission_text = parse_project(text)
        if project:
            entry = f"- [project:{project}] {mission_text}"
        else:
            entry = f"- {mission_text}"

        inserted = insert_pending_mission(state.MISSIONS_FILE, entry)
        if inserted:
            try:
                from app.api.mission_index import record_mission
                record_mission(state.INSTANCE_DIR, entry, project or None)
            except Exception as exc:
                logging.warning("record_mission failed (non-fatal): %s", exc)
        return jsonify({"ok": True, "type": "mission", "text": mission_text})

    else:
        # Direct chat — call claude CLI like awake.py does
        # Save user message to history
        save_conversation_message(state.CONVERSATION_HISTORY_FILE, "user", text)

        prompt = _build_dashboard_prompt(text)
        project_path = os.environ.get("KOAN_CURRENT_PROJECT_PATH", str(state.KOAN_ROOT))
        allowed_tools_list = get_allowed_tools().split(",")
        models = get_model_config()

        cmd = build_full_command(
            prompt=prompt,
            allowed_tools=allowed_tools_list,
            model=models["chat"],
            fallback=models["fallback"],
            max_turns=1,
        )

        try:
            result = run_cli(
                cmd,
                capture_output=True, text=True, timeout=state.CHAT_TIMEOUT,
                cwd=project_path,
            )
            response = result.stdout.strip()
            if result.returncode != 0:
                print(f"[dashboard] Claude error (exit {result.returncode}): {result.stderr[:200]}", file=sys.stderr)
            if not response:
                if result.stderr:
                    print(f"[dashboard] Claude stderr: {result.stderr[:500]}")
                response = "I couldn't formulate a response. Try again?"
            # Save assistant response to history
            save_conversation_message(state.CONVERSATION_HISTORY_FILE, "assistant", response)
            return jsonify({"ok": True, "type": "chat", "response": response})
        except subprocess.TimeoutExpired:
            # Retry with lite context (no journal, no summary) like awake.py
            print(f"[dashboard] Chat timed out ({state.CHAT_TIMEOUT}s). Retrying with lite context...")
            lite_prompt = _build_dashboard_prompt(text, lite=True)
            lite_cmd = build_full_command(
                prompt=lite_prompt,
                allowed_tools=allowed_tools_list,
                model=models["chat"],
                fallback=models["fallback"],
                max_turns=1,
            )
            try:
                result = run_cli(
                    lite_cmd,
                    capture_output=True, text=True, timeout=state.CHAT_TIMEOUT,
                    cwd=project_path,
                )
                if result.stderr:
                    print(f"[dashboard] Lite retry stderr: {result.stderr[:500]}")
                response = result.stdout.strip()
                if result.returncode != 0:
                    print(f"[dashboard] Claude error on retry (exit {result.returncode}): {result.stderr[:200]}", file=sys.stderr)
                if response:
                    save_conversation_message(state.CONVERSATION_HISTORY_FILE, "assistant", response)
                    return jsonify({"ok": True, "type": "chat", "response": response})
                else:
                    timeout_msg = f"Timeout after {state.CHAT_TIMEOUT}s — try a shorter question."
                    save_conversation_message(state.CONVERSATION_HISTORY_FILE, "assistant", timeout_msg)
                    return jsonify({"ok": True, "type": "chat", "response": timeout_msg})
            except subprocess.TimeoutExpired:
                timeout_msg = f"Timeout after {state.CHAT_TIMEOUT}s — try a shorter question."
                save_conversation_message(state.CONVERSATION_HISTORY_FILE, "assistant", timeout_msg)
                return jsonify({"ok": True, "type": "chat", "response": timeout_msg})
            except (OSError, ValueError) as e:
                return jsonify({"ok": False, "error": str(e)})
        except (OSError, ValueError) as e:
            return jsonify({"ok": False, "error": str(e)})


@chat_bp.route("/progress")
def progress_page():
    """Live progress page — tails pending.md via SSE."""
    return render_template("progress.html")


@chat_bp.route("/api/progress")
def api_progress():
    """JSON snapshot of pending.md content."""
    content = read_file(state.PENDING_FILE)
    return jsonify({
        "active": state.PENDING_FILE.exists(),
        "content": content,
    })


@chat_bp.route("/api/progress/stream")
def api_progress_stream():
    """SSE stream of pending.md changes.

    Polls the file every second, sends an event when content changes.
    Sends a heartbeat comment every 15s to keep the connection alive.
    """
    def generate():
        last_content = None
        last_mtime = 0.0
        heartbeat_counter = 0

        while True:
            try:
                if state.PENDING_FILE.exists():
                    st = state.PENDING_FILE.stat()
                    if st.st_mtime != last_mtime:
                        last_mtime = st.st_mtime
                        content = state.PENDING_FILE.read_text()
                        if content != last_content:
                            last_content = content
                            payload = json.dumps({
                                "active": True,
                                "content": content,
                            })
                            yield f"data: {payload}\n\n"
                            heartbeat_counter = 0
                else:
                    if last_content is not None:
                        # File was deleted — mission completed
                        payload = json.dumps({
                            "active": False,
                            "content": "",
                        })
                        yield f"data: {payload}\n\n"
                        last_content = None
                        last_mtime = 0.0
                        heartbeat_counter = 0
            except OSError:
                pass

            heartbeat_counter += 1
            if heartbeat_counter >= 15:
                yield ": heartbeat\n\n"
                heartbeat_counter = 0

            time.sleep(1)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@chat_bp.route("/api/state/stream")
def api_state_stream():
    """SSE stream of agent state changes.

    Polls signal files every 2s, sends an event when state changes.
    Sends a heartbeat comment every 15s to keep the connection alive.
    Includes attention_count (cached at 30s TTL) in each payload.
    """
    def generate():
        last_json = None
        heartbeat_counter = 0
        # Mutable containers for mtime-based mission count caching
        missions_mtime = [0.0]
        missions_counts = [{"pending": 0, "in_progress": 0, "done": 0}]
        # Mutable container for mtime-based forecast caching
        burn_rate_mtime = [0.0]
        forecast_cache = [{**_EMPTY_FORECAST}]
        # Mutable container for mtime-based config-sync caching
        config_mtime = [0.0]
        config_sync_cache = [{"synced": True, "restart_pending": False,
                              "changed_safe_keys": [], "changed_unsafe_keys": []}]

        while True:
            try:
                agent_state = stats_svc.get_agent_state()
                # Add attention count (cheap — uses 30s cache)
                try:
                    from app.attention import get_attention_count
                    agent_state["attention_count"] = get_attention_count(str(state.KOAN_ROOT))
                except Exception as e:
                    print(f"[dashboard] attention count error: {e}", file=sys.stderr)
                    agent_state["attention_count"] = 0
                # Add mission counts (uses mtime check to avoid re-parsing)
                try:
                    if state.MISSIONS_FILE.exists():
                        mtime = state.MISSIONS_FILE.stat().st_mtime
                        if mtime != missions_mtime[0]:
                            missions_mtime[0] = mtime
                            m = missions_svc.parse_missions()
                            missions_counts[0] = {
                                "pending": len(m["pending"]),
                                "in_progress": len(m["in_progress"]),
                                "done": len(m["done"]),
                            }
                    else:
                        missions_counts[0] = {"pending": 0, "in_progress": 0, "done": 0}
                except OSError:
                    pass
                agent_state["missions"] = missions_counts[0]
                # Add forecast (uses mtime check on .burn-rate.json to avoid re-reading)
                try:
                    burn_rate_file = state.INSTANCE_DIR / ".burn-rate.json"
                    br_mtime = burn_rate_file.stat().st_mtime if burn_rate_file.exists() else 0.0
                except OSError:
                    br_mtime = 0.0
                if br_mtime != burn_rate_mtime[0]:
                    burn_rate_mtime[0] = br_mtime
                    forecast_cache[0] = stats_svc.build_forecast()
                agent_state["forecast"] = forecast_cache[0]
                # Add config-sync status (mtime-gated on config files + baseline)
                try:
                    from app import config_sync as _cs
                    newest = 0.0
                    for p in (state.INSTANCE_DIR / "config.yaml",
                              state.INSTANCE_DIR / "projects.yaml",
                              state.INSTANCE_DIR / _cs.BASELINE_FILE):
                        if p.exists():
                            newest = max(newest, p.stat().st_mtime)
                    if newest != config_mtime[0]:
                        config_mtime[0] = newest
                        config_sync_cache[0] = _cs.compute_status(state.KOAN_ROOT)
                except (OSError, ImportError) as e:
                    print(f"[dashboard] config_sync error: {e}", file=sys.stderr)
                agent_state["config_sync"] = config_sync_cache[0]
                state_json = json.dumps(agent_state, sort_keys=True)
                if state_json != last_json:
                    last_json = state_json
                    yield f"data: {json.dumps(agent_state)}\n\n"
                    heartbeat_counter = 0
            except OSError:
                pass

            heartbeat_counter += 1
            if heartbeat_counter >= 8:  # 8 * 2s = 16s ~ 15s heartbeat
                yield ": heartbeat\n\n"
                heartbeat_counter = 0

            time.sleep(2)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
