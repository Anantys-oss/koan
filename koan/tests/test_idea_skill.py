"""Tests for the /idea core skill — ideas backlog management."""

import re
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from app.missions import (
    parse_ideas,
    insert_idea,
    delete_idea,
    promote_idea,
)
from app.skills import SkillContext


# ---------------------------------------------------------------------------
# missions.py — parse_ideas
# ---------------------------------------------------------------------------

class TestParseIdeas:
    def test_empty_content(self):
        assert parse_ideas("") == []

    def test_no_ideas_section(self):
        content = "# Missions\n\n## Pending\n\n- task\n\n## Done\n"
        assert parse_ideas(content) == []

    def test_empty_ideas_section(self):
        content = "# Missions\n\n## Ideas\n\n## Pending\n\n## Done\n"
        assert parse_ideas(content) == []

    def test_single_idea(self):
        content = "# Missions\n\n## Ideas\n\n- my idea\n\n## Pending\n"
        assert parse_ideas(content) == ["- my idea"]

    def test_multiple_ideas(self):
        content = textwrap.dedent("""\
            # Missions

            ## Ideas

            - idea one
            - idea two
            - idea three

            ## Pending
        """)
        ideas = parse_ideas(content)
        assert len(ideas) == 3
        assert ideas[0] == "- idea one"
        assert ideas[2] == "- idea three"

    def test_ideas_with_project_tags(self):
        content = textwrap.dedent("""\
            # Missions

            ## Ideas

            - [project:koan] fix something
            - plain idea
            - [project:webapp] add feature

            ## Pending
        """)
        ideas = parse_ideas(content)
        assert len(ideas) == 3
        assert "[project:koan]" in ideas[0]

    def test_case_insensitive_header(self):
        content = "# Missions\n\n## ideas\n\n- lower case\n\n## Pending\n"
        assert parse_ideas(content) == ["- lower case"]

    def test_ideas_at_end_of_file(self):
        """Ideas section at end of file with no following section."""
        content = "# Missions\n\n## Pending\n\n## Done\n\n## Ideas\n\n- last idea\n"
        assert parse_ideas(content) == ["- last idea"]

    def test_ideas_section_not_confused_with_pending(self):
        """Items in Ideas should NOT appear in parse_sections."""
        from app.missions import parse_sections
        content = textwrap.dedent("""\
            # Missions

            ## Ideas

            - idea item
            - another idea

            ## Pending

            - pending item

            ## In Progress

            ## Done
        """)
        sections = parse_sections(content)
        ideas = parse_ideas(content)
        assert len(ideas) == 2
        assert len(sections["pending"]) == 1
        assert "idea item" not in str(sections["pending"])

    def test_multiline_idea(self):
        """Ideas with continuation lines are captured as single items."""
        content = textwrap.dedent("""\
            # Missions

            ## Ideas

            - main idea line
              continuation detail
              more detail

            ## Pending
        """)
        ideas = parse_ideas(content)
        assert len(ideas) == 1
        assert "main idea line" in ideas[0]
        assert "continuation detail" in ideas[0]
        assert "more detail" in ideas[0]

    def test_multiline_mixed_with_simple(self):
        """Multi-line and single-line ideas coexist."""
        content = textwrap.dedent("""\
            # Missions

            ## Ideas

            - multi-line idea
              with details
            - simple idea
            - another multi
              also with details

            ## Pending
        """)
        ideas = parse_ideas(content)
        assert len(ideas) == 3
        assert "\n" in ideas[0]  # multi-line
        assert "\n" not in ideas[1]  # single-line
        assert "\n" in ideas[2]  # multi-line

    def test_multiline_idea_blank_line_separator(self):
        """Blank lines between ideas don't become continuations."""
        content = textwrap.dedent("""\
            # Missions

            ## Ideas

            - idea one

            - idea two

            ## Pending
        """)
        ideas = parse_ideas(content)
        assert len(ideas) == 2
        assert ideas[0] == "- idea one"
        assert ideas[1] == "- idea two"


# ---------------------------------------------------------------------------
# missions.py — insert_idea
# ---------------------------------------------------------------------------

