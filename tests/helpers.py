import copy
import tempfile
import unittest
from pathlib import Path

from reddit_scout.config import load_config
from reddit_scout.db import Store
from reddit_scout.models import Item


def post(identifier="abc123", body="Our business wastes 5 hours every week chasing overdue invoices manually. I would pay $150 per month.",
         title="Chasing overdue invoices", **kwargs):
    return Item("t3_" + identifier, "t3_" + identifier, "smallbusiness", "post", title, body,
                f"https://www.reddit.com/r/smallbusiness/comments/{identifier}/test/", complete=True, **kwargs)


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = copy.deepcopy(load_config())
        self.config["storage"]["database"] = str(self.root / "state.sqlite3")
        self.config["storage"]["reports"] = str(self.root / "reports")
        self.config["browser"]["profile"] = str(self.root / "profile")
        self.config["analysis"]["mode"] = "local"
        self.config["analysis"]["min_request_interval_seconds"] = 0
        self.config["collection"].update(subreddits=["smallbusiness"], feeds=["new"], search_queries=[],
                                          max_feed_pages_per_run=1, max_threads_per_run=1)
        self.store = Store(self.config["storage"]["database"])
        self.addCleanup(lambda: self.store.close())

    def save(self, *items):
        with self.store.conn:
            for item in items:
                self.store.upsert(item)
