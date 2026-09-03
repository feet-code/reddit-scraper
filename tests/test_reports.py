import json
from dataclasses import replace

from reddit_scout.analysis import analyze
from reddit_scout.reports import safe_cell, write_reports

from .helpers import StoreCase, post


class ReportTests(StoreCase):
    def test_html_escapes_source_content_and_quotes(self):
        item = post(body='Our business wastes <script>alert("xss")</script> 5 hours chasing overdue invoices. I would pay $99.')
        self.save(item)
        analyze(self.store, self.config)
        result = write_reports(self.store, self.config)
        from pathlib import Path
        html = Path(result["paths"]["html"]).read_text(encoding="utf-8")
        self.assertNotIn('<script>alert', html)
        self.assertIn('&lt;script&gt;alert', html)
        self.assertIn("Price experiment", html)
        self.assertIn("Unvalidated hypothesis", html)
        self.assertEqual(set(result["paths"]), {"html", "markdown", "json", "csv"})

    def test_csv_neutralizes_formula_injection(self):
        for value in ("=HYPERLINK(1)", "+1+2", "-3+4", "@SUM(A1)", "  =cmd", "\t=cmd"):
            self.assertTrue(safe_cell(value).startswith("'"))
        self.assertEqual(safe_cell("ordinary text"), "ordinary text")

    def test_report_regeneration_removes_deleted_evidence(self):
        item = post()
        self.save(item)
        analyze(self.store, self.config)
        paths = write_reports(self.store, self.config)["paths"]
        with self.store.conn:
            self.store.remove(item.id)
        write_reports(self.store, self.config)
        from pathlib import Path
        data = json.loads(Path(paths["json"]).read_text())
        self.assertEqual(data["opportunities"], [])
        for path in paths.values():
            self.assertNotIn("t3_abc123", Path(path).read_text(encoding="utf-8"))

    def test_partial_collection_is_visible_in_report(self):
        with self.store.conn:
            self.store.set_meta("last_collection", {"blocked": "Reddit requires a manual sign-in", "errors": []})
        result = write_reports(self.store, self.config)
        from pathlib import Path
        self.assertIn("manual sign-in", Path(result["paths"]["html"]).read_text())
