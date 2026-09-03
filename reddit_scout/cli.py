from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import logging
import logging.handlers
import sqlite3
import sys
import time
from pathlib import Path

from .analysis import analyze
from .browser import BrowserReader
from .collector import collect
from .config import ConfigError, load_config, validate
from .db import Store
from .demo import demo_items
from .locking import InstanceLock
from .models import Item
from .reports import write_reports

LOG = logging.getLogger(__name__)


def parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=argparse.SUPPRESS, help="TOML configuration file")
    common.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS, help="Include tracebacks in errors")
    root = argparse.ArgumentParser(description="Find recurring Reddit problems worth validating as paid tools.", parents=[common])
    subs = root.add_subparsers(dest="command", required=True)
    for name, help_text in (("login", "Sign in manually once; save a dedicated browser session"),
                            ("demo", "Run an offline, explicitly synthetic demonstration"),
                            ("doctor", "Check configuration and dependencies without accessing Reddit"),
                            ("status", "Show saved progress; works while the bot is running"),
                            ("report", "Rebuild HTML, Markdown, JSON, and CSV reports")):
        subs.add_parser(name, help=help_text, parents=[common])
    for name in ("run", "collect", "analyze"):
        command = subs.add_parser(name, parents=[common], help={"run": "Collect, analyze, and report",
                "collect": "Collect posts/comments and save progress", "analyze": "Analyze saved text and write reports"}[name])
        if name != "collect":
            command.add_argument("--local", action="store_true", help="Use local analysis even if a Gemini key is configured")
        if name != "analyze":
            command.add_argument("--subreddits", help="Comma-separated subset; e.g. smallbusiness,sysadmin,msp")
            command.add_argument("--max-feeds", type=int, help="Maximum feed/search pages this run")
            command.add_argument("--max-threads", type=int, help="Maximum thread pages this run")
            command.add_argument("--headless", action="store_true", help="Hide the browser (login always opens visibly)")
        if name == "run":
            command.add_argument("--watch", action="store_true", help="Repeat until Ctrl+C or the configured run limit")
            command.add_argument("--interval-minutes", type=float, help="Delay between completed cycles")
            command.add_argument("--max-cycles", type=int, default=0, help="0 = unlimited; applies with --watch")
            command.add_argument("--hours", type=float, default=0, help="Stop watch after this many hours; 0 = unlimited")
    command = subs.add_parser("import", parents=[common], help="Import normalized Reddit JSON/JSONL you are allowed to use")
    command.add_argument("file", type=Path)
    command.add_argument("--local", action="store_true")
    command = subs.add_parser("purge", parents=[common], help="Remove old local evidence and regenerate reports")
    command.add_argument("--older-than-days", type=int, help="Defaults to storage.retention_days")
    return root


def configure_logging(config: dict, verbose: bool):
    path = Path(config["storage"]["database"]).parent / "scout.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    handlers = [logging.StreamHandler(), logging.handlers.RotatingFileHandler(
        path, maxBytes=2_000_000, backupCount=2, encoding="utf-8")]
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", handlers=handlers, force=True)


def import_records(store: Store, path: Path) -> int:
    if path.stat().st_size > 20_000_000:
        raise ValueError("Import is larger than 20 MB; split it into smaller files")
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        rows = json.loads(text)
        if not isinstance(rows, list):
            raise ValueError("JSON import must be an array of normalized records")
        numbered = enumerate(rows, 1)
    else:
        numbered = ((i, json.loads(line)) for i, line in enumerate(text.splitlines(), 1) if line.strip())
    changed = 0
    with store.conn:
        for line, raw in numbered:
            try:
                item = Item.from_dict(raw)
            except (ValueError, TypeError) as exc:
                raise ValueError(f"Invalid import record {line}: {exc}") from exc
            if item.body.casefold() in {"[deleted]", "[removed]"}:
                store.remove(item.id)
            else:
                changed += store.upsert(item)
    return changed


def cycle(store: Store, config: dict, command: str) -> int:
    removed = store.prune(config["storage"]["retention_days"])
    if removed:
        LOG.info("Expired %d old observations and their analyses", removed)
    collection = None
    analysis = None
    collection_error = None
    if command in {"collect", "run"}:
        try:
            collection = collect(store, config)
        except RuntimeError as exc:
            collection_error = str(exc)
            LOG.error("%s", exc)
    if command in {"analyze", "run", "demo", "import"}:
        analysis = analyze(store, config)
    report = write_reports(store, config)
    print(f"\nSaved {report['count']} opportunities: {report['paths']['html']}")
    print("Also available: opportunities.md, opportunities.json, opportunities.csv")
    print("Progress:", json.dumps({k: v for k, v in store.stats().items() if not k.startswith("last_")}))
    if collection_error:
        return 1
    if collection and (collection["blocked"] or collection["errors"]):
        return 2
    if analysis and analysis["error"]:
        return 2
    return 0


