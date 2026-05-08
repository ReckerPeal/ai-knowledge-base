"""Tests for article JSON validation and Codex hook dispatch."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def valid_article() -> dict[str, object]:
    """Build a valid knowledge article fixture.

    Returns:
        A knowledge article matching AGENTS.md schema guidance.
    """
    return {
        "id": "20260507-github_trending-owner-repo",
        "title": "Example AI Agent Framework",
        "source": "github_trending",
        "source_url": "https://github.com/owner/repo",
        "summary": "This agent framework adds memory and evaluation for LLM workflows.",
        "content": "Structured analysis with background, highlights, limits, and use cases.",
        "tags": ["AI", "LLM", "Agent", "Framework"],
        "status": "draft",
        "published_at": "2026-05-07T10:30:00+08:00",
        "collected_at": "2026-05-07T11:00:00+08:00",
        "language": "en",
        "score": 8.6,
        "metadata": {
            "author": "owner",
            "stars": 12345,
            "comments": 42,
            "distribution_channels": ["telegram", "lark"],
        },
    }


class ValidateJsonHookTest(unittest.TestCase):
    """Verify article validation and hook event handling."""

    def test_validator_accepts_agents_schema_article(self) -> None:
        """AGENTS.md style article JSON passes validation."""
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temp_dir:
            article_path = (
                Path(temp_dir)
                / "knowledge"
                / "articles"
                / "2026-05-07"
                / "20260507-github_trending-owner-repo.json"
            )
            article_path.parent.mkdir(parents=True)
            article_path.write_text(
                json.dumps(valid_article(), ensure_ascii=False),
                encoding="utf-8",
            )

            result = subprocess.run(
                [sys.executable, "hooks/validate_json.py", str(article_path)],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("OK", result.stdout)

    def test_validator_expands_recursive_globs(self) -> None:
        """Recursive glob patterns include dated article subdirectories."""
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temp_dir:
            temp_path = Path(temp_dir)
            article_path = (
                temp_path
                / "knowledge"
                / "articles"
                / "2026-05-07"
                / "20260507-github_trending-owner-repo.json"
            )
            article_path.parent.mkdir(parents=True)
            article_path.write_text(
                json.dumps(valid_article(), ensure_ascii=False),
                encoding="utf-8",
            )
            pattern = str(temp_path / "knowledge" / "articles" / "**" / "*.json")

            result = subprocess.run(
                [sys.executable, "hooks/validate_json.py", pattern],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("total=1", result.stdout)

    def test_hook_dispatches_edit_for_nested_article_json(self) -> None:
        """PostToolUse Edit events for nested article JSON run validation."""
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temp_dir:
            article_path = (
                Path(temp_dir)
                / "knowledge"
                / "articles"
                / "2026-05-07"
                / "20260507-github_trending-owner-repo.json"
            )
            article_path.parent.mkdir(parents=True)
            article_path.write_text(
                json.dumps(valid_article(), ensure_ascii=False),
                encoding="utf-8",
            )
            relative_path = article_path.relative_to(REPO_ROOT)
            hook_input = {
                "hook_event_name": "PostToolUse",
                "tool_name": "Edit",
                "tool_input": {"file_path": str(relative_path)},
            }

            result = subprocess.run(
                [sys.executable, "hooks/validate_article_hook.py"],
                cwd=REPO_ROOT,
                input=json.dumps(hook_input),
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("OK", result.stdout)

    def test_hook_skips_non_article_json(self) -> None:
        """PostToolUse events outside knowledge/articles do not run validation."""
        hook_input = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": "knowledge/raw/github-trending-2026-05-07.json"},
        }

        result = subprocess.run(
            [sys.executable, "hooks/validate_article_hook.py"],
            cwd=REPO_ROOT,
            input=json.dumps(hook_input),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual("", result.stdout)

    def test_hook_supports_apply_patch_tool_name(self) -> None:
        """Codex patch-style edit events are treated as write events."""
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temp_dir:
            article_path = (
                Path(temp_dir)
                / "knowledge"
                / "articles"
                / "2026-05-07"
                / "20260507-github_trending-owner-repo.json"
            )
            article_path.parent.mkdir(parents=True)
            article_path.write_text(
                json.dumps(valid_article(), ensure_ascii=False),
                encoding="utf-8",
            )
            relative_path = article_path.relative_to(REPO_ROOT)
            hook_input = {
                "hook_event_name": "PostToolUse",
                "tool_name": "apply_patch",
                "tool_input": {"path": str(relative_path)},
            }

            result = subprocess.run(
                [sys.executable, "hooks/validate_article_hook.py"],
                cwd=REPO_ROOT,
                input=json.dumps(hook_input),
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