class TestInsertIdea:
    def test_insert_into_existing_section(self):
        content = "# Missions\n\n## Ideas\n\n- existing\n\n## Pending\n\n## Done\n"
        result = insert_idea(content, "- new idea")
        ideas = parse_ideas(result)
        assert len(ideas) == 2
        assert ideas[0] == "- existing"
        assert ideas[1] == "- new idea"

    def test_insert_appends_at_bottom(self):
        """New ideas are added at the bottom, not the top."""
        content = "# Missions\n\n## Ideas\n\n- first\n- second\n\n## Pending\n"
        result = insert_idea(content, "- third")
        ideas = parse_ideas(result)
        assert ideas == ["- first", "- second", "- third"]

    def test_insert_multiple_preserves_order(self):
        """Successive inserts maintain chronological order (oldest first)."""
        content = "# Missions\n\n## Ideas\n\n## Pending\n"
        content = insert_idea(content, "- idea A")
        content = insert_idea(content, "- idea B")
        content = insert_idea(content, "- idea C")
        ideas = parse_ideas(content)
        assert ideas == ["- idea A", "- idea B", "- idea C"]

    def test_insert_after_multiline_idea(self):
        """New idea goes after multi-line continuation lines."""
        content = (
            "# Missions\n\n## Ideas\n\n"
            "- multi-line idea\n  continuation line\n\n## Pending\n"
        )
        result = insert_idea(content, "- new idea")
        ideas = parse_ideas(result)
        assert len(ideas) == 2
        assert ideas[1] == "- new idea"

    def test_insert_creates_section(self):
        content = "# Missions\n\n## Pending\n\n## Done\n"
        result = insert_idea(content, "- first idea")
        assert "## Ideas" in result
        ideas = parse_ideas(result)
        assert ideas == ["- first idea"]

    def test_insert_preserves_pending(self):
        content = "# Missions\n\n## Pending\n\n- task\n\n## Done\n"
        result = insert_idea(content, "- idea")
        from app.missions import parse_sections
        sections = parse_sections(result)
        assert len(sections["pending"]) == 1

    def test_insert_empty_content(self):
        result = insert_idea("", "- idea from nothing")
        assert "## Ideas" in result
        ideas = parse_ideas(result)
        assert ideas == ["- idea from nothing"]


# ---------------------------------------------------------------------------
# missions.py — delete_idea
# ---------------------------------------------------------------------------

class TestDeleteIdea:
    def test_delete_first(self):
        content = textwrap.dedent("""\
            # Missions

            ## Ideas

            - idea one
            - idea two
            - idea three

            ## Pending
        """)
        result, deleted = delete_idea(content, 1)
        assert deleted == "- idea one"
        ideas = parse_ideas(result)
        assert len(ideas) == 2
        assert "- idea one" not in ideas

    def test_delete_last(self):
        content = "# Missions\n\n## Ideas\n\n- a\n- b\n- c\n\n## Pending\n"
        result, deleted = delete_idea(content, 3)
        assert deleted == "- c"
        assert len(parse_ideas(result)) == 2

    def test_delete_out_of_range(self):
        content = "# Missions\n\n## Ideas\n\n- only one\n\n## Pending\n"
        result, deleted = delete_idea(content, 2)
        assert deleted is None
        assert result == content

    def test_delete_zero_index(self):
        content = "# Missions\n\n## Ideas\n\n- item\n\n## Pending\n"
        result, deleted = delete_idea(content, 0)
        assert deleted is None

    def test_delete_negative_index(self):
        content = "# Missions\n\n## Ideas\n\n- item\n\n## Pending\n"
        result, deleted = delete_idea(content, -1)
        assert deleted is None

    def test_delete_multiline_idea(self):
        """Deleting a multi-line idea removes all continuation lines."""
        content = textwrap.dedent("""\
            # Missions

            ## Ideas

            - multi-line idea
              continuation line 1
              continuation line 2
            - keep this

            ## Pending
        """)
        result, deleted = delete_idea(content, 1)
        assert "multi-line idea" in deleted
        assert "continuation line 1" in deleted
        ideas = parse_ideas(result)
        assert len(ideas) == 1
        assert ideas[0] == "- keep this"
        assert "continuation line" not in result

    def test_delete_second_multiline_idea(self):
        """Delete the second multi-line idea, keep the first."""
        content = textwrap.dedent("""\
            # Missions

            ## Ideas

            - first idea
              first details
            - second idea
              second details

            ## Pending
        """)
        result, deleted = delete_idea(content, 2)
        assert "second idea" in deleted
        assert "second details" in deleted
        ideas = parse_ideas(result)
        assert len(ideas) == 1
        assert "first idea" in ideas[0]
        assert "first details" in ideas[0]


