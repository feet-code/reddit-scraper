from dataclasses import replace

from reddit_scout.analysis import analyze, build_opportunities, local_analysis
from reddit_scout.demo import demo_items
from reddit_scout.signals import buying_evidence

from .helpers import StoreCase, post


class AnalysisTests(StoreCase):
    def test_money_requires_actual_payment_language(self):
        cases = [
            ("I spend 3 hours every week on invoicing.", "none"),
            ("Our company makes $2 million a year.", "none"),
            ("We have $50,000 in overdue invoices.", "none"),
            ("We pay $80,000 in salaries.", "none"),
            ("My budget for rent is $2,000 per month.", "none"),
            ("I won't pay $99 for this tool.", "none"),
            ("We have no budget and would never pay.", "none"),
            ("I would pay $150 per month to fix this.", "explicit_willingness"),
            ("We currently pay $80 per month for QuickBooks.", "existing_spend"),
            ("We already pay for HubSpot.", "existing_spend"),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(buying_evidence(text)[0], expected)

    def test_time_sentence_does_not_hide_later_spending(self):
        kind, quote = buying_evidence("I spend 3 hours every week on invoices. We currently pay $80 per month for QuickBooks.")
        self.assertEqual(kind, "existing_spend")
        self.assertIn("$80", quote)

    def test_many_replies_are_only_one_independent_thread(self):
        item = post()
        replies = [replace(item, id=f"t1_rep{i}", kind="comment", body=f"Our agency wastes {i+2} hours chasing overdue invoices every week.") for i in range(8)]
        self.save(item, *replies)
        analyze(self.store, self.config)
        result = build_opportunities(self.store, self.config)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["independent_threads"], 1)
        self.assertEqual(result[0]["stage"], "early_signal")
        self.assertLessEqual(result[0]["score"], 45)

    def test_copied_crossposts_do_not_inflate_recurrence(self):
        item = post()
        self.save(item, replace(item, id="t3_copy99", thread_id="t3_copy99", title="Reposted question"))
        analyze(self.store, self.config)
        self.assertEqual(build_opportunities(self.store, self.config)[0]["independent_threads"], 1)

    def test_comment_context_is_not_its_own_payment_evidence(self):
        comment = replace(post(), kind="comment", id="t1_comment1", title="I would pay $300 for this",
                          body="Our team has overdue invoices and struggles to follow up manually every week.")
        findings = local_analysis(comment)["findings"]
        self.assertEqual(findings[0]["money_kind"], "none")
        self.assertNotIn("$300", findings[0]["evidence_quote"])

    def test_demo_ranks_recurring_costly_work_above_minor_complaint(self):
        self.save(*demo_items())
        analyze(self.store, self.config)
        results = build_opportunities(self.store, self.config)
        self.assertEqual(results[0]["problem_key"], "invoice-followup")
        self.assertEqual(results[0]["independent_threads"], 3)
        self.assertTrue(all(o["synthetic"] for o in results))
        self.assertTrue(all(not o["pricing_hypothesis"]["validated"] for o in results))
        self.assertNotIn("t3_demo09", {e["id"] for o in results for e in o["evidence"]})
        self.assertLess(results[-1]["score"], results[0]["score"])

    def test_upvotes_do_not_raise_opportunity_score(self):
        item = post(score=1)
        self.save(item)
        analyze(self.store, self.config)
        before = build_opportunities(self.store, self.config)[0]["score"]
        self.save(replace(item, score=100000))
        analyze(self.store, self.config)
        self.assertEqual(build_opportunities(self.store, self.config)[0]["score"], before)

    def test_unmodified_content_is_not_reanalyzed(self):
        self.save(post())
        self.assertEqual(analyze(self.store, self.config)["local_processed"], 1)
        self.assertEqual(analyze(self.store, self.config)["local_processed"], 0)
        self.config["collection"]["min_text_characters"] = 900
        self.assertEqual(analyze(self.store, self.config)["local_processed"], 1)
        self.assertEqual(build_opportunities(self.store, self.config), [])
