from __future__ import annotations

import logging
import time
from pathlib import Path
from urllib.parse import urlsplit

from .locking import InstanceLock

LOG = logging.getLogger(__name__)
DOM_SCRIPT = Path(__file__).with_name("dom.js").read_text(encoding="utf-8")


class AccessBlocked(RuntimeError):
    """Stop the entire collection session; never cycle routes/accounts to evade it."""


class PageUnavailable(RuntimeError):
    pass


class LayoutChanged(RuntimeError):
    pass


class BrowserReader:
    def __init__(self, config: dict, *, login: bool = False):
        self.config = config
        self.login_mode = login
        self.runtime = self.context = self.page = self.lock = None
        self.last_navigation = 0.0

    def __enter__(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("Install the browser dependency: python -m pip install -r requirements.txt; "
                               "then python -m playwright install chromium") from exc
        self.lock = InstanceLock(self.config["profile"])
        self.lock.__enter__()
        try:
            profile = Path(self.config["profile"])
            profile.mkdir(parents=True, exist_ok=True)
            try:
                profile.chmod(0o700)
            except OSError:
                pass
            self.runtime = sync_playwright().start()
            options = {"user_data_dir": str(profile), "headless": False if self.login_mode else self.config["headless"],
                       "viewport": {"width": 1440, "height": 1000}, "accept_downloads": False}
            if self.config["channel"] != "chromium":
                options["channel"] = self.config["channel"]
            self.context = self.runtime.chromium.launch_persistent_context(**options)
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
            self.page.set_default_timeout(self.config["navigation_timeout_seconds"] * 1000)
            return self
        except Exception as exc:
            self.__exit__(None, None, None)
            reason = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            raise RuntimeError("Could not open the dedicated browser profile. Install Chromium with "
                               "'python -m playwright install chromium', or choose an installed chrome/msedge "
                               "channel in config.toml. Close other bot windows using this profile. "
                               f"Browser error: {reason}") from exc

    def __exit__(self, *_):
        try:
            if self.context:
                self.context.close()
        finally:
            try:
                if self.runtime:
                    self.runtime.stop()
            finally:
                if self.lock:
                    self.lock.__exit__(None, None, None)

    def login(self):
        self.page.goto("https://www.reddit.com/login/", wait_until="domcontentloaded")
        print("Sign in yourself in the opened Reddit window. Your password is never read by this bot.")
        input("Once your Reddit feed is visible, return here and press Enter: ")
        if urlsplit(self.page.url).path.startswith(("/login", "/register")):
            raise AccessBlocked("The browser is still on Reddit's sign-in page. Run 'python bot.py login' again.")
        data = self.page.evaluate(DOM_SCRIPT, {})
        if data["block"]:
            raise AccessBlocked(data["block"])
        print("Browser session saved. Next: python bot.py run")

    def navigate(self, url: str):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != "www.reddit.com" or not parsed.path.startswith("/r/"):
            raise ValueError("Collection navigation must be a Reddit community page")
        delay = self.config["page_delay_seconds"] - (time.monotonic() - self.last_navigation)
        if delay > 0:
            time.sleep(delay)
        self.last_navigation = time.monotonic()
        try:
            response = self.page.goto(url, wait_until="domcontentloaded")
        except Exception as exc:
            raise PageUnavailable(f"Navigation failed ({type(exc).__name__}); run again to retry this saved job") from exc
        if response and response.status in {401, 403, 429}:
            raise AccessBlocked(f"Reddit returned HTTP {response.status}; collection paused. "
                                "Check access in the dedicated browser with 'python bot.py login'.")
        if response and response.status >= 400:
            raise PageUnavailable(f"Reddit returned HTTP {response.status}")
        try:
            self.page.locator("shreddit-post, main article, main search-telemetry-tracker, main h2 a[href*='/comments/']").first.wait_for(
                state="attached", timeout=min(15000, self.config["navigation_timeout_seconds"] * 1000))
        except Exception:
            # Empty results and challenge pages are classified by their rendered text below.
            pass

    def snapshots(self, url: str, *, detail: bool = False, thread_id: str = ""):
        self.navigate(url)
        seen, stagnant = set(), 0
        for index in range(self.config["max_scrolls"] + 1):
            try:
                data = self.page.evaluate(DOM_SCRIPT, {"detail": detail, "thread_id": thread_id})
            except Exception as exc:
                raise PageUnavailable(f"Could not read rendered page ({type(exc).__name__})") from exc
            if data["block"]:
                raise AccessBlocked(data["block"] + "; use 'python bot.py login' before resuming.")
            if not data["posts"] and not data["removed_ids"] and not data["empty"]:
                raise LayoutChanged("Reddit loaded but no recognizable post cards were found. "
                                    "Check the browser for a dialog, or update reddit_scout/dom.js. "
                                    "The feed was NOT recorded as successfully empty.")
            yield data
            identifiers = {row["id"] for row in (data["comments"] if detail else data["posts"])}
            new_ids = identifiers - seen
            seen.update(identifiers)
            stagnant = 0 if new_ids else stagnant + 1
            if data["empty"] or stagnant >= 2:
                break
            if detail and (len(seen) >= self.config["comments_per_post"] or self.config["comments_per_post"] == 0):
                break
            if index < self.config["max_scrolls"]:
                self.page.mouse.wheel(0, 1000)
                self.page.wait_for_timeout(self.config["scroll_delay_seconds"] * 1000)
