from dataclasses import replace

from reddit_scout.browser import AccessBlocked, LayoutChanged
from reddit_scout.collector import collect
from reddit_scout.db import Store

from .helpers import StoreCase, post


def snapshot(*items, empty=False, removed=()):
    return {"posts": [i.as_dict() for i in items if i.kind == "post"],
            "comments": [i.as_dict() for i in items if i.kind == "comment"],
            "removed_ids": list(removed), "empty": empty, "block": ""}


class FakeReader:
    def __init__(self, config):
        self.config = config

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def snapshots(self, url, *, detail=False, thread_id=""):
        item = post()
        if detail:
            reply = replace(item, id="t1_reply01", kind="comment", body="Our business has overdue invoice problems too.",
                            permalink=item.permalink + "reply01/")
            yield snapshot(item, reply)
        else:
            yield snapshot(replace(item, complete=False))


class CollectorTests(StoreCase):
    def test_resume_after_crash_reuses_committed_page_and_queued_thread(self):
        class CrashReader(FakeReader):
            def snapshots(self, url, **kwargs):
                yield from super().snapshots(url, **kwargs)
                raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            collect(self.store, self.config, CrashReader)
        self.assertEqual(self.store.stats()["posts"], 1)
        self.assertEqual(self.store.stats()["due_threads"], 1)
        self.assertEqual(self.store.stats()["due_feeds"], 1)
        self.assertTrue(self.store.get_meta("last_collection")["interrupted"])
        self.store.close()
        self.store = Store(self.config["storage"]["database"])
        result = collect(self.store, self.config, FakeReader)
        self.assertIsNone(result["blocked"])
        self.assertEqual(self.store.stats()["posts"], 1)
        self.assertEqual(self.store.stats()["comments"], 1)
        self.assertEqual(self.store.stats()["due_threads"], 0)

    def test_block_stops_all_further_navigation_without_losing_saved_work(self):
        calls = []
        class BlockReader(FakeReader):
            def snapshots(self, url, **kwargs):
                calls.append(url)
                yield snapshot(replace(post(), complete=False))
                raise AccessBlocked("Verify you are human")
        self.config["collection"].update(feeds=["new", "top"], max_feed_pages_per_run=2)
        result = collect(self.store, self.config, BlockReader)
        self.assertTrue(result["blocked"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.store.stats()["posts"], 1)
        self.assertEqual(self.store.stats()["due_feeds"], 2)

    def test_broken_layout_is_visible_and_other_feed_can_continue(self):
        class ChangedReader(FakeReader):
            def snapshots(self, url, **kwargs):
                if "/new/" in url:
                    raise LayoutChanged("No recognizable post cards")
                yield from super().snapshots(url, **kwargs)
        self.config["collection"].update(feeds=["new", "top"], max_feed_pages_per_run=2)
        result = collect(self.store, self.config, ChangedReader)
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["feeds"], 1)
        self.assertEqual(result["threads"], 1)

    def test_small_feed_budget_rotates_across_subreddits(self):
        class EmptyReader(FakeReader):
            calls = []
            def snapshots(self, url, **kwargs):
                self.calls.append(url)
                yield snapshot(empty=True)
        self.config["collection"].update(subreddits=["smallbusiness", "sysadmin"], max_threads_per_run=0)
        collect(self.store, self.config, EmptyReader)
        collect(self.store, self.config, EmptyReader)
        self.assertEqual(len(EmptyReader.calls), 2)
        self.assertIn("/smallbusiness/", EmptyReader.calls[0])
        self.assertIn("/sysadmin/", EmptyReader.calls[1])

    def test_observed_deleted_post_removes_existing_evidence(self):
        item = post()
        self.save(item)
        class DeletedReader(FakeReader):
            def snapshots(self, url, **kwargs):
                yield snapshot(removed=[item.id], empty=True)
        self.config["collection"]["max_threads_per_run"] = 0
        collect(self.store, self.config, DeletedReader)
        self.assertEqual(self.store.items(), [])