# ---------------------------------------------------------------------------
# missions.py — promote_idea
# ---------------------------------------------------------------------------

class TestPromoteIdea:
    def test_promote_moves_to_pending(self):
        content = textwrap.dedent("""\
            # Missions

            ## Ideas

            - idea to promote
            - keep this

            ## Pending

            ## In Progress

            ## Done
        """)
        result, promoted = promote_idea(content, 1)
        assert promoted == "- idea to promote"

        from app.missions import parse_sections
        ideas = parse_ideas(result)
        sections = parse_sections(result)
        assert len(ideas) == 1
        assert "- keep this" in ideas[0]
        assert len(sections["pending"]) == 1
        assert "idea to promote" in sections["pending"][0]

    def test_promote_out_of_range(self):
        content = "# Missions\n\n## Ideas\n\n- only\n\n## Pending\n\n## Done\n"
        result, promoted = promote_idea(content, 5)
        assert promoted is None
        assert result == content

    def test_promote_with_project_tag(self):
        content = textwrap.dedent("""\
            # Missions

            ## Ideas

            - [project:koan] tagged idea

            ## Pending

            ## In Progress

            ## Done
        """)
        result, promoted = promote_idea(content, 1)
        assert promoted == "- [project:koan] tagged idea"
        from app.missions import parse_sections
        sections = parse_sections(result)
        assert "[project:koan]" in sections["pending"][0]

    def test_promote_multiline_idea(self):
        """Promoting a multi-line idea moves all lines to pending."""
        content = textwrap.dedent("""\
            # Missions

            ## Ideas

            - [project:koan] implement feature X
              Step 1: do this
              Step 2: do that
            - keep this idea

            ## Pending

            ## In Progress

            ## Done
        """)
        result, promoted = promote_idea(content, 1)
        assert "implement feature X" in promoted
        assert "Step 1: do this" in promoted
        assert "Step 2: do that" in promoted

        ideas = parse_ideas(result)
        assert len(ideas) == 1
        assert "keep this idea" in ideas[0]

        from app.missions import parse_sections
        sections = parse_sections(result)
        assert any("implement feature X" in p for p in sections["pending"])

    def test_promote_with_french_section_headers(self):
        """Promote works when missions.md uses French headers."""
        content = textwrap.dedent("""\
            # Missions

            ## Ideas

            - my idea

            ## Pending

            - existing pending

            ## In Progress

            ## Done
        """)
        result, promoted = promote_idea(content, 1)
        assert promoted == "- my idea"

        ideas = parse_ideas(result)
        assert len(ideas) == 0

        from app.missions import parse_sections
        sections = parse_sections(result)
        assert len(sections["pending"]) == 2
        assert any("my idea" in p for p in sections["pending"])
        assert any("existing pending" in p for p in sections["pending"])

    def test_promote_preserves_other_ideas(self):
        """Only the promoted idea is removed; others stay."""
        content = textwrap.dedent("""\
            # Missions

            ## Ideas

            - idea A
            - idea B
            - idea C

            ## Pending

            ## Done
        """)
        result, promoted = promote_idea(content, 2)
        assert promoted == "- idea B"
        ideas = parse_ideas(result)
        assert len(ideas) == 2
        assert ideas[0] == "- idea A"
        assert ideas[1] == "- idea C"

    def test_promote_last_idea(self):
        """Promoting the last idea empties the Ideas section."""
        content = "# Missions\n\n## Ideas\n\n- sole idea\n\n## Pending\n\n## Done\n"
        result, promoted = promote_idea(content, 1)
        assert promoted == "- sole idea"
        assert len(parse_ideas(result)) == 0
        from app.missions import parse_sections
        assert any("sole idea" in p for p in parse_sections(result)["pending"])


# ---------------------------------------------------------------------------
# promote_all_ideas() unit tests
# ---------------------------------------------------------------------------

