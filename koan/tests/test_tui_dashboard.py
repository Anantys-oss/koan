"""Tests for the terminal dashboard (app.tui_dashboard)."""

import asyncio
import time

import pytest

from app import tui_dashboard as tui


@pytest.fixture(autouse=True)
def _no_keepawake(monkeypatch):
    # Never spawn the real keep-awake (caffeinate / systemd-inhibit) in tests.
    monkeypatch.setattr(tui.KoanDashboard, "_start_keepawake", lambda self: None)


def _write_config(tmp_path, text):
    inst = tmp_path / "instance"
    inst.mkdir(exist_ok=True)
    (inst / "config.yaml").write_text(text)
    return tmp_path


# --- value coercion ---------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("true", True),
    ("false", False),
    ("42", 42),
    ("3.5", 3.5),
    ("hello world", "hello world"),
])
def test_coerce_types(raw, expected):
    assert tui._coerce(raw) == expected


# --- comment-preserving edit ------------------------------------------------

def test_set_config_value_updates_nested_key(tmp_path):
    _write_config(tmp_path, "auto_update:\n  enabled: false\n")
    tui.set_config_value(tmp_path, "auto_update.enabled", True)
    out = (tmp_path / "instance" / "config.yaml").read_text()
    import yaml
    assert yaml.safe_load(out)["auto_update"]["enabled"] is True


def test_set_config_value_preserves_comments(tmp_path):
    _write_config(tmp_path, "# top comment\nauto_update:\n  enabled: false  # inline\n")
    tui.set_config_value(tmp_path, "auto_update.enabled", True)
    out = (tmp_path / "instance" / "config.yaml").read_text()
    assert "# top comment" in out
    assert "# inline" in out


def test_set_config_value_creates_missing_path(tmp_path):
    _write_config(tmp_path, "existing: 1\n")
    tui.set_config_value(tmp_path, "new.deep.key", "v")
    import yaml
    out = yaml.safe_load((tmp_path / "instance" / "config.yaml").read_text())
    assert out["new"]["deep"]["key"] == "v"
    assert out["existing"] == 1


def test_set_nested_key_helper():
    data = {}
    tui._set_nested_key(data, "a.b.c", 42)
    assert data["a"]["b"]["c"] == 42

    # Overwrites existing value
    tui._set_nested_key(data, "a.b.c", 99)
    assert data["a"]["b"]["c"] == 99

    # Extends existing nested path
    tui._set_nested_key(data, "a.d", "hello")
    assert data["a"]["d"] == "hello"
    assert data["a"]["b"]["c"] == 99  # sibling intact


# --- bar rendering ----------------------------------------------------------

def test_bar_contains_percentage_and_blocks(tmp_path):
    _write_config(tmp_path, "x: 1\n")
    app = tui.KoanDashboard(tmp_path)
    bar = app._bar("Session", 50, "3h")
    assert "50%" in bar
    assert "█" in bar and "░" in bar


# --- textual pilot ----------------------------------------------------------

def test_pilot_builds_tree_and_edits(tmp_path):
    _write_config(tmp_path, "auto_update:\n  enabled: false\n")

    async def scenario():
        app = tui.KoanDashboard(tmp_path)
        async with app.run_test() as pilot:
            tree = app.query_one("#config-tree", tui.Tree)
            # Root has the auto_update branch with one editable leaf.
            assert len(tree.root.children) == 1
            branch = tree.root.children[0]
            branch.expand()
            await pilot.pause()
            leaf = branch.children[0]
            assert leaf.data["path"] == "auto_update.enabled"
            # Apply an edit through the same path the modal uses.
            tui.set_config_value(tmp_path, leaf.data["path"], True)
            app._build_config_tree()
            await pilot.pause()

    asyncio.run(scenario())
    import yaml
    out = yaml.safe_load((tmp_path / "instance" / "config.yaml").read_text())
    assert out["auto_update"]["enabled"] is True


def test_pilot_config_tab_focuses_tree_and_arrows_move(tmp_path):
    _write_config(tmp_path, "a:\n  one: 1\n  two: 2\n  three: 3\n")

    async def scenario():
        app = tui.KoanDashboard(tmp_path)
        async with app.run_test() as pilot:
            app.query_one(tui.TabbedContent).active = "config"
            await pilot.pause()
            tree = app.query_one("#config-tree", tui.Tree)
            # Focus stays on the tab bar after tab activation; Down enters the tree.
            assert app.focused is not tree
            await pilot.press("down")
            await pilot.pause()
            assert app.focused is tree
            tree.root.children[0].expand()
            await pilot.pause()
            start = tree.cursor_line
            await pilot.press("down")
            await pilot.pause()
            assert tree.cursor_line != start  # arrows browse the tree

    asyncio.run(scenario())


