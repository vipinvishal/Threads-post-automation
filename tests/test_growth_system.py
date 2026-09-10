import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import growth_intelligence
import infographic
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
            "image_template": "handwritten_poster",
            "numeric_claims": [],
            "reply_seed": "Start with task-specific evaluation.",
        })
        result = generate_post_json(
            "practical_tips", "Local LLM quantization", "source", False,
            ["handwritten_poster"], "cold_open_stat", "share",
        )
        self.assertFalse(result["cta_included"])

    def test_handwritten_poster_copy_is_bounded(self):
        raw = {
            "headline_line1": "one two three four five six seven eight",
            "headline_line2": "quality breaks here today",
            "context_line": "one benchmark compared these three formats",
            "cards": [
                {"label": "full precision baseline model", "value": "55 GB total model weights"},
                {"label": "four bit", "value": "17 GB"},
                {"label": "one bit", "value": "6.2 GB"},
            ],
            "evidence_line1": "matched full precision on coding tasks in this benchmark",
            "evidence_line2": "under the reported evaluation conditions",
            "warning": "one bit reasoning quality collapsed sharply",
            "takeaway_line1": "use four bit first today",
            "takeaway_line2": "before buying more gpu capacity",
        }
        result = infographic._coerce_poster_copy(raw)
        self.assertEqual(len(result["cards"]), 3)
        self.assertLessEqual(len(result["headline_line1"].split()), 7)
        self.assertTrue(all(len(card["label"].split()) <= 3 for card in result["cards"]))
        self.assertTrue(all(len(card["value"].split()) <= 4 for card in result["cards"]))

    def test_handwritten_prompt_keeps_exact_copy_and_mascot(self):
        copy = {
            "headline_line1": "Your model can shrink",
            "headline_line2": "But quality has a floor",
            "context_line": "One benchmark compared",
            "cards": [
                {"label": "BF16", "value": "55 GB"},
                {"label": "4-bit", "value": "17 GB"},
                {"label": "1-bit", "value": "6.2 GB"},
            ],
            "evidence_line1": "4-bit matched BF16",
            "evidence_line2": "on coding tasks",
            "warning": "1-bit reasoning collapsed",
            "takeaway_line1": "Use 4-bit",
            "takeaway_line2": "before buying GPUs",
        }
        prompt = infographic.build_handwritten_poster_prompt("quantization", copy)
        self.assertIn('"4-bit matched BF16"', prompt)
        self.assertIn("blue bird mascot", prompt)
        self.assertIn("exactly three", prompt)

    def test_poster_copy_rejects_invented_number(self):
        response = json.dumps({
            "headline_line1": "Latency dropped 99%",
            "headline_line2": "But verify the workload",
            "context_line": "One benchmark compared",
            "cards": [
                {"label": "Before", "value": "Slow"},
                {"label": "Change", "value": "Cache"},
                {"label": "After", "value": "Fast"},
            ],
            "evidence_line1": "Measure the same workload",
            "evidence_line2": "under the same conditions",
            "warning": "Do not trust vendor claims",
            "takeaway_line1": "Benchmark your stack",
            "takeaway_line2": "before changing architecture",
        })
        with self.assertRaises(RuntimeError):
            infographic.generate_poster_copy(
                "The measured latency improved.", "Caching", "Caching helped.",
                lambda *_: response,
            )

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
    def test_buffer_receives_single_poster_asset(self, post):
        response = post.return_value
        response.status_code = 200
        response.json.return_value = {"data": {"createPost": {"post": {"id": "buffer-1"}}}}
        response.raise_for_status.return_value = None
        self.assertEqual(schedule_to_buffer("hello", "https://i/poster.png"), "buffer-1")
        variables = post.call_args.kwargs["json"]["variables"]
        self.assertEqual(variables["imageUrl0"], "https://i/poster.png")
        self.assertNotIn("imageUrl1", variables)


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
