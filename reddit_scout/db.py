from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from .models import Item


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=15)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA secure_delete=ON")
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS items (
                id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, subreddit TEXT NOT NULL,
                kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
                permalink TEXT NOT NULL, created_utc REAL, score INTEGER,
                num_comments INTEGER, source TEXT NOT NULL, complete INTEGER NOT NULL,
                content_hash TEXT NOT NULL, first_seen REAL NOT NULL, last_seen REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS items_thread ON items(thread_id);
            CREATE TABLE IF NOT EXISTS analyses (
                item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
                analyzer TEXT NOT NULL, content_hash TEXT NOT NULL,
                payload TEXT NOT NULL, analyzed_at REAL NOT NULL,
                PRIMARY KEY(item_id, analyzer)
            );
            CREATE TABLE IF NOT EXISTS jobs (
                url TEXT PRIMARY KEY, kind TEXT NOT NULL, subreddit TEXT NOT NULL,
                priority REAL NOT NULL DEFAULT 0, ready_at REAL NOT NULL DEFAULT 0,
                last_finished REAL NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '', discovered REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS jobs_due ON jobs(kind, ready_at, last_finished);
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def upsert(self, item: Item, now: float | None = None) -> bool:
        """Called inside the caller's transaction; return whether evidence changed."""
        now = time.time() if now is None else now
        old = self.conn.execute("SELECT * FROM items WHERE id=?", (item.id,)).fetchone()
        # A feed preview must never replace a full thread body or its analysis.
        if old and old["complete"] and not item.complete:
            self.conn.execute("UPDATE items SET score=COALESCE(?,score),num_comments=COALESCE(?,num_comments) WHERE id=?",
                              (item.score, item.num_comments, item.id))
            return False
        changed = old is None or old["content_hash"] != item.content_hash
        if changed and old:
            self.conn.execute("DELETE FROM analyses WHERE item_id=?", (item.id,))
        values = item.as_dict() | {"content_hash": item.content_hash,
                                  "first_seen": old["first_seen"] if old else now, "last_seen": now}
        columns = list(values)
        updates = ",".join(f"{key}=excluded.{key}" for key in columns if key not in {"id", "first_seen"})
        self.conn.execute(f"INSERT INTO items ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)}) "
                          f"ON CONFLICT(id) DO UPDATE SET {updates}", list(values.values()))
        return changed

    def items(self) -> list[Item]:
        fields = set(Item.__dataclass_fields__)
        return [Item(**{key: row[key] for key in fields}) for row in self.conn.execute(
            "SELECT * FROM items ORDER BY first_seen,id")]

    def enqueue(self, url: str, kind: str, subreddit: str, priority: float = 0):
        self.conn.execute("""INSERT INTO jobs(url,kind,subreddit,priority,discovered)
            VALUES(?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET priority=MAX(priority,excluded.priority)""",
                          (url, kind, subreddit, priority, time.time()))

    def due(self, kind: str, subreddits: list[str], limit: int, *, exploration: float = 0) -> list[dict]:
        if limit <= 0 or not subreddits:
            return []
        placeholders = ",".join("?" for _ in subreddits)
        base = f"SELECT * FROM jobs WHERE kind=? AND ready_at<=? AND subreddit IN ({placeholders})"
        params = [kind, time.time(), *subreddits]
        if kind == "feed":
            rows = self.conn.execute(base + " ORDER BY last_finished,discovered,url LIMIT ?", [*params, limit]).fetchall()
            return [dict(row) for row in rows]
        # Reserve part of the work for oldest pending threads, including quiet titles.
        n_explore = min(limit, max(1, round(limit * exploration))) if exploration else 0
        oldest = self.conn.execute(base + " ORDER BY last_finished,discovered,url LIMIT ?", [*params, n_explore]).fetchall()
        selected = {row["url"]: dict(row) for row in oldest}
        rows = self.conn.execute(base + " ORDER BY priority DESC,last_finished,discovered,url LIMIT ?",
                                 [*params, limit + n_explore]).fetchall()
        for row in rows:
            if len(selected) >= limit:
                break
            selected.setdefault(row["url"], dict(row))
        return list(selected.values())

    def finish_job(self, url: str, refresh_hours: float, error: str = ""):
        now = time.time()
        self.conn.execute("""UPDATE jobs SET ready_at=?,last_finished=?,attempts=attempts+1,last_error=? WHERE url=?""",
                          (now + refresh_hours * 3600, now, error[:500], url))

    def remove(self, identifier: str):
        if identifier.startswith("t3_"):
            row = self.conn.execute("SELECT permalink FROM items WHERE id=?", (identifier,)).fetchone()
            if row:
                self.conn.execute("DELETE FROM jobs WHERE url=?", (row[0],))
            self.conn.execute("DELETE FROM items WHERE thread_id=?", (identifier,))
        else:
            self.conn.execute("DELETE FROM items WHERE id=?", (identifier,))

    def prune(self, days: int) -> int:
        cutoff = time.time() - days * 86400
        with self.conn:
            urls = self.conn.execute("SELECT permalink FROM items WHERE last_seen<? AND kind='post'", (cutoff,)).fetchall()
            self.conn.executemany("DELETE FROM jobs WHERE kind='thread' AND url=?", [(r[0],) for r in urls])
            count = self.conn.execute("DELETE FROM items WHERE last_seen<?", (cutoff,)).rowcount
        if count:
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return count

    def cached(self, item: Item, analyzer: str) -> dict | None:
        row = self.conn.execute("SELECT payload FROM analyses WHERE item_id=? AND analyzer=? AND content_hash=?",
                                (item.id, analyzer, item.content_hash)).fetchone()
        return json.loads(row[0]) if row else None

    def save_analysis(self, item: Item, analyzer: str, payload: dict):
        self.conn.execute("""INSERT INTO analyses VALUES(?,?,?,?,?) ON CONFLICT(item_id,analyzer)
            DO UPDATE SET content_hash=excluded.content_hash,payload=excluded.payload,analyzed_at=excluded.analyzed_at""",
                          (item.id, analyzer, item.content_hash, json.dumps(payload, ensure_ascii=False), time.time()))

    def get_meta(self, key: str, default=None):
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key: str, value):
        self.conn.execute("INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                          (key, json.dumps(value)))

    def stats(self) -> dict:
        rows = self.conn.execute("SELECT kind,COUNT(*) AS n FROM items GROUP BY kind").fetchall()
        counts = {row["kind"]: row["n"] for row in rows}
        return {"posts": counts.get("post", 0), "comments": counts.get("comment", 0),
                "analyses": self.conn.execute("SELECT COUNT(*) FROM analyses").fetchone()[0],
                "due_feeds": self.conn.execute("SELECT COUNT(*) FROM jobs WHERE kind='feed' AND ready_at<=?", (time.time(),)).fetchone()[0],
                "due_threads": self.conn.execute("SELECT COUNT(*) FROM jobs WHERE kind='thread' AND ready_at<=?", (time.time(),)).fetchone()[0],
                "job_errors": self.conn.execute("SELECT COUNT(*) FROM jobs WHERE last_error!=''").fetchone()[0],
                "last_collection": self.get_meta("last_collection"),
                "last_analysis": self.get_meta("last_analysis")}
