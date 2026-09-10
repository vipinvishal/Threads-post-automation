import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import growth_intelligence
import infographic_templates
import threads_insights
from generate_and_schedule import (
    build_final_post,
    extract_numeric_claims,
    fact_check_claims,
    generate_post_json,
    schedule_to_buffer,
)


class GrowthIntelligenceTests(unittest.TestCase):
    def test_dedupe_keeps_stronger_signal(self):
        signals = [
            {"source": "one", "title": "AI inference launch", "url": "https://x.test/a?ref=1", "signal_score": 2},
            {"source": "two", "title": "AI inference launch update", "url": "https://x.test/a", "signal_score": 9},
        ]
        result = growth_intelligence.dedupe_signals(signals)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["signal_score"], 9)

    def test_short_ai_terms_use_word_boundaries(self):
        self.assertFalse(growth_intelligence._is_ai_relevant("Tailwind Labs is joining Shopify"))
        self.assertTrue(growth_intelligence._is_ai_relevant("New AI inference runtime"))

    def test_skill_update_only_replaces_managed_block(self):
        original = "stable\n<!-- DAILY_INTELLIGENCE_START -->\nold\n<!-- DAILY_INTELLIGENCE_END -->\nend\n"
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "SKILL.md"
            path.write_text(original, encoding="utf-8")
            growth_intelligence.update_skill_daily_section(
                {"date_ist": "2026-09-09", "candidates": []}, [], path
            )
            updated = path.read_text(encoding="utf-8")
        self.assertTrue(updated.startswith("stable\n"))
        self.assertTrue(updated.endswith("\nend\n"))
        self.assertIn("2026-09-09", updated)

    def test_lessons_require_real_metrics(self):
        self.assertIn("Not enough", growth_intelligence.performance_lessons([{"insights": {}}])[0])


class ContentTests(unittest.TestCase):
    @patch("generate_and_schedule.generate_text")
    def test_share_cta_boolean_is_normalized(self, generate_text):
        generate_text.return_value = json.dumps({
            "format": "practical_tips",
            "hook": "Three checks before quantizing",
            "body": "Three checks before quantizing\n\nShare this with a local-LLM builder.",
            "cta_included": True,
            "cta_type": "share",
            "cta_text": "Share this with a local-LLM builder",
            "tag": "AI",
            "image_template": "educational_carousel",
            "numeric_claims": [],
            "reply_seed": "Start with task-specific evaluation.",
        })
        result = generate_post_json(
            "practical_tips", "Local LLM quantization", "source", False,
            ["educational_carousel"], "cold_open_stat", "share",
        )
        self.assertFalse(result["cta_included"])

    def test_carousel_is_exactly_five_bounded_cards(self):
        raw = {
            "slides": [{"title_hl": "x" * 100, "bullets": ["y" * 100] * 5}],
            "alt_text": "z" * 500,
        }
        result = infographic_templates._coerce_educational_carousel(raw)
        self.assertEqual([s["kind"] for s in result["slides"]],
                         ["cover", "problem", "mechanism", "example", "takeaway"])
        self.assertLessEqual(len(result["slides"][0]["title_hl"]), 30)
        self.assertEqual(len(result["slides"][0]["bullets"]), 3)
        self.assertLessEqual(len(result["alt_text"]), 300)

    def test_numeric_extractor_ignores_list_numbers(self):
        claims = extract_numeric_claims("1. Clean chunks\n2. Test retrieval\nLatency fell 35% to 80ms. Cost ₹120.")
        self.assertNotIn("1", claims)
        self.assertNotIn("2", claims)
        self.assertIn("35%", claims)
        self.assertIn("80ms", claims)
        self.assertIn("₹120", claims)

    def test_strict_numeric_gate_requires_number_in_source(self):
        _, flagged = fact_check_claims(["35% faster"], "The measured change was 20%.", "It was 35% faster.")
        self.assertEqual(flagged, ["35% faster"])

    def test_final_post_has_only_one_conversion_action(self):
        text, _ = build_final_post("A useful body.", "#AI", "AI", True, "click", "Read the full test")
        self.assertIn("Read the full test", text)
        self.assertNotIn("follow @vipinailabs", text.lower())
        self.assertLessEqual(len(text), 500)

    @patch("generate_and_schedule.requests.post")
    def test_buffer_receives_all_carousel_assets(self, post):
        response = post.return_value
        response.status_code = 200
        response.json.return_value = {"data": {"createPost": {"post": {"id": "buffer-1"}}}}
        response.raise_for_status.return_value = None
        self.assertEqual(schedule_to_buffer("hello", ["https://i/1.png", "https://i/2.png"]), "buffer-1")
        variables = post.call_args.kwargs["json"]["variables"]
        self.assertEqual(variables["imageUrl0"], "https://i/1.png")
        self.assertEqual(variables["imageUrl1"], "https://i/2.png")


class InsightTests(unittest.TestCase):
    def test_match_thread_uses_text_and_time(self):
        now = datetime.now(timezone.utc).isoformat()
        entry = {"post_text": "RAG fails before retrieval starts. Here is why.", "timestamp": now}
        thread = {"id": "1", "text": "RAG fails before retrieval starts. Here is why.\n#AI", "timestamp": now}
        self.assertEqual(threads_insights.match_thread(entry, [thread])["id"], "1")

    def test_metric_value_handles_values_shape(self):
        self.assertEqual(threads_insights._metric_value({"values": [{"value": 42}]}), 42.0)
        self.assertEqual(threads_insights._metric_value({"total_value": {"value": 51}}), 51.0)


if __name__ == "__main__":
    unittest.main()
