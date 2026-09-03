from __future__ import annotations

import copy
import math
import os
import re
import tomllib
from pathlib import Path


DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config.toml"


class ConfigError(ValueError):
    pass


def load_env(path: Path) -> None:
    """Small .env reader: no evaluation/interpolation; real environment wins."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not re.fullmatch(r"[A-Z_][A-Z0-9_]*", key.strip()):
            raise ConfigError("Invalid .env line; expected NAME=value (values are never executed)")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def load_config(path: str | Path | None = None) -> dict:
    selected = Path(path or DEFAULT_PATH).expanduser().resolve()
    try:
        defaults = tomllib.loads(DEFAULT_PATH.read_text(encoding="utf-8-sig"))
        custom = tomllib.loads(selected.read_text(encoding="utf-8-sig"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"Cannot read config {selected}: {exc}") from exc
    config = copy.deepcopy(defaults)
    for section, values in custom.items():
        if section not in config or not isinstance(values, dict):
            raise ConfigError(f"Unknown configuration section: {section}")
        for key, value in values.items():
            if key not in config[section]:
                raise ConfigError(f"Unknown configuration setting: {section}.{key}")
            config[section][key] = value
    validate(config)
    for section, key in (("storage", "database"), ("storage", "reports"), ("browser", "profile")):
        config[section][key] = str((selected.parent / config[section][key]).resolve())
    config["config_path"] = str(selected)
    load_env(selected.parent / ".env")
    return config


def validate(c: dict) -> None:
    for section, key in (("storage", "database"), ("storage", "reports"), ("browser", "profile")):
        if not isinstance(c[section][key], str) or not c[section][key].strip():
            raise ConfigError(f"{section}.{key} must be a nonempty path")
    bounds = {
        "storage": {"retention_days": (1, 365)},
        "browser": {"navigation_timeout_seconds": (5, 120), "page_delay_seconds": (1, 120),
                    "scroll_delay_seconds": (1, 30), "max_scrolls": (0, 50),
                    "comments_per_post": (0, 200)},
        "collection": {"posts_per_feed": (1, 300), "max_feed_pages_per_run": (1, 200),
                       "max_threads_per_run": (0, 1000), "refresh_hours": (1, 720),
                       "exploration_fraction": (0, 1), "min_text_characters": (1, 1000)},
        "analysis": {"batch_size": (1, 20), "max_items_per_run": (1, 10000),
                     "max_requests_per_run": (1, 1000), "min_request_interval_seconds": (0, 300),
                     "max_input_characters": (500, 20000), "min_independent_threads": (1, 100),
                     "report_limit": (1, 500)},
        "watch": {"interval_minutes": (1, 10080)},
    }
    real_values = {"page_delay_seconds", "scroll_delay_seconds", "refresh_hours",
                   "exploration_fraction", "min_request_interval_seconds", "interval_minutes"}
    for section, settings in bounds.items():
        for key, (minimum, maximum) in settings.items():
            value = c[section][key]
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or not minimum <= value <= maximum
                    or (key not in real_values and not isinstance(value, int))):
                raise ConfigError(f"{section}.{key} must be a number in [{minimum}, {maximum}]"
                                  + (" (integer)" if key not in real_values else ""))
    for section, key in (("collection", "subreddits"), ("collection", "feeds"),
                         ("collection", "search_queries"), ("analysis", "models")):
        values = c[section][key]
        if not isinstance(values, list) or any(not isinstance(x, str) or not x.strip() for x in values):
            raise ConfigError(f"{section}.{key} must be an array of nonempty strings")
    subs = c["collection"]["subreddits"]
    if not subs or any(not re.fullmatch(r"[A-Za-z0-9_]{2,30}", s) for s in subs):
        raise ConfigError("Use subreddit names without r/, spaces, slashes, or URL parameters")
    c["collection"]["subreddits"] = list(dict.fromkeys(s.lower() for s in subs))
    if any(f not in {"new", "top", "hot"} for f in c["collection"]["feeds"]):
        raise ConfigError("collection.feeds supports new, top, and hot")
    if not c["collection"]["feeds"] and not c["collection"]["search_queries"]:
        raise ConfigError("Configure at least one feed or search query")
    if any(len(q) > 300 for q in c["collection"]["search_queries"]):
        raise ConfigError("Search queries must be 300 characters or fewer")
    if c["collection"]["time_filter"] not in {"hour", "day", "week", "month", "year", "all"}:
        raise ConfigError("Invalid collection.time_filter")
    if c["browser"]["channel"] not in {"chromium", "chrome", "msedge"}:
        raise ConfigError("browser.channel must be chromium, chrome, or msedge")
    if not isinstance(c["browser"]["headless"], bool):
        raise ConfigError("browser.headless must be true or false")
    if c["analysis"]["mode"] not in {"auto", "local", "gemini"}:
        raise ConfigError("analysis.mode must be auto, local, or gemini")
    if not c["analysis"]["models"] or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", m) for m in c["analysis"]["models"]):
        raise ConfigError("analysis.models must contain valid Gemini model IDs")