def test_pilot_can_leave_config_tab_via_number_keys(tmp_path):
    _write_config(tmp_path, "a:\n  one: 1\n  two: 2\n")

    async def scenario():
        app = tui.KoanDashboard(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("c")  # to config — focus stays on tab bar
            await pilot.pause()
            tree = app.query_one("#config-tree", tui.Tree)
            assert app.focused is not tree  # tree does not auto-focus
            await pilot.press("2")  # back to logs
            await pilot.pause()
            assert app.query_one(tui.TabbedContent).active == "logs"
            assert app.focused is not tree  # tree no longer traps keys

    asyncio.run(scenario())


def test_pilot_bool_toggles_with_space_and_enter(tmp_path):
    _write_config(tmp_path, "auto_update:\n  enabled: false\n")

    async def scenario():
        app = tui.KoanDashboard(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("c")  # config tab, focus stays on tab bar
            await pilot.pause()
            tree = app.query_one("#config-tree", tui.Tree)
            branch = tree.root.children[0]
            branch.expand()
            await pilot.pause()
            tree.move_cursor(branch.children[0])
            await pilot.pause()
            assert tree.cursor_node.data["path"] == "auto_update.enabled"
            import yaml
            cfg = tmp_path / "instance" / "config.yaml"

            async def _focus_leaf():
                # The tree is rebuilt (collapsed) after each toggle.
                b = tree.root.children[0]
                b.expand()
                await pilot.pause()
                tree.move_cursor(b.children[0])
                await pilot.pause()

            await pilot.press("down")  # move focus into the tree
            await pilot.pause()
            await pilot.press("t")  # toggle false -> true, no modal
            await pilot.pause()
            assert yaml.safe_load(cfg.read_text())["auto_update"]["enabled"] is True

            await _focus_leaf()
            await pilot.press("down")  # re-focus the tree after rebuild
            await pilot.pause()
            await pilot.press("enter")  # enter also flips a bool, no modal
            await pilot.pause()
            assert yaml.safe_load(cfg.read_text())["auto_update"]["enabled"] is False

    asyncio.run(scenario())


def test_pilot_logs_with_ansi_and_brackets_do_not_crash(tmp_path):
    _write_config(tmp_path, "x: 1\n")
    logs = tmp_path / "logs"
    logs.mkdir()
    # Real-world log line: ANSI codes + bracket tokens that look like markup.
    (logs / "run.log").write_text(
        "\x1b[36m=== Run 1/10 — 2026-06-07 19:13:45 ===\x1b[0m\n"
        "[run] picking mission [project:my-app]\n"
    )
    (logs / "awake.log").write_text("\x1b[34m[init]\x1b[0m Token: abc\n")

    async def scenario():
        app = tui.KoanDashboard(tmp_path)
        async with app.run_test() as pilot:
            # Before the fix this raised MarkupError; reaching the assert means
            # the ANSI/bracket content rendered as literal text.
            app.refresh_dynamic()
            await pilot.pause()
            app._render_logs()  # second pass also clean

    asyncio.run(scenario())


def test_pilot_letter_aliases_switch_tabs(tmp_path):
    _write_config(tmp_path, "x: 1\n")

    async def scenario():
        app = tui.KoanDashboard(tmp_path)
        async with app.run_test() as pilot:
            tabs = app.query_one(tui.TabbedContent)
            await pilot.press("c")  # config
            await pilot.pause()
            assert tabs.active == "config"
            await pilot.press("u")  # usage
            await pilot.pause()
            assert tabs.active == "usage"
            await pilot.press("l")  # logs
            await pilot.pause()
            assert tabs.active == "logs"

    asyncio.run(scenario())


# --- status tab + toggles ---------------------------------------------------

def test_dot_on_off():
    app = tui.KoanDashboard("/tmp/x")
    assert "◉" in app._dot(True)
    assert "○" in app._dot(False)


def test_pilot_status_is_initial_tab_with_flags(tmp_path):
    _write_config(tmp_path, "x: 1\n")

    async def scenario():
        app = tui.KoanDashboard(tmp_path)
        async with app.run_test() as pilot:
            assert app.query_one(tui.TabbedContent).active == "status"
            await pilot.pause()
            body = app.query_one("#status-body", tui.Static)
            rendered = body.render()
            text = getattr(rendered, "plain", str(rendered))
            assert "web board" in text
            assert "keep awake" in text
            assert "missions" in text

    asyncio.run(scenario())


def test_pilot_web_toggle_starts_then_stops(tmp_path, monkeypatch):
    _write_config(tmp_path, "x: 1\n")
    state = {"running": False, "started": 0, "stopped": 0}
    monkeypatch.setattr("app.pid_manager.check_pidfile",
                        lambda root, name: 123 if state["running"] else None)

    def fake_start(root):
        state["running"] = True
        state["started"] += 1
        return (True, "ok")

    def fake_stop(root, name, **k):
        state["running"] = False
        state["stopped"] += 1
        return "stopped"

    monkeypatch.setattr("app.pid_manager.start_dashboard", fake_start)
    monkeypatch.setattr("app.pid_manager.stop_process", fake_stop)
    monkeypatch.setattr("webbrowser.open", lambda url: None)

    async def scenario():
        app = tui.KoanDashboard(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("w")  # start
            await pilot.pause()
            await pilot.press("w")  # stop
            await pilot.pause()

    asyncio.run(scenario())
    assert state["started"] == 1
    assert state["stopped"] == 1


def test_keepawake_toggle_lifecycle(tmp_path, monkeypatch):
    # Replace the real spawn with a fake handle so no process is created.
    class FakeProc:
        def __init__(self):
            self._alive = True

        def poll(self):
            return None if self._alive else 0

        def terminate(self):
            self._alive = False

        def wait(self, timeout=None):
            return 0

    def fake_start(self):
        self._keepawake = FakeProc()

    monkeypatch.setattr(tui.KoanDashboard, "_start_keepawake", fake_start)
    app = tui.KoanDashboard(tmp_path)
    # Exercise the helpers directly (actions need a mounted app for notify).
    assert app._keepawake_on() is False
    app._start_keepawake()
    assert app._keepawake_on() is True
    app._stop_keepawake()
    assert app._keepawake_on() is False


def test_keepawake_command_prefers_caffeinate(monkeypatch):
    app = tui.KoanDashboard("/tmp/x")
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/caffeinate" if name == "caffeinate" else None)
    argv, label = app._keepawake_command()
    assert argv[0] == "caffeinate"
    # Linux fallback when caffeinate is absent.
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/systemd-inhibit" if name == "systemd-inhibit" else None)
    argv, label = app._keepawake_command()
    assert argv[0] == "systemd-inhibit"


def test_stop_process_not_running(tmp_path):
    from app import pid_manager
    assert pid_manager.stop_process(tmp_path, "dashboard") == "not_running"


def test_detach_returns_true(tmp_path):
    app = tui.KoanDashboard(tmp_path)
    assert app._detached is False
    app.action_detach()  # sets the flag and asks the app to exit
    assert app._detached is True


def test_new_mission_queues_to_missions_md(tmp_path):
    _write_config(tmp_path, "x: 1\n")

    async def scenario():
        app = tui.KoanDashboard(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("m")  # open the new-mission modal
            await pilot.pause()
            app.screen.query_one("#mission", tui.Input).value = "do the thing"
            await pilot.press("enter")
            await pilot.pause()

    asyncio.run(scenario())
    md = (tmp_path / "instance" / "missions.md").read_text()
    assert "do the thing" in md


def test_pilot_status_shows_mission_titles_and_telegram(tmp_path, monkeypatch):
    _write_config(tmp_path, "x: 1\n")
    inst = tmp_path / "instance"
    # Title carries a [project:koan] tag — must be escaped, not parsed as markup.
    (inst / "missions.md").write_text(
        "# Missions\n\n## Pending\n\n## In Progress\n\n"
        "- /review https://x [project:koan]\n\n## Done\n")
    monkeypatch.setenv("KOAN_TELEGRAM_TOKEN", "t")
    monkeypatch.setenv("KOAN_TELEGRAM_CHAT_ID", "c")

    async def scenario():
        app = tui.KoanDashboard(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.refresh_dynamic()  # would raise MarkupError on the tag before the fix
            await pilot.pause()
            body = app.query_one("#status-body", tui.Static)
            rendered = body.render()
            text = getattr(rendered, "plain", str(rendered))
            assert "project:koan" in text  # tag rendered literally, not parsed
            assert "telegram" in text

    asyncio.run(scenario())

def test_run_status_reads_pidfile(tmp_path, monkeypatch):
    app = tui.KoanDashboard(tmp_path)
    monkeypatch.setattr(
        "app.pid_manager.check_pidfile", lambda root, name: 123 if name == "run" else None
    )
    assert app._run_status() is True
    monkeypatch.setattr(
        "app.pid_manager.check_pidfile", lambda root, name: None
    )
    assert app._run_status() is False


def test_api_status_reads_pidfile(tmp_path, monkeypatch):
    app = tui.KoanDashboard(tmp_path)
    monkeypatch.setattr(
        "app.pid_manager.check_pidfile", lambda root, name: 456 if name == "api" else None
    )
    assert app._api_status() is True
    monkeypatch.setattr(
        "app.pid_manager.check_pidfile", lambda root, name: None
    )
    assert app._api_status() is False


def test_pilot_status_shows_run_and_api(tmp_path, monkeypatch):
    _write_config(tmp_path, "x: 1\n")
    monkeypatch.setattr(
        "app.pid_manager.check_pidfile",
        lambda root, name: 111 if name in ("run", "api") else None,
    )

    async def scenario():
        app = tui.KoanDashboard(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.refresh_dynamic()
            await pilot.pause()
            body = app.query_one("#status-body", tui.Static)
            rendered = body.render()
            text = getattr(rendered, "plain", str(rendered))
            assert "run loop" in text
            assert "api" in text
            assert "running" in text  # run loop is up
            assert "live" in text     # api is up

    asyncio.run(scenario())


def test_pilot_status_shows_provider_and_models(tmp_path, monkeypatch):
    _write_config(tmp_path, "x: 1\n")
    monkeypatch.setattr(
        "app.config.get_cli_provider_name", lambda: "claude"
    )
    monkeypatch.setattr(
        "app.config.get_model_config", lambda project_name="": {
            "mission": "opus",
            "chat": "haiku",
            "lightweight": "haiku",
            "fallback": "sonnet",
            "review_mode": "",
            "reflect": "",
        }
    )

    async def scenario():
        app = tui.KoanDashboard(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.refresh_dynamic()
            await pilot.pause()
            body = app.query_one("#status-body", tui.Static)
            rendered = body.render()
            text = getattr(rendered, "plain", str(rendered))
            assert "provider" in text
            assert "claude" in text
            assert "mission: opus" in text
            assert "chat: haiku" in text
            assert "lightweight: haiku" in text
            assert "fallback: sonnet" in text
            # Empty roles should not appear
            assert "review mode" not in text
            assert "reflect" not in text

    asyncio.run(scenario())


def test_pilot_status_shows_provider_even_without_models(tmp_path, monkeypatch):
    _write_config(tmp_path, "x: 1\n")
    monkeypatch.setattr(
        "app.config.get_cli_provider_name", lambda: "ollama-launch"
    )
    monkeypatch.setattr(
        "app.config.get_model_config", lambda project_name="": {
            "mission": "",
            "chat": "",
            "lightweight": "",
            "fallback": "",
            "review_mode": "",
            "reflect": "",
        }
    )

    async def scenario():
        app = tui.KoanDashboard(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.refresh_dynamic()
            await pilot.pause()
            body = app.query_one("#status-body", tui.Static)
            rendered = body.render()
            text = getattr(rendered, "plain", str(rendered))
            assert "provider" in text
            assert "ollama-launch" in text
            assert "models" not in text  # no non-empty models, line hidden

    asyncio.run(scenario())


# --- usage tab reset --------------------------------------------------------

def _write_usage(tmp_path, session_pct=30, weekly_pct=40):
    inst = tmp_path / "instance"
    inst.mkdir(exist_ok=True)
    (inst / "usage.md").write_text(
        f"# Usage\n\nSession (5hr) : ~{session_pct}% (reset in 3h)\n"
        f"Weekly (7 day) : ~{weekly_pct}% (Resets in 3d)\n"
    )
    return tmp_path


def test_render_usage_shows_hint_when_data_exists(tmp_path):
    _write_usage(tmp_path, 25, 50)

    async def scenario():
        app = tui.KoanDashboard(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("u")
            await pilot.pause()
            body = app.query_one("#usage-body", tui.Static)
            rendered = body.render()
            text = getattr(rendered, "plain", str(rendered))
            assert "25%" in text
            assert "50%" in text
            assert "r = reset / override quota" in text

    asyncio.run(scenario())


def test_render_usage_no_hint_when_no_usage_md(tmp_path):
    inst = tmp_path / "instance"
    inst.mkdir(exist_ok=True)

    async def scenario():
        app = tui.KoanDashboard(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("u")
            await pilot.pause()
            body = app.query_one("#usage-body", tui.Static)
            rendered = body.render()
            text = getattr(rendered, "plain", str(rendered))
            assert "no usage.md yet" in text
            assert "r = reset" not in text

    asyncio.run(scenario())


def test_pilot_reset_quota_override(tmp_path, monkeypatch):
    _write_usage(tmp_path, 60, 70)
    calls = []
    monkeypatch.setattr(
        "app.usage_estimator.cmd_set_used",
        lambda pct, sf, um: calls.append(("set", pct)),
    )
    monkeypatch.setattr(
        "app.usage_estimator.cmd_reset_session",
        lambda sf, um: calls.append(("reset",)),
    )
    monkeypatch.setattr(
        "app.pause_manager.is_paused", lambda root: False
    )

    async def scenario():
        app = tui.KoanDashboard(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("u")  # usage tab
            await pilot.pause()
            await pilot.press("r")  # open reset modal
            await pilot.pause()
            app.screen.query_one("#value", tui.Input).value = "15"
            await pilot.press("enter")
            await pilot.pause()

    asyncio.run(scenario())
    assert calls == [("set", 15)]


def test_pilot_reset_quota_full_reset(tmp_path, monkeypatch):
    _write_usage(tmp_path, 80, 90)
    calls = []
    monkeypatch.setattr(
        "app.usage_estimator.cmd_set_used",
        lambda pct, sf, um: calls.append(("set", pct)),
    )
    monkeypatch.setattr(
        "app.usage_estimator.cmd_reset_session",
        lambda sf, um: calls.append(("reset",)),
    )
    monkeypatch.setattr(
        "app.pause_manager.is_paused", lambda root: False
    )

    async def scenario():
        app = tui.KoanDashboard(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("u")
            await pilot.pause()
            await pilot.press("r")
            await pilot.pause()
            # Click "Full Reset" button (second button)
            buttons = app.screen.query(tui.Button)
            reset_btn = [b for b in buttons if b.id == "reset"][0]
            reset_btn.press()
            await pilot.pause()

    asyncio.run(scenario())
    assert calls == [("reset",)]


def test_pilot_reset_quota_invalid_input(tmp_path, monkeypatch):
    _write_usage(tmp_path, 50, 50)
    calls = []
    monkeypatch.setattr(
        "app.usage_estimator.cmd_set_used",
        lambda pct, sf, um: calls.append(("set", pct)),
    )
    monkeypatch.setattr(
        "app.usage_estimator.cmd_reset_session",
        lambda sf, um: calls.append(("reset",)),
    )
    monkeypatch.setattr(
        "app.pause_manager.is_paused", lambda root: False
    )

    async def scenario():
        app = tui.KoanDashboard(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("u")
            await pilot.pause()
            await pilot.press("r")
            await pilot.pause()
            app.screen.query_one("#value", tui.Input).value = "abc"
            await pilot.press("enter")
            await pilot.pause()

    asyncio.run(scenario())
    assert calls == []  # no update for invalid input


def test_refresh_on_usage_tab_triggers_reset_modal(tmp_path, monkeypatch):
    _write_usage(tmp_path, 10, 20)
    modal_shown = []
    original_reset_quota = tui.KoanDashboard.action_reset_quota

    def fake_reset_quota(self):
        modal_shown.append(True)

    monkeypatch.setattr(tui.KoanDashboard, "action_reset_quota", fake_reset_quota)

    async def scenario():
        app = tui.KoanDashboard(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("u")
            await pilot.pause()
            await pilot.press("r")
            await pilot.pause()

    asyncio.run(scenario())
    assert modal_shown == [True]


# --- usage tab: last_action + duration parity -----------------------------

def test_pilot_usage_shows_last_action_and_duration(tmp_path, monkeypatch):
    _write_config(tmp_path, "x: 1\n")
    inst = tmp_path / "instance"
    (inst / "usage.md").write_text(
        "Session (5hr) : 25% (reset in 3h)\n"
        "Weekly (7 day) : 60% (resets in 3d)\n"
    )
    (inst / "session_outcomes.json").write_text(
        '[{"timestamp": "2026-06-08T12:00:00", "project": "koan", "mode": "implement",'
        ' "duration_minutes": 42, "outcome": "productive", "last_action": "Edit"}]'
    )

    async def scenario():
        app = tui.KoanDashboard(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("u")
            await pilot.pause()
            body = app.query_one("#usage-body", tui.Static)
            rendered = body.render()
            text = getattr(rendered, "plain", str(rendered))
            assert "Last      Edit" in text
            assert "Duration  42 min" in text

    asyncio.run(scenario())


def test_pilot_usage_hides_last_action_when_empty(tmp_path, monkeypatch):
    _write_config(tmp_path, "x: 1\n")
    inst = tmp_path / "instance"
    (inst / "usage.md").write_text(
        "Session (5hr) : 25% (reset in 3h)\n"
        "Weekly (7 day) : 60% (resets in 3d)\n"
    )
    (inst / "session_outcomes.json").write_text(
        '[{"timestamp": "2026-06-08T12:00:00", "project": "koan", "mode": "implement",'
        ' "duration_minutes": 7, "outcome": "productive", "last_action": ""}]'
    )

    async def scenario():
        app = tui.KoanDashboard(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("u")
            await pilot.pause()
            body = app.query_one("#usage-body", tui.Static)
            rendered = body.render()
            text = getattr(rendered, "plain", str(rendered))
            assert "Duration  7 min" in text
            assert "Last" not in text

    asyncio.run(scenario())


def test_pilot_usage_hides_duration_when_none(tmp_path, monkeypatch):
    _write_config(tmp_path, "x: 1\n")
    inst = tmp_path / "instance"
    (inst / "usage.md").write_text(
        "Session (5hr) : 25% (reset in 3h)\n"
        "Weekly (7 day) : 60% (resets in 3d)\n"
    )
    (inst / "session_outcomes.json").write_text(
        '[{"timestamp": "2026-06-08T12:00:00", "project": "koan", "mode": "implement",'
        ' "outcome": "productive", "last_action": "Read"}]'
    )

    async def scenario():
        app = tui.KoanDashboard(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("u")
            await pilot.pause()
            body = app.query_one("#usage-body", tui.Static)
            rendered = body.render()
            text = getattr(rendered, "plain", str(rendered))
            assert "Last      Read" in text
            assert "Duration" not in text

    asyncio.run(scenario())


# --- quit confirmation --------------------------------------------------------

def test_active_processes_excludes_dashboard(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.pid_manager.check_pidfile",
        lambda root, name: 123 if name in ("run", "awake", "api") else None,
    )
    app = tui.KoanDashboard(tmp_path)
    assert app._active_processes() == ["run", "awake", "api"]


def test_quit_confirmation_default_when_nothing_active(tmp_path, monkeypatch):
    monkeypatch.setattr("app.pid_manager.check_pidfile", lambda root, name: None)
    app = tui.KoanDashboard(tmp_path)
    captured = {}

    def _capture(screen, callback=None):
        captured["screen"] = screen

    app.push_screen = _capture
    app.action_request_quit()
    assert "agent + bridge" in captured["screen"]._message
    assert "Active processes" not in captured["screen"]._message
    assert "In progress" not in captured["screen"]._message


def test_quit_confirmation_shows_active_processes(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.pid_manager.check_pidfile",
        lambda root, name: 123 if name == "run" else None,
    )
    app = tui.KoanDashboard(tmp_path)
    captured = {}

    def _capture(screen, callback=None):
        captured["screen"] = screen

    app.push_screen = _capture
    app.action_request_quit()
    assert "Active processes: run" in captured["screen"]._message


def test_quit_confirmation_shows_in_progress_missions(tmp_path, monkeypatch):
    _write_config(tmp_path, "x: 1\n")
    inst = tmp_path / "instance"
    (inst / "missions.md").write_text(
        "# Missions\n\n## Pending\n\n## In Progress\n\n"
        "- mission alpha\n- mission beta\n\n## Done\n"
    )
    monkeypatch.setattr("app.pid_manager.check_pidfile", lambda root, name: None)
    app = tui.KoanDashboard(tmp_path)
    captured = {}

    def _capture(screen, callback=None):
        captured["screen"] = screen

    app.push_screen = _capture
    app.action_request_quit()
    msg = captured["screen"]._message
    assert "In progress (2):" in msg
    assert "mission alpha" in msg
    assert "mission beta" in msg


def test_quit_confirmation_caps_missions_at_five(tmp_path, monkeypatch):
    _write_config(tmp_path, "x: 1\n")
    inst = tmp_path / "instance"
    missions = "\n".join(f"- mission {i}" for i in range(7))
    (inst / "missions.md").write_text(
        f"# Missions\n\n## Pending\n\n## In Progress\n\n{missions}\n\n## Done\n"
    )
    monkeypatch.setattr("app.pid_manager.check_pidfile", lambda root, name: None)
    app = tui.KoanDashboard(tmp_path)
    captured = {}

    def _capture(screen, callback=None):
        captured["screen"] = screen

    app.push_screen = _capture
    app.action_request_quit()
    msg = captured["screen"]._message
    assert "In progress (7):" in msg
    assert "… +2 more" in msg
    # Only first 5 listed explicitly.
    assert msg.count("mission ") == 5


def test_quit_confirmation_shows_both_processes_and_missions(tmp_path, monkeypatch):
    _write_config(tmp_path, "x: 1\n")
    inst = tmp_path / "instance"
    (inst / "missions.md").write_text(
        "# Missions\n\n## Pending\n\n## In Progress\n\n"
        "- mission one\n\n## Done\n"
    )
    monkeypatch.setattr(
        "app.pid_manager.check_pidfile",
        lambda root, name: 123 if name in ("run", "awake") else None,
    )
    app = tui.KoanDashboard(tmp_path)
    captured = {}

    def _capture(screen, callback=None):
        captured["screen"] = screen

    app.push_screen = _capture
    app.action_request_quit()
    msg = captured["screen"]._message
    assert "Active processes: run, awake" in msg
    assert "In progress (1):" in msg
    assert "mission one" in msg


# --- double CTRL-C quit -------------------------------------------------------

def test_first_ctrl_c_shows_notification(tmp_path, monkeypatch):
    """First CTRL-C shows a notification, does not exit."""
    monkeypatch.setattr("app.pid_manager.check_pidfile", lambda root, name: None)
    app = tui.KoanDashboard(tmp_path)
    notifications = []
    app.notify = lambda msg, **kw: notifications.append((msg, kw))
    app.exit = lambda *a, **kw: pytest.fail("should not exit on first CTRL-C")

    app.action_help_quit()

    assert len(notifications) == 1
    assert "Ctrl-C" in notifications[0][0]
    assert notifications[0][1].get("title") == "Stop Kōan?"
    assert app._last_interrupt_at > 0


def test_double_ctrl_c_exits(tmp_path, monkeypatch):
    """Second CTRL-C within the window exits the app."""
    monkeypatch.setattr("app.pid_manager.check_pidfile", lambda root, name: None)
    app = tui.KoanDashboard(tmp_path)
    notifications = []
    exited = []
    app.notify = lambda msg, **kw: notifications.append((msg, kw))
    app.exit = lambda *a, **kw: exited.append(True)

    app.action_help_quit()
    assert len(notifications) == 1
    assert not exited

    app.action_help_quit()
    assert exited
    assert not app._detached


def test_ctrl_c_after_window_resets(tmp_path, monkeypatch):
    """CTRL-C after the window expires shows a new notification instead of exiting."""
    monkeypatch.setattr("app.pid_manager.check_pidfile", lambda root, name: None)
    app = tui.KoanDashboard(tmp_path)
    notifications = []
    exited = []
    app.notify = lambda msg, **kw: notifications.append((msg, kw))
    app.exit = lambda *a, **kw: exited.append(True)

    app._last_interrupt_at = time.monotonic() - tui.KoanDashboard._INTERRUPT_WINDOW - 1
    app.action_help_quit()

    assert len(notifications) == 1
    assert not exited


def test_no_duplicate_notification_within_window(tmp_path, monkeypatch):
    """Within the window, second CTRL-C quits rather than showing a duplicate notification."""
    monkeypatch.setattr("app.pid_manager.check_pidfile", lambda root, name: None)
    app = tui.KoanDashboard(tmp_path)
    notifications = []
    exited = []
    app.notify = lambda msg, **kw: notifications.append((msg, kw))
    app.exit = lambda *a, **kw: exited.append(True)

    app.action_help_quit()
    app.action_help_quit()

    assert len(notifications) == 1
    assert exited