class TestPromoteAllIdeas:
    def test_promote_all_empty(self):
        """Promoting all with no ideas returns empty list."""
        from app.missions import promote_all_ideas
        content = "# Missions\n\n## Ideas\n\n## Pending\n\n## Done\n"
        result, promoted = promote_all_ideas(content)
        assert promoted == []
        assert result == content

    def test_promote_all_single(self):
        """Promoting all with one idea moves it to pending."""
        from app.missions import promote_all_ideas
        content = "# Missions\n\n## Ideas\n\n- only idea\n\n## Pending\n\n## Done\n"
        result, promoted = promote_all_ideas(content)
        assert len(promoted) == 1
        assert "only idea" in promoted[0]
        assert len(parse_ideas(result)) == 0
        from app.missions import parse_sections
        sections = parse_sections(result)
        assert any("only idea" in p for p in sections["pending"])

    def test_promote_all_multiple(self):
        """Promoting all moves every idea to pending in order."""
        from app.missions import promote_all_ideas
        content = textwrap.dedent("""\
            # Missions

            ## Ideas

            - idea A
            - idea B
            - idea C

            ## Pending

            ## Done
        """)
        result, promoted = promote_all_ideas(content)
        assert len(promoted) == 3
        assert "idea A" in promoted[0]
        assert "idea B" in promoted[1]
        assert "idea C" in promoted[2]
        assert len(parse_ideas(result)) == 0
        from app.missions import parse_sections
        sections = parse_sections(result)
        pending_text = "\n".join(sections["pending"])
        assert "idea A" in pending_text
        assert "idea B" in pending_text
        assert "idea C" in pending_text

    def test_promote_all_with_project_tags(self):
        """Project tags are preserved when promoting all ideas."""
        from app.missions import promote_all_ideas
        content = textwrap.dedent("""\
            # Missions

            ## Ideas

            - [project:koan] fix tests
            - [project:web] add auth

            ## Pending

            ## Done
        """)
        result, promoted = promote_all_ideas(content)
        assert len(promoted) == 2
        assert "[project:koan]" in promoted[0]
        assert "[project:web]" in promoted[1]
        from app.missions import parse_sections
        sections = parse_sections(result)
        pending_text = "\n".join(sections["pending"])
        assert "[project:koan]" in pending_text
        assert "[project:web]" in pending_text

    def test_promote_all_with_existing_pending(self):
        """Promoting all ideas preserves existing pending missions."""
        from app.missions import promote_all_ideas
        content = textwrap.dedent("""\
            # Missions

            ## Ideas

            - new idea

            ## Pending

            - existing task

            ## Done
        """)
        result, promoted = promote_all_ideas(content)
        assert len(promoted) == 1
        from app.missions import parse_sections
        sections = parse_sections(result)
        pending_text = "\n".join(sections["pending"])
        assert "existing task" in pending_text
        assert "new idea" in pending_text


# ---------------------------------------------------------------------------
# Handler tests (direct handler invocation)
# ---------------------------------------------------------------------------

