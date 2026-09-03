import copy
import io
import json
import os
import urllib.error
from unittest.mock import patch

from reddit_scout.analysis import local_analysis
from reddit_scout.gemini import GeminiAnalyzer, GeminiUnavailable, retry_seconds, validate_output

from .helpers import StoreCase, post


def answer(item):
    findings = local_analysis(item)["findings"]
    return {"items": [{"id": item.id, "findings": findings}]}


def envelope(value):
    return {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(value)}]}}]}


class GeminiTests(StoreCase):
    def setUp(self):
        super().setUp()
        patcher = patch.dict(os.environ, {"GEMINI_API_KEY": "test-only-not-a-real-key"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.config["analysis"]["models"] = ["model-one", "model-two"]

    def test_fabricated_quote_rejected(self):
        item = post()
        value = answer(item)
        value["items"][0]["findings"][0]["evidence_quote"] = "I definitely promise to buy this for a million dollars"
        with self.assertRaisesRegex(ValueError, "Ungrounded"):
            validate_output(value, [item], 6000)

    def test_unknown_and_missing_source_ids_rejected(self):
        item = post()
        value = answer(item)
        value["items"][0]["id"] = "t3_notinbatch"
        with self.assertRaises(ValueError):
            validate_output(value, [item], 6000)
        with self.assertRaises(ValueError):
            validate_output({"items": []}, [item], 6000)

    def test_spending_time_cannot_be_promoted_to_existing_spend(self):
        item = post(body="I spend 3 hours every week manually chasing overdue invoices.")
        value = answer(item)
        finding = value["items"][0]["findings"][0]
        finding.update(money_kind="existing_spend", money_quote=item.body)
        results = validate_output(value, [item], 6000)
        self.assertEqual(results[item.id]["findings"][0]["money_kind"], "none")

    def test_429_falls_back_and_persists_model_cooldown(self):
        calls = []
        item = post()
        def transport(model, payload):
            calls.append(model)
            if model == "model-one":
                raise urllib.error.HTTPError("https://example.invalid", 429, "rate limit", {"Retry-After": "120"}, io.BytesIO())
            return envelope(answer(item))
        engine = GeminiAnalyzer(self.config["analysis"], self.store, transport=transport)
        self.assertEqual(engine.analyze([item], [])[item.id]["model"], "model-two")
        second = GeminiAnalyzer(self.config["analysis"], self.store, transport=transport)
        second.analyze([item], [])
        self.assertEqual(calls, ["model-one", "model-two", "model-two"])
        self.assertEqual(self.store.get_meta("gemini_usage")["requests"], 3)

    def test_auth_error_does_not_retry_every_model(self):
        calls = []
        def transport(model, payload):
            calls.append(model)
            raise urllib.error.HTTPError("https://example.invalid", 403, "invalid access", {}, io.BytesIO())
        engine = GeminiAnalyzer(self.config["analysis"], self.store, transport=transport)
        with self.assertRaises(GeminiUnavailable):
            engine.analyze([post()], [])
        self.assertEqual(calls, ["model-one"])

    def test_attempt_budget_includes_failed_requests(self):
        self.config["analysis"]["max_requests_per_run"] = 1
        def transport(*_):
            raise ValueError("malformed JSON")
        engine = GeminiAnalyzer(self.config["analysis"], self.store, transport=transport)
        with self.assertRaisesRegex(GeminiUnavailable, "budget"):
            engine.analyze([post()], [])
        self.assertEqual(engine.requests, 1)

    def test_truncated_response_is_not_accepted_or_cached(self):
        def transport(*_):
            result = envelope(answer(post()))
            result["candidates"][0]["finishReason"] = "MAX_TOKENS"
            return result
        engine = GeminiAnalyzer(self.config["analysis"], self.store, transport=transport)
        with self.assertRaises(GeminiUnavailable):
            engine.analyze([post()], [])
        self.assertEqual(self.store.stats()["analyses"], 0)

    def test_retry_after_seconds(self):
        self.assertEqual(retry_seconds("120"), 120)
        self.assertEqual(retry_seconds("invalid"), 60)

    def test_alternative_name_must_exist_in_source(self):
        item = post()
        value = answer(item)
        value["items"][0]["findings"][0]["alternatives"] = ["InventedCompetitor"]
        with self.assertRaisesRegex(ValueError, "alternative"):
            validate_output(value, [item], 6000)
