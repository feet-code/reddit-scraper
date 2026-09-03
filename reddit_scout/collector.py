from __future__ import annotations

import logging
import time
from urllib.parse import urlencode, urlsplit

from .browser import AccessBlocked, BrowserReader, LayoutChanged, PageUnavailable
from .db import Store
from .models import Item
from .signals import priority

LOG = logging.getLogger(__name__)


def feed_urls(config: dict):
    for sub in config["subreddits"]:
        for feed in config["feeds"]:
            suffix = "?" + urlencode({"t": config["time_filter"]}) if feed == "top" else ""
            yield sub, f"https://www.reddit.com/r/{sub}/{feed}/{suffix}"
        for query in config["search_queries"]:
            params = urlencode({"q": query, "restrict_sr": "on", "sort": "new", "t": config["time_filter"], "type": "link"})
            yield sub, f"https://www.reddit.com/r/{sub}/search/?{params}"


def collect(store: Store, config: dict, reader_factory=BrowserReader) -> dict:
    c = config["collection"]
    allowed_feeds = {url for _, url in feed_urls(c)}
    result = {"feeds": 0, "threads": 0, "changed_items": 0, "errors": [], "blocked": None,
              "started_at": time.time()}
    with store.conn:
        for sub, url in feed_urls(c):
            store.enqueue(url, "feed", sub)
    feeds = [j for j in store.due("feed", c["subreddits"], 100000) if j["url"] in allowed_feeds]
    feeds = feeds[:c["max_feed_pages_per_run"]]
    try:
        with reader_factory(config["browser"]) as reader:
            for i, job in enumerate(feeds, 1):
                LOG.info("Feed %d/%d: %s", i, len(feeds), job["url"])
                seen = set()
                try:
                    for data in reader.snapshots(job["url"]):
                        with store.conn:
                            for identifier in data["removed_ids"]:
                                store.remove(identifier)
                            for raw in data["posts"]:
                                if len(seen) >= c["posts_per_feed"]:
                                    break
                                if raw["id"] in seen:
                                    continue
                                item = Item.from_dict(raw)
                                # Reddit recommendations/search can include other communities.
                                if item.subreddit != job["subreddit"]:
                                    continue
                                seen.add(item.id)
                                result["changed_items"] += store.upsert(item)
                                store.enqueue(item.permalink, "thread", item.subreddit, priority(item.text))
                        if len(seen) >= c["posts_per_feed"]:
                            break
                    with store.conn:
                        store.finish_job(job["url"], c["refresh_hours"])
                    result["feeds"] += 1
                    LOG.info("  Saved %d post previews", len(seen))
                except (PageUnavailable, LayoutChanged, ValueError) as exc:
                    _job_error(store, result, job, exc, c["refresh_hours"])
            threads = store.due("thread", c["subreddits"], c["max_threads_per_run"],
                                exploration=c["exploration_fraction"])
            for i, job in enumerate(threads, 1):
                thread_id = "t3_" + urlsplit(job["url"]).path.split("/")[4]
                LOG.info("Thread %d/%d: %s", i, len(threads), job["url"])
                seen_comments = set()
                try:
                    for data in reader.snapshots(job["url"], detail=True, thread_id=thread_id):
                        with store.conn:
                            for identifier in data["removed_ids"]:
                                store.remove(identifier)
                            root_deleted = thread_id in data["removed_ids"]
                            for raw in data["posts"] + data["comments"]:
                                if root_deleted:
                                    break
                                if raw["kind"] == "comment":
                                    if raw["id"] in seen_comments:
                                        continue
                                    if len(seen_comments) >= config["browser"]["comments_per_post"]:
                                        continue
                                item = Item.from_dict(raw)
                                if item.thread_id != thread_id:
                                    continue
                                result["changed_items"] += store.upsert(item)
                                if item.kind == "comment":
                                    seen_comments.add(item.id)
                        if root_deleted or len(seen_comments) >= config["browser"]["comments_per_post"]:
                            break
                    with store.conn:
                        store.finish_job(job["url"], c["refresh_hours"])
                    result["threads"] += 1
                    LOG.info("  Saved %d loaded comments", len(seen_comments))
                except (PageUnavailable, LayoutChanged, ValueError) as exc:
                    _job_error(store, result, job, exc, c["refresh_hours"])
    except AccessBlocked as exc:
        result["blocked"] = str(exc)
        LOG.error("Collection paused: %s", exc)
    except KeyboardInterrupt:
        result["interrupted"] = True
        raise
    except RuntimeError as exc:
        result["fatal_error"] = str(exc)
        raise
    finally:
        result["finished_at"] = time.time()
        with store.conn:
            store.set_meta("last_collection", result)
    return result


def _job_error(store, result, job, exc, refresh_hours):
    message = str(exc)
    LOG.warning("  %s", message)
    result["errors"].append({"url": job["url"], "error": message})
    with store.conn:
        store.finish_job(job["url"], min(refresh_hours, 1), message)
