"""Tests for file-based system prompt delivery.

Verifies that ``build_full_command_managed`` routes the system prompt through
a 0600 temp file on supporting providers, keeping the prompt out of ``argv``
(and therefore out of ``ps`` listings and process supervisors).
"""

import os
import stat
from unittest.mock import patch

from app.provider import (
    ClaudeProvider,
    ClineProvider,
    CodexProvider,
    OllamaLaunchProvider,
    build_full_command,
    build_full_command_managed,
    cleanup_managed_paths,
)


class TestProviderCapabilityFlag:
    """Each provider must declare whether it supports file-mode system prompts."""

    def test_claude_supports_file_mode(self):
        assert ClaudeProvider().supports_system_prompt_file() is True

    def test_cline_does_not_support_file_mode(self):
        assert ClineProvider().supports_system_prompt_file() is False

    def test_codex_does_not_support_file_mode(self):
        assert CodexProvider().supports_system_prompt_file() is False

    def test_ollama_launch_supports_file_mode(self):
        assert OllamaLaunchProvider().supports_system_prompt_file() is True


class TestOllamaLaunchFileModeArgs:
    """OllamaLaunchProvider inherits file-mode support from ClaudeProvider."""

    def test_file_flag_with_path(self):
        p = OllamaLaunchProvider()
        assert p.build_system_prompt_file_args("/tmp/x.txt") == [
            "--append-system-prompt-file",
            "/tmp/x.txt",
        ]

    def test_file_flag_empty_path_yields_empty(self):
        p = OllamaLaunchProvider()
        assert p.build_system_prompt_file_args("") == []


class TestClaudeFileModeArgs:
    """Claude provider should emit --append-system-prompt-file when given a path."""

    def test_file_flag_with_path(self):
        p = ClaudeProvider()
        assert p.build_system_prompt_file_args("/tmp/x.txt") == [
            "--append-system-prompt-file",
            "/tmp/x.txt",
        ]

    def test_file_flag_empty_path_yields_empty(self):
        p = ClaudeProvider()
        assert p.build_system_prompt_file_args("") == []


class TestBuildCommandFilePrecedence:
    """When system_prompt_file is set, the provider must use it (not argv)."""

    def test_file_takes_precedence_over_inline_content(self, tmp_path):
        f = tmp_path / "prompt.txt"
        f.write_text("file content")

        cmd = ClaudeProvider().build_command(
            prompt="user question",
            system_prompt="should not appear",
            system_prompt_file=str(f),
        )

        assert "--append-system-prompt-file" in cmd
        idx = cmd.index("--append-system-prompt-file")
        assert cmd[idx + 1] == str(f)

        # Inline content path is bypassed completely.
        assert "--append-system-prompt" not in cmd[:idx] + cmd[idx + 2 :]
        assert "should not appear" not in cmd

    def test_argv_used_when_file_unset(self):
        cmd = ClaudeProvider().build_command(
            prompt="user question",
            system_prompt="legacy inline content",
        )
        assert "--append-system-prompt" in cmd


class TestBuildFullCommandManagedFileMode:
    """build_full_command_managed writes the system prompt to a temp file."""

    @patch("app.config.get_skip_permissions", return_value=False)
    def test_writes_file_and_returns_cleanup_path(self, _mock_perm):
        # Force Claude provider for the test (capability matters, not env).
        with patch("app.provider.get_provider", return_value=ClaudeProvider()):
            cmd, paths = build_full_command_managed(
                prompt="user question",
                system_prompt="STABLE SYSTEM PROMPT CONTENT",
            )

        assert len(paths) == 1
        path = paths[0]
        try:
            # File flag is used, content does NOT appear in argv.
            assert "--append-system-prompt-file" in cmd
            assert "STABLE SYSTEM PROMPT CONTENT" not in cmd

            idx = cmd.index("--append-system-prompt-file")
            assert cmd[idx + 1] == path

            # Content was written to the file.
            with open(path) as f:
                assert f.read() == "STABLE SYSTEM PROMPT CONTENT"

            # File is private (0600 on POSIX).
            mode = stat.S_IMODE(os.stat(path).st_mode)
            assert mode == 0o600, f"expected 0600, got {oct(mode)}"
        finally:
            cleanup_managed_paths(paths)

    @patch("app.config.get_skip_permissions", return_value=False)
    def test_no_temp_file_when_system_prompt_empty(self, _mock_perm):
        with patch("app.provider.get_provider", return_value=ClaudeProvider()):
            cmd, paths = build_full_command_managed(
                prompt="user question",
                system_prompt="",
            )
        assert paths == []
        assert "--append-system-prompt-file" not in cmd
        assert "--append-system-prompt" not in cmd

    @patch("app.config.get_skip_permissions", return_value=False)
    def test_no_temp_file_when_provider_lacks_support(self, _mock_perm):
        with patch("app.provider.get_provider", return_value=CodexProvider()):
            cmd, paths = build_full_command_managed(
                prompt="user question",
                system_prompt="inline content",
            )
        # No temp file, no file flag — content is prepended to user prompt instead.
        assert paths == []
        assert "--append-system-prompt-file" not in cmd
        # Codex prepends system prompt to user prompt (existing fallback).
        assert any("inline content" in arg for arg in cmd)

    def test_cleanup_managed_paths_removes_files(self, tmp_path):
        p1 = tmp_path / "a.txt"
        p1.write_text("a")
        p2 = tmp_path / "b.txt"
        p2.write_text("b")

        cleanup_managed_paths([str(p1), str(p2)])

        assert not p1.exists()
        assert not p2.exists()

    def test_cleanup_managed_paths_ignores_missing(self, tmp_path):
        # Must not raise even when the file is already gone.
        cleanup_managed_paths([str(tmp_path / "never-existed.txt")])


class TestArgvLeakSurface:
    """Regression: the system prompt content must not be in argv when using
    file mode. This is the core privacy property the file-mode plumbing
    delivers — verify it directly so a future refactor can't silently
    regress it.
    """

    @patch("app.config.get_skip_permissions", return_value=False)
    def test_prompt_content_absent_from_argv(self, _mock_perm):
        secret = "DO-NOT-LEAK-VIA-PS-SENTINEL-7f3"
        with patch("app.provider.get_provider", return_value=ClaudeProvider()):
            cmd, paths = build_full_command_managed(
                prompt="user question",
                system_prompt=secret,
            )
        try:
            argv_blob = " ".join(cmd)
            assert secret not in argv_blob
        finally:
            cleanup_managed_paths(paths)

    @patch("app.config.get_skip_permissions", return_value=False)
    def test_build_full_command_legacy_still_works_with_inline_system_prompt(
        self, _mock_perm
    ):
        """build_full_command (non-managed) preserves the legacy argv path
        for callers that haven't migrated."""
        with patch("app.provider.get_provider", return_value=ClaudeProvider()):
            cmd = build_full_command(
                prompt="user question",
                system_prompt="inline",
            )
        assert "--append-system-prompt" in cmd
        assert "inline" in cmd