def doctor(config: dict) -> int:
    available = importlib.util.find_spec("playwright") is not None
    print(f"Python: {sys.version.split()[0]}")
    print(f"Config: {config['config_path']}")
    print(f"Playwright: {'installed' if available else 'missing'}")
    print(f"Browser: {config['browser']['channel']} ({'hidden' if config['browser']['headless'] else 'visible'})")
    print(f"Profile: {config['browser']['profile']}")
    print(f"Configured communities: {len(config['collection']['subreddits'])}")
    import os
    print(f"Gemini key: {'configured' if os.getenv('GEMINI_API_KEY') else 'not set (local mode works)'}")
    print("Reddit API credentials: not required")
    if not available:
        print("Install: python -m pip install -r requirements.txt")
        print("Then:    python -m playwright install chromium")
    else:
        print("If Chromium is not installed: python -m playwright install chromium")
    return 0 if available else 1


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = parser().parse_args(argv)
    verbose = getattr(args, "verbose", False)
    try:
        config = load_config(getattr(args, "config", None))
        if getattr(args, "local", False):
            config["analysis"]["mode"] = "local"
        if getattr(args, "subreddits", None):
            config["collection"]["subreddits"] = [s.strip().removeprefix("r/") for s in args.subreddits.split(",")]
        for argument, section, field in (("max_feeds", "collection", "max_feed_pages_per_run"),
                                          ("max_threads", "collection", "max_threads_per_run"),
                                          ("interval_minutes", "watch", "interval_minutes")):
            if getattr(args, argument, None) is not None:
                config[section][field] = getattr(args, argument)
        if getattr(args, "headless", False):
            config["browser"]["headless"] = True
        validate(config)
        if args.command == "run":
            if args.max_cycles < 0 or args.hours < 0 or args.hours != args.hours or args.hours == float("inf"):
                raise ConfigError("--max-cycles and --hours must be finite nonnegative values")
            if not args.watch and (args.max_cycles or args.hours):
                raise ConfigError("--max-cycles / --hours require --watch")
        if args.command == "demo":
            config = copy.deepcopy(config)
            config["storage"]["database"] = str(Path(config["storage"]["database"]).parent / "demo.sqlite3")
            config["storage"]["reports"] = str(Path(config["storage"]["reports"]) / "demo")
            config["analysis"]["mode"] = "local"
        configure_logging(config, verbose)
        if args.command == "doctor":
            return doctor(config)
        if args.command == "login":
            with BrowserReader(config["browser"], login=True) as reader:
                reader.login()
            return 0
        if args.command == "status":
            with Store(config["storage"]["database"]) as store:
                print(json.dumps(store.stats(), indent=2, ensure_ascii=False))
            return 0
        with InstanceLock(config["storage"]["database"]), Store(config["storage"]["database"]) as store:
            if args.command == "purge":
                days = args.older_than_days if args.older_than_days is not None else config["storage"]["retention_days"]
                if not 0 <= days <= 365:
                    raise ConfigError("--older-than-days must be 0..365 (0 removes all current observations)")
                removed = store.prune(days)
                print(f"Removed {removed} observations")
                result = write_reports(store, config)
                print(f"Updated reports: {result['paths']['html']}")
                return 0
            if args.command == "demo":
                with store.conn:
                    for item in demo_items():
                        store.upsert(item)
                print("SYNTHETIC DEMO: no Reddit requests, no model requests, no real market evidence.")
            if args.command == "import":
                print(f"Imported {import_records(store, args.file)} new/changed observations")
            start, cycles, outcome = time.monotonic(), 0, 0
            while True:
                outcome = cycle(store, config, args.command)
                cycles += 1
                if args.command != "run" or not args.watch:
                    break
                if (store.get_meta("last_collection", {}) or {}).get("blocked") or outcome == 1:
                    LOG.error("Watch stopped; resolve browser access/setup and rerun the same command to resume")
                    break
                if args.max_cycles and cycles >= args.max_cycles:
                    break
                remaining = args.hours * 3600 - (time.monotonic() - start) if args.hours else float("inf")
                if remaining <= 0:
                    break
                delay = min(config["watch"]["interval_minutes"] * 60, remaining)
                LOG.info("Cycle %d saved. Next cycle in %.1f minutes; Ctrl+C safely stops.", cycles, delay / 60)
                until = time.monotonic() + delay
                while time.monotonic() < until:
                    time.sleep(min(15, max(0, until - time.monotonic())))
                if args.hours and time.monotonic() - start >= args.hours * 3600:
                    break
            return outcome
    except KeyboardInterrupt:
        print("\nStopped. Committed work is saved; rerun the same command to resume.")
        return 130
    except (ConfigError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        LOG.error("%s", exc, exc_info=verbose)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