class TestIdeaHandler:
    def _make_ctx(self, tmp_path, missions_content=None, command="idea", args=""):
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir(exist_ok=True)
        if missions_content is not None:
            (instance_dir / "missions.md").write_text(missions_content)
        return SkillContext(
            koan_root=tmp_path,
            instance_dir=instance_dir,
            command_name=command,
            args=args,
        )

    def test_no_missions_file(self, tmp_path):
        from skills.core.idea.handler import handle

        ctx = self._make_ctx(tmp_path, command="idea")
        result = handle(ctx)
        assert "No ideas" in result or "No missions file" in result

    def test_list_empty(self, tmp_path):
        from skills.core.idea.handler import handle

        ctx = self._make_ctx(
            tmp_path,
            "# Missions\n\n## Ideas\n\n## Pending\n\n## Done\n",
            command="idea",
        )
        result = handle(ctx)
        assert "No ideas" in result

    def test_list_with_ideas(self, tmp_path):
        from skills.core.idea.handler import handle

        content = textwrap.dedent("""\
            # Missions

            ## Ideas

            - first idea
            - second idea

            ## Pending

            ## Done
        """)
        ctx = self._make_ctx(tmp_path, content, command="idea")
        result = handle(ctx)
        assert "IDEAS" in result
        assert "1. first idea" in result
        assert "2. second idea" in result

    def test_ideas_command_always_lists(self, tmp_path):
        from skills.core.idea.handler import handle

        content = "# Missions\n\n## Ideas\n\n- item\n\n## Pending\n\n## Done\n"
        ctx = self._make_ctx(tmp_path, content, command="ideas")
        result = handle(ctx)
        assert "IDEAS" in result
        assert "1. item" in result

    def test_add_idea(self, tmp_path):
        from skills.core.idea.handler import handle

        content = "# Missions\n\n## Ideas\n\n## Pending\n\n## Done\n"
        ctx = self._make_ctx(tmp_path, content, command="idea", args="my new idea")
        result = handle(ctx)
        assert "Idea saved" in result
        assert "my new idea" in result

        # Verify it was written to the file
        written = (tmp_path / "instance" / "missions.md").read_text()
        assert "my new idea" in written

    def test_add_idea_with_project_tag(self, tmp_path):
        from skills.core.idea.handler import handle

        content = "# Missions\n\n## Ideas\n\n## Pending\n\n## Done\n"
        ctx = self._make_ctx(
            tmp_path, content, command="idea",
            args="[project:koan] fix something",
        )
        result = handle(ctx)
        assert "Idea saved" in result

        written = (tmp_path / "instance" / "missions.md").read_text()
        assert "[project:koan]" in written

    def test_buffer_alias_adds(self, tmp_path):
        from skills.core.idea.handler import handle

        content = "# Missions\n\n## Ideas\n\n## Pending\n\n## Done\n"
        ctx = self._make_ctx(tmp_path, content, command="buffer", args="buffer idea")
        result = handle(ctx)
        assert "Idea saved" in result

    def test_delete_idea(self, tmp_path):
        from skills.core.idea.handler import handle

        content = "# Missions\n\n## Ideas\n\n- to delete\n- to keep\n\n## Pending\n\n## Done\n"
        ctx = self._make_ctx(tmp_path, content, command="idea", args="delete 1")
        result = handle(ctx)
        assert "Deleted" in result
        assert "to delete" in result

        written = (tmp_path / "instance" / "missions.md").read_text()
        assert "to delete" not in written
        assert "to keep" in written

    def test_delete_with_rm_alias(self, tmp_path):
        from skills.core.idea.handler import handle

        content = "# Missions\n\n## Ideas\n\n- item\n\n## Pending\n\n## Done\n"
        ctx = self._make_ctx(tmp_path, content, command="idea", args="rm 1")
        result = handle(ctx)
        assert "Deleted" in result

    def test_delete_invalid_index(self, tmp_path):
        from skills.core.idea.handler import handle

        content = "# Missions\n\n## Ideas\n\n- only one\n\n## Pending\n\n## Done\n"
        ctx = self._make_ctx(tmp_path, content, command="idea", args="delete 5")
        result = handle(ctx)
        assert "Invalid index" in result

    def test_promote_idea(self, tmp_path):
        from skills.core.idea.handler import handle

        content = textwrap.dedent("""\
            # Missions

            ## Ideas

            - promote me

            ## Pending

            ## In Progress

            ## Done
        """)
        ctx = self._make_ctx(tmp_path, content, command="idea", args="promote 1")
        result = handle(ctx)
        assert "Promoted to pending" in result
        assert "promote me" in result

        written = (tmp_path / "instance" / "missions.md").read_text()
        ideas = parse_ideas(written)
        assert len(ideas) == 0
        from app.missions import parse_sections
        sections = parse_sections(written)
        assert any("promote me" in p for p in sections["pending"])

    def test_promote_with_push_alias(self, tmp_path):
        from skills.core.idea.handler import handle

        content = "# Missions\n\n## Ideas\n\n- pushme\n\n## Pending\n\n## In Progress\n\n## Done\n"
        ctx = self._make_ctx(tmp_path, content, command="idea", args="push 1")
        result = handle(ctx)
        assert "Promoted to pending" in result

    def test_promote_invalid_index(self, tmp_path):
        from skills.core.idea.handler import handle

        content = "# Missions\n\n## Ideas\n\n- only\n\n## Pending\n\n## Done\n"
        ctx = self._make_ctx(tmp_path, content, command="idea", args="promote 99")
        result = handle(ctx)
        assert "Invalid index" in result

    def test_promote_multiline_via_handler(self, tmp_path):
        """Handler correctly promotes multi-line ideas."""
        from skills.core.idea.handler import handle

        content = textwrap.dedent("""\
            # Missions

            ## Ideas

            - implement /plan skill
              /plan can be used with one idea or a project
              Like /plan This is an idea
            - keep this idea

            ## Pending

            ## In Progress

            ## Done
        """)
        ctx = self._make_ctx(tmp_path, content, command="idea", args="promote 1")
        result = handle(ctx)
        assert "Promoted to pending" in result
        assert "implement /plan skill" in result

        written = (tmp_path / "instance" / "missions.md").read_text()
        ideas = parse_ideas(written)
        assert len(ideas) == 1
        assert "keep this idea" in ideas[0]

        from app.missions import parse_sections
        sections = parse_sections(written)
        assert any("implement /plan skill" in p for p in sections["pending"])

    def test_promote_with_french_headers_via_handler(self, tmp_path):
        """Handler works with French section headers (## Pending)."""
        from skills.core.idea.handler import handle

        content = textwrap.dedent("""\
            # Missions

            ## Ideas

            - idea for French layout

            ## Pending

            - existing mission

            ## In Progress

            ## Done
        """)
        ctx = self._make_ctx(tmp_path, content, command="idea", args="promote 1")
        result = handle(ctx)
        assert "Promoted to pending" in result

        written = (tmp_path / "instance" / "missions.md").read_text()
        assert len(parse_ideas(written)) == 0
        from app.missions import parse_sections
        sections = parse_sections(written)
        assert len(sections["pending"]) == 2

    def test_promote_no_ideas_via_handler(self, tmp_path):
        """Promote on empty ideas section returns informative message."""
        from skills.core.idea.handler import handle

        content = "# Missions\n\n## Ideas\n\n## Pending\n\n## Done\n"
        ctx = self._make_ctx(tmp_path, content, command="idea", args="promote 1")
        result = handle(ctx)
        assert "No ideas to promote" in result

    def test_promote_all_via_handler(self, tmp_path):
        """Handler promotes all ideas at once."""
        from skills.core.idea.handler import handle

        content = textwrap.dedent("""\
            # Missions

            ## Ideas

            - idea one
            - idea two
            - idea three

            ## Pending

            ## In Progress

            ## Done
        """)
        ctx = self._make_ctx(tmp_path, content, command="idea", args="promote all")
        result = handle(ctx)
        assert "Promoted 3 ideas" in result
        assert "idea one" in result
        assert "idea two" in result
        assert "idea three" in result

        written = (tmp_path / "instance" / "missions.md").read_text()
        assert len(parse_ideas(written)) == 0
        from app.missions import parse_sections
        sections = parse_sections(written)
        assert len(sections["pending"]) == 3

    def test_promote_all_empty_via_handler(self, tmp_path):
        """Handler returns informative message when no ideas to promote."""
        from skills.core.idea.handler import handle

        content = "# Missions\n\n## Ideas\n\n## Pending\n\n## Done\n"
        ctx = self._make_ctx(tmp_path, content, command="idea", args="promote all")
        result = handle(ctx)
        assert "No ideas to promote" in result

    def test_promote_all_with_push_alias(self, tmp_path):
        """Handler supports 'push all' alias."""
        from skills.core.idea.handler import handle

        content = "# Missions\n\n## Ideas\n\n- single\n\n## Pending\n\n## Done\n"
        ctx = self._make_ctx(tmp_path, content, command="idea", args="push all")
        result = handle(ctx)
        assert "Promoted 1 idea" in result
        assert "ideas" not in result  # singular

    def test_promote_all_single_idea_singular(self, tmp_path):
        """Singular form used when promoting exactly one idea."""
        from skills.core.idea.handler import handle

        content = "# Missions\n\n## Ideas\n\n- lonely idea\n\n## Pending\n\n## Done\n"
        ctx = self._make_ctx(tmp_path, content, command="idea", args="activate all")
        result = handle(ctx)
        assert "Promoted 1 idea to" in result

    def test_delete_multiline_via_handler(self, tmp_path):
        """Handler deletes multi-line ideas completely."""
        from skills.core.idea.handler import handle

        content = textwrap.dedent("""\
            # Missions

            ## Ideas

            - multi-line idea to delete
              with continuation
            - keep this

            ## Pending

            ## Done
        """)
        ctx = self._make_ctx(tmp_path, content, command="idea", args="delete 1")
        result = handle(ctx)
        assert "Deleted" in result

        written = (tmp_path / "instance" / "missions.md").read_text()
        assert "continuation" not in written
        assert "keep this" in written

    def test_list_multiline_ideas_via_handler(self, tmp_path):
        """Handler lists multi-line ideas showing only the first line."""
        from skills.core.idea.handler import handle

        content = textwrap.dedent("""\
            # Missions

            ## Ideas

            - main idea
              extra details here

            ## Pending

            ## Done
        """)
        ctx = self._make_ctx(tmp_path, content, command="idea")
        result = handle(ctx)
        assert "IDEAS" in result
        assert "1. main idea" in result

    @patch("app.utils.get_known_projects", return_value=[("koan", "/p/koan"), ("web", "/p/web")])
    def test_add_idea_auto_detect_project_from_first_word(self, mock_proj, tmp_path):
        """'/idea koan some text' auto-detects koan as project."""
        from skills.core.idea.handler import handle

        content = "# Missions\n\n## Ideas\n\n## Pending\n\n## Done\n"
        ctx = self._make_ctx(tmp_path, content, command="idea", args="koan add retry logic")
        result = handle(ctx)
        assert "Idea saved" in result
        assert "(project: koan)" in result

        written = (tmp_path / "instance" / "missions.md").read_text()
        assert "[project:koan]" in written
        assert "add retry logic" in written
        # The project name should NOT appear in the idea text itself
        assert "- [project:koan] add retry logic" in written

    @patch("app.utils.get_known_projects", return_value=[("koan", "/p/koan"), ("web", "/p/web")])
    def test_add_idea_multi_project_no_project_asks(self, mock_proj, tmp_path):
        """Multi-project setup without project specified prompts user."""
        from skills.core.idea.handler import handle

        content = "# Missions\n\n## Ideas\n\n## Pending\n\n## Done\n"
        ctx = self._make_ctx(tmp_path, content, command="idea", args="fix the login bug")
        result = handle(ctx)
        assert "Which project" in result
        assert "koan" in result
        assert "web" in result
        assert "/idea koan" in result

    @patch("app.utils.get_known_projects", return_value=[("myproj", "/p/myproj")])
    def test_add_idea_single_project_no_prompt(self, mock_proj, tmp_path):
        """Single-project setup saves without prompting."""
        from skills.core.idea.handler import handle

        content = "# Missions\n\n## Ideas\n\n## Pending\n\n## Done\n"
        ctx = self._make_ctx(tmp_path, content, command="idea", args="fix the login bug")
        result = handle(ctx)
        assert "Idea saved" in result
        # Single project → no project tag, no prompt
        assert "Which project" not in result

    @patch("app.utils.get_known_projects", return_value=[("koan", "/p/koan"), ("web", "/p/web")])
    def test_add_idea_explicit_tag_takes_priority(self, mock_proj, tmp_path):
        """Explicit [project:X] tag takes priority over first-word detection."""
        from skills.core.idea.handler import handle

        content = "# Missions\n\n## Ideas\n\n## Pending\n\n## Done\n"
        ctx = self._make_ctx(
            tmp_path, content, command="idea",
            args="[project:web] koan should do something",
        )
        result = handle(ctx)
        assert "Idea saved" in result
        assert "(project: web)" in result

        written = (tmp_path / "instance" / "missions.md").read_text()
        assert "[project:web]" in written
        assert "koan should do something" in written

    @patch("app.utils.get_known_projects", return_value=[("koan", "/p/koan"), ("web", "/p/web")])
    def test_add_idea_case_insensitive_project(self, mock_proj, tmp_path):
        """Project detection is case-insensitive."""
        from skills.core.idea.handler import handle

        content = "# Missions\n\n## Ideas\n\n## Pending\n\n## Done\n"
        ctx = self._make_ctx(tmp_path, content, command="idea", args="Koan fix tests")
        result = handle(ctx)
        assert "Idea saved" in result
        assert "(project: koan)" in result

    @patch("app.utils.get_known_projects", return_value=[("koan", "/p/koan"), ("web", "/p/web")])
    def test_add_idea_project_only_no_text(self, mock_proj, tmp_path):
        """'/idea koan' with no idea text stores empty idea."""
        from skills.core.idea.handler import handle

        content = "# Missions\n\n## Ideas\n\n## Pending\n\n## Done\n"
        ctx = self._make_ctx(tmp_path, content, command="idea", args="koan")
        result = handle(ctx)
        # Should detect project but idea text is empty — still saves
        assert "Idea saved" in result
        assert "(project: koan)" in result

    @patch("app.utils.get_known_projects", return_value=[("koan", "/p/koan")])
    def test_add_idea_project_shown_in_ack_with_explicit_tag(self, mock_proj, tmp_path):
        """Acknowledgment includes project when explicit tag is used."""
        from skills.core.idea.handler import handle

        content = "# Missions\n\n## Ideas\n\n## Pending\n\n## Done\n"
        ctx = self._make_ctx(
            tmp_path, content, command="idea",
            args="[project:koan] improve tests",
        )
        result = handle(ctx)
        assert "(project: koan)" in result
        assert "improve tests" in result


