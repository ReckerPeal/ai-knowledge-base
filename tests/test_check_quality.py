"""Tests for the knowledge article quality checker."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class CheckQualityTest(unittest.TestCase):
    """Verify article quality scoring behavior."""

    def test_scores_high_quality_article_as_a(self) -> None:
        """A complete technical article with valid tags receives grade A."""
        from hooks.check_quality import grade_for_score, score_article

        article = {
            "id": "20260507-github-owner-repo",
            "title": "Agent Memory Framework",
            "source": "github_trending",
            "source_url": "https://github.com/owner/repo",
            "summary": (
                "This LLM agent framework adds structured memory, RAG retrieval, "
                "and evaluation tooling for production workflows."
            ),
            "content": "Detailed analysis.",
            "tags": ["AI", "LLM", "Agent"],
            "status": "draft",
            "published_at": "2026-05-07T10:30:00+08:00",
            "collected_at": "2026-05-07T11:00:00+08:00",
            "language": "en",
            "score": 9,
            "metadata": {},
        }

        report = score_article(Path("article.json"), article)

        self.assertEqual("A", grade_for_score(report.total_score))
        self.assertGreaterEqual(report.total_score, 80)
        self.assertEqual(5, len(report.dimension_scores))

    def test_empty_words_reduce_grade(self) -> None:
        """Blacklisted hollow words lower the hollow-word dimension."""
        from hooks.check_quality import score_article

        article = {
            "id": "20260507-github-owner-repo",
            "title": "Agent Platform",
            "source_url": "https://example.com/item",
            "summary": "赋能团队打通全链路闭环，这是一项 revolutionary platform.",
            "tags": ["AI", "Agent"],
            "status": "draft",
            "collected_at": "2026-05-07T11:00:00+08:00",
            "score": 6,
        }

        report = score_article(Path("article.json"), article)
        hollow_score = next(
            score
            for score in report.dimension_scores
            if score.name == "空洞词检测"
        )

        self.assertLess(hollow_score.score, hollow_score.max_score)
        self.assertIn("赋能", hollow_score.notes)

    def test_cli_returns_one_when_any_file_is_c_grade(self) -> None:
        """CLI exits with 1 when any input file receives grade C."""
        weak_article = {
            "id": "",
            "title": "",
            "source_url": "not-a-url",
            "summary": "短摘要",
            "tags": ["unknown"],
            "status": "",
            "score": 1,
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            article_path = Path(temp_dir) / "weak.json"
            article_path.write_text(
                json.dumps(weak_article, ensure_ascii=False),
                encoding="utf-8",
            )

            result = subprocess.run(
                [sys.executable, "hooks/check_quality.py", str(article_path)],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(1, result.returncode)
        self.assertIn("等级: C", result.stdout)


if __name__ == "__main__":
    unittest.main()
