from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from urllib.parse import urlsplit


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def reddit_url(value: str) -> str:
    """Keep only Reddit community/comment URLs, without tracking parameters."""
    if value.startswith("/") and not value.startswith("//"):
        value = "https://www.reddit.com" + value
    parts = urlsplit(value)
    if (parts.scheme != "https" or parts.hostname not in {"reddit.com", "www.reddit.com"}
            or parts.username or parts.password or parts.port not in {None, 443}
            or not re.fullmatch(r"/r/[A-Za-z0-9_]+/comments/[a-z0-9]+(?:/[A-Za-z0-9_%.-]*)*/?", parts.path)):
        raise ValueError("Expected a Reddit community post/comment permalink")
    return "https://www.reddit.com" + parts.path.rstrip("/") + "/"


def timestamp(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)):
            result = float(value)
        else:
            result = datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        return result if 0 < result < 32503680000 else None
    except (TypeError, ValueError, OverflowError):
        return None


def count(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


@dataclass
class Item:
    id: str
    thread_id: str
    subreddit: str
    kind: str
    title: str
    body: str
    permalink: str
    created_utc: float | None = None
    score: int | None = None
    num_comments: int | None = None
    source: str = "reddit"
    complete: bool = False

    @property
    def text(self) -> str:
        # A comment's title is context, not evidence written by that commenter.
        return clean_text(f"{self.title} {self.body}" if self.kind == "post" else self.body)

    @property
    def content_hash(self) -> str:
        payload = [self.title, self.body, self.subreddit, self.kind, self.source]
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict, *, demo: bool = False) -> "Item":
        if not isinstance(raw, dict):
            raise ValueError("Each record must be an object")
        kind = raw.get("kind", "post")
        identifier = raw.get("id", "")
        thread_id = raw.get("thread_id", identifier if kind == "post" else "")
        if kind not in {"post", "comment"} or not isinstance(identifier, str):
            raise ValueError("Invalid record kind/id")
        if not re.fullmatch(r"t[13]_[a-z0-9]+", identifier) or not re.fullmatch(r"t3_[a-z0-9]+", thread_id):
            raise ValueError("IDs must be Reddit fullnames, e.g. t3_abc123 / t1_def456")
        if not identifier.startswith("t3_" if kind == "post" else "t1_"):
            raise ValueError("ID prefix does not match record kind")
        subreddit = str(raw.get("subreddit", "")).lower()
        if not re.fullmatch(r"[a-z0-9_]{2,30}", subreddit):
            raise ValueError("Invalid subreddit name")
        title, body = raw.get("title", ""), raw.get("body", "")
        if not isinstance(title, str) or not isinstance(body, str):
            raise ValueError("title and body must be strings")
        url = "" if demo else reddit_url(raw.get("permalink", ""))
        if not demo:
            parts = urlsplit(url).path.split("/")
            if parts[2].lower() != subreddit or "t3_" + parts[4] != thread_id:
                raise ValueError("Permalink does not match the subreddit/thread ID")
            if kind == "post" and identifier != thread_id:
                raise ValueError("A post's id must equal its thread_id")
            if kind == "comment" and (len(parts) < 8 or parts[6] != identifier[3:]):
                raise ValueError("Comment permalink does not match the comment ID")
        return cls(identifier, thread_id, subreddit, kind, clean_text(title)[:1000],
                   clean_text(body)[:40000], url, timestamp(raw.get("created_utc")),
                   count(raw.get("score")), count(raw.get("num_comments")),
                   "demo" if demo else "reddit", bool(raw.get("complete", False)))