# ---------------------------------------------------------------------------
# clean_mission_display helper
# ---------------------------------------------------------------------------

class TestCleanIdea:
    def test_strip_dash(self):
        from app.missions import clean_mission_display
        assert clean_mission_display("- simple idea") == "simple idea"

    def test_strip_project_tag(self):
        from app.missions import clean_mission_display
        result = clean_mission_display("- [project:koan] fix parser")
        assert result == "[koan] fix parser"

    def test_truncation(self):
        from app.missions import clean_mission_display
        long = "- " + "x" * 200
        result = clean_mission_display(long)
        assert result.endswith("...")
        assert len(result) == 120

    def test_multiline_shows_first_line_only(self):
        from app.missions import clean_mission_display
        result = clean_mission_display("- main idea\n  continuation line\n  more details")
        assert result == "main idea"
        assert "continuation" not in result

    def test_multiline_with_project_tag(self):
        from app.missions import clean_mission_display
        result = clean_mission_display("- [project:koan] main idea\n  details here")
        assert result == "[koan] main idea"
        assert "details" not in result


# ---------------------------------------------------------------------------
# Integration: command routing via awake.py
# ---------------------------------------------------------------------------

class TestIdeaCommandRouting:
    @patch("app.command_handlers.send_telegram")
    def test_idea_routes_via_skill(self, mock_send, tmp_path):
        from app.command_handlers import handle_command

        missions_file = tmp_path / "missions.md"
        missions_file.write_text(
            "# Missions\n\n## Ideas\n\n- test idea\n\n## Pending\n\n## In Progress\n\n## Done\n"
        )
        with patch("app.command_handlers.KOAN_ROOT", tmp_path), \
             patch("app.command_handlers.INSTANCE_DIR", tmp_path), \
             patch("app.command_handlers.MISSIONS_FILE", missions_file):
            handle_command("/idea")
        mock_send.assert_called_once()
        output = mock_send.call_args[0][0]
        assert "IDEAS" in output
        assert "test idea" in output

    @patch("app.command_handlers.send_telegram")
    def test_ideas_routes_via_skill(self, mock_send, tmp_path):
        from app.command_handlers import handle_command

        missions_file = tmp_path / "missions.md"
        missions_file.write_text(
            "# Missions\n\n## Ideas\n\n- listed idea\n\n## Pending\n\n## In Progress\n\n## Done\n"
        )
        with patch("app.command_handlers.KOAN_ROOT", tmp_path), \
             patch("app.command_handlers.INSTANCE_DIR", tmp_path), \
             patch("app.command_handlers.MISSIONS_FILE", missions_file):
            handle_command("/ideas")
        mock_send.assert_called_once()
        assert "listed idea" in mock_send.call_args[0][0]

    @patch("app.command_handlers.send_telegram")
    def test_buffer_routes_via_skill(self, mock_send, tmp_path):
        from app.command_handlers import handle_command

        missions_file = tmp_path / "missions.md"
        missions_file.write_text(
            "# Missions\n\n## Ideas\n\n## Pending\n\n## In Progress\n\n## Done\n"
        )
        with patch("app.command_handlers.KOAN_ROOT", tmp_path), \
             patch("app.command_handlers.INSTANCE_DIR", tmp_path), \
             patch("app.command_handlers.MISSIONS_FILE", missions_file):
            handle_command("/buffer new buffered idea")
        mock_send.assert_called_once()
        assert "Idea saved" in mock_send.call_args[0][0]

    @patch("app.command_handlers.send_telegram")
    def test_idea_appears_in_help(self, mock_send, tmp_path):
        from app.command_handlers import handle_command

        with patch("app.command_handlers.KOAN_ROOT", tmp_path), \
             patch("app.command_handlers.INSTANCE_DIR", tmp_path):
            handle_command("/help")
        mock_send.assert_called_once()
        help_text = mock_send.call_args[0][0]
        assert "/idea" in help_text
