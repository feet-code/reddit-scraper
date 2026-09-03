import json
import time
from dataclasses import replace

from reddit_scout.analysis import analyze, local_key
from reddit_scout.cli import import_records
from reddit_scout.locking import InstanceLock
from reddit_scout.models import Item, reddit_url

from .helpers import StoreCase, post


class StorageTests(StoreCase):
    def test_feed_preview_cannot_overwrite_full_body(self):
        item = post()
        self.save(item)
        analyze(self.store, self.config)
        preview = replace(item, body="Truncated preview", complete=False, score=42)
        self.save(preview)
        saved = self.store.items()[0]
        self.assertEqual(saved.body, item.body)
        self.assertEqual(saved.score, 42)
        self.assertIsNotNone(self.store.cached(item, local_key(self.config)))

    def test_edit_invalidates_previous_analysis(self):
        item = post()
        self.save(item)
        analyze(self.store, self.config)
        edited = replace(item, body="We solved this problem and no longer need a tool.")
        self.save(edited)
        self.assertIsNone(self.store.cached(edited, local_key(self.config)))

    def test_removing_post_cascades_to_comments_and_analyses(self):
        item = post()
        comment = replace(item, id="t1_comment1", kind="comment")
        self.save(item, comment)
        analyze(self.store, self.config)
        with self.store.conn:
            self.store.remove(item.id)
        self.assertEqual(self.store.items(), [])
        self.assertEqual(self.store.stats()["analyses"], 0)

    def test_transaction_rolls_back_an_interrupted_page(self):
        try:
            with self.store.conn:
                self.store.upsert(post())
                self.store.enqueue(post().permalink, "thread", "smallbusiness")
                raise RuntimeError("crash before commit")
        except RuntimeError:
            pass
        self.assertEqual(self.store.items(), [])
        self.assertEqual(self.store.stats()["due_threads"], 0)

    def test_retention_removes_content_analysis_and_obsolete_thread_job(self):
        item = post()
        with self.store.conn:
            self.store.upsert(item, now=time.time()-40*86400)
            self.store.enqueue(item.permalink, "thread", "smallbusiness")
        analyze(self.store, self.config)
        self.assertEqual(self.store.prune(30), 1)
        self.assertEqual(self.store.items(), [])
        self.assertEqual(self.store.stats()["due_threads"], 0)

    def test_urls_cannot_route_to_external_sites_or_mismatched_sources(self):
        for url in ("https://evil.example/r/test/comments/a/b/", "//evil.example/path", "javascript:alert(1)",
                    "https://www.reddit.com@evil.example/r/test/comments/a/b/", "https://www.reddit.com/user/someone/"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                reddit_url(url)
        raw = post().as_dict() | {"id": "t3_another", "thread_id": "t3_another"}
        with self.assertRaises(ValueError):
            Item.from_dict(raw)

    def test_comment_urls_and_tracking_parameters(self):
        raw = post().as_dict() | {"id": "t1_comment1", "kind": "comment",
            "permalink": "https://www.reddit.com/r/smallbusiness/comments/abc123/comment/comment1/?utm_source=test"}
        self.assertTrue(Item.from_dict(raw).permalink.endswith("/comment/comment1/"))

    def test_import_is_atomic_and_drops_unneeded_author_fields(self):
        path = self.root / "records.json"
        path.write_text(json.dumps([post().as_dict() | {"author": "unused-person"}]))
        self.assertEqual(import_records(self.store, path), 1)
        self.assertNotIn("author", self.store.items()[0].as_dict())
        path.write_text(json.dumps([post("second1").as_dict(), {"id": "broken"}]))
        with self.assertRaises(ValueError):
            import_records(self.store, path)
        self.assertEqual(len(self.store.items()), 1)

    def test_instance_lock_releases_after_exception(self):
        path = self.root / "locked"
        with self.assertRaises(RuntimeError):
            with InstanceLock(path):
                with InstanceLock(path):
                    pass
        with InstanceLock(path):
            pass
