# Reddit Opportunity Scout

Find recurring problems people discuss on Reddit, then turn the strongest evidence into a shortlist of paid software ideas.

**No Reddit API credentials required.** This bot reads Reddit's rendered website using a browser on your PC. Public pages can work without signing in. If needed, sign in manually once in the bot's dedicated browser; it reuses that local session.

It collects post text and loaded comments across configurable communities, spots complaints and expensive workarounds, groups similar problems, and writes a ranked report with source links, exact excerpts, possible buyers, narrow tool ideas, and unvalidated price experiments. It never posts, votes, sends DMs, or asks for your password in a terminal.

## Quick start

Requires **Python 3.11 or newer**. Run these commands in this repository.

```bash
python -m venv .venv
```

Activate it on Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Or macOS/Linux:

```bash
source .venv/bin/activate
```

Then install and try it:

```bash
python -m pip install -r requirements.txt
python -m playwright install chromium
python bot.py demo
python bot.py run --subreddits smallbusiness,sysadmin,msp --max-feeds 3 --max-threads 5
```

If PowerShell prevents activation, use `.\.venv\Scripts\python.exe` in place of `python`. If your Python command is `py`, use `py -3 -m venv .venv` to create the environment.

The demo needs **no dependencies or keys** and uses explicitly synthetic examples. Open `reports/demo/opportunities.html` to see the output. Demo data lives in its own database and never enters the live research report.

For real runs, open **`reports/opportunities.html`**. The same report is available as Markdown, JSON, and CSV in `reports/`. HTML is a local file: open it directly in your browser, with no server required.

### Sign in if needed

```bash
python bot.py login
```

A browser opens. Sign in yourself on Reddit, wait until your feed is visible, then return to the terminal and press Enter. Close that bot window before starting another command. The bot keeps a separate profile in `data/browser-profile/`; it does not copy cookies from your everyday browser or read saved passwords.

Then rerun your collection command. If you prefer an installed Chrome or Edge, change `browser.channel` in `config.toml` to `"chrome"` or `"msedge"`. The default uses Playwright's installed Chromium.

If Reddit shows a CAPTCHA, security block, login gate, HTTP 401/403/429, or restricted community, the bot pauses collection and preserves its queue. An account does not guarantee that Reddit will accept automation. It does not bypass access checks or switch identities. Resolve normal sign-in in the dedicated browser and rerun when access is available.

## Run across your communities

Edit `config.toml`. It starts with 22 communities covering small business, stores, agencies, finance operations, sales, IT, and property businesses. Add or remove communities based on the buyers you want to serve.

```bash
# One bounded collection + analysis + report cycle
python bot.py run

# Keep going, with progress saved between cycles; stop with Ctrl+C
python bot.py run --watch

# Run daytime for up to 12 hours, with one hour between cycles
python bot.py run --watch --hours 12 --interval-minutes 60

# Focus on a few communities with a larger thread budget
python bot.py run --subreddits ecommerce,shopify,EtsySellers --max-feeds 8 --max-threads 50

# Show progress while the bot is running
python bot.py status
```

The watch duration is checked **between cycles**; an in-progress bounded cycle is allowed to finish. Your PC must be awake. The process does not install a background service or a cloud schedule. Restart the same command after shutting down your PC; there is no special resume flag.

Default cycle limits are 24 feed/search pages, 25 posts per feed, 30 thread pages, and up to 40 loaded comments per thread. It checks both new and top posts plus two targeted searches. Feeds rotate through the configured communities across runs, so a small budget does not keep restarting at the first subreddit. Unvisited threads stay queued; 25% of the thread budget explores older pending threads, including titles without obvious pain words.

The default repeat delay is six hours and the default refresh window for completed jobs is 24 hours. Repeating sooner does not immediately rescrape every completed page. Change these in the config if you want a different pace.

Pagination uses bounded scrolling of the website. It reads comments Reddit has loaded, including loaded nested replies; it does not expand every collapsed reply or retrieve an entire historical archive. Search results can provide only a title until the thread is opened. Deleted/removed content observed during a revisit is removed locally, along with its cached analysis. Changes in Reddit's layout fail visibly rather than being silently reported as an empty successful scrape.

## Optional Gemini analysis

Local analysis works out of the box. For better interpretation of nuanced complaints and grouping into specific software jobs, copy `.env.example` to `.env` and set:

```dotenv
GEMINI_API_KEY=your_key_here
```

The default `analysis.mode = "auto"` uses Gemini when that key is set. Choose `"local"` to keep analysis on your PC, or use:

```bash
python bot.py run --local
python bot.py analyze --local
```

Gemini receives candidate text excerpts and the thread title for context. No browser credentials, cookies, or author fields are sent. It is optional inference, not model training; choose a provider/data setup appropriate for the content you use.

The configured chain is `gemini-3.7-flash`, `gemini-3.6-flash`, `gemini-3.5-flash`, then `gemini-2.5-flash`. Model availability and free quotas depend on your Google project; edit the chain to match what you can access. No other paid service is silently enabled. A key on a billing-enabled project can incur charges; use an appropriate project and request budget.

By default a run analyzes up to 180 candidates, in batches of six, with at most 35 model request attempts and six seconds between attempts. Failed attempts count toward that budget. Rate-limited/unavailable models get saved cooldowns. If all models fail, local results remain available and unfinished Gemini work stays pending for the next run. Successfully analyzed, unchanged text is cached across restarts; edited text invalidates its old analysis. A model response is accepted only if every source ID is present and all quoted evidence matches the source text exactly.

## What the ranking means

The score is an explainable prioritization heuristic, **not a probability of success or proof that anyone will buy**. It emphasizes:

- The same specific problem appearing in separate threads.
- First-person offers to pay or actual spending on current solutions.
- Quantified time costs, recurring work, severity, and buyer context.
- A task that a narrow software product could plausibly improve.

An active discussion with 100 replies is still only one independent thread. Identical reposted text is deduplicated. Upvotes do not increase the score. Counts are **threads, not unique people**; the bot deliberately does not build user profiles. Keyword rules are English-focused and can miss nuance; Gemini grouping also needs review.

Single-thread/insufficiently corroborated ideas are labeled **early signals** and capped at 45/100. Promotional findings are excluded. Price objections lower the score. Salaries, company revenue, invoice balances, and hours spent are not treated as willingness to pay.

Each idea includes source excerpts and links, a possible buyer, a narrow MVP scope, mentioned alternatives, and validation questions. The default USD price bands are explicitly **illustrative subscription experiments**, not prices extracted from Reddit. Test them only after interviews show enough monthly value. Start with one painful job and ask for a paid pilot before building a broad product. The report makes no claims about market size, search volume, competition, or guaranteed revenue.

## Other commands

```bash
python bot.py doctor                 # Config/dependency check; no Reddit requests
python bot.py collect                # Collection only; cached analyses remain usable
python bot.py analyze                # Process saved data and rebuild reports
python bot.py report                 # Rebuild reports from cached analyses
python bot.py run --headless         # Hide the browser after verifying your setup
python bot.py run --config my.toml   # Paths are relative to that config file
python bot.py purge --older-than-days 7
```

`report` does not invoke Gemini or create new analyses. Run `analyze` first if you only collected/imported fresh data.

You can import Reddit records you are allowed to use as a JSON array or JSONL (one object per line):

```bash
python bot.py import my-reddit-export.jsonl --local
```

Each record needs `id` (`t3_...` post or `t1_...` comment), `thread_id` (`t3_...`), `subreddit`, `kind` (`post`/`comment`), `title`, `body`, and a matching Reddit `permalink`. Optional fields: `created_utc` (Unix timestamp or ISO date), `score`, `num_comments`, and `complete` (true for a full body). For comments, the title is context only. Author fields are discarded. Imports are limited to 20 MB per file and roll back if a record is invalid.

## Saved state and troubleshooting

- `data/scout.sqlite3`: collected text, queued URLs, analysis cache, and progress. Pages and their discovered jobs commit together; a crash cannot advance a checkpoint past unsaved data.
- `data/browser-profile/`: your dedicated local browser session. Keep it private. The default data/profile paths, reports, `.env`, logs, and databases are excluded from Git.
- `data/scout.log`: rotating timestamped logs with per-feed/thread progress and errors. Credentials and model payloads are not logged.
- `reports/opportunities.*`: the latest report, replaced atomically per file. JSON includes collection errors, analysis backlog, scoring components, and all selected source evidence.

The default retention window is 30 days since an observation was last fetched. Cleanup runs at the start of each work cycle; it cannot run while your PC is off. Revisiting a deleted source removes its local evidence and analyses, and the next report rebuild removes it from the current exports. This is not continuous deletion monitoring. Delete any copies you made elsewhere separately. `purge --older-than-days 0` removes all current observations and rebuilds empty reports; it leaves your sign-in and feed configuration in place.

Use only content you are allowed to access and follow Reddit's applicable terms and community rules. Keep reports for your own research; review full source context before contacting anyone or acting on an idea.

Common fixes:

- **No Chromium executable:** run `python -m playwright install chromium` in the same environment where you installed the requirements.
- **Another bot/profile is in use:** stop the other bot/login window. OS locks release automatically after a crashed process; do not delete a live profile lock.
- **No posts found / layout changed:** check the visible browser for a dialog. Inspect `reddit_scout/dom.js` if Reddit changed its post or search markup. The failing URL and reason remain in status/logs.
- **Some communities fail:** status and reports show partial coverage. Other ordinary page errors can continue; an access/security block pauses the entire collection session.
- **No new model results:** check `gemini_pending` and `error` in status, then the key, model names, quotas, and request budget. Rerun `analyze` when available.
- **Only a few communities covered:** increase `--max-feeds`, or keep running; queued feeds rotate and finished jobs honor the refresh window.

Exit codes: `0` completed, `1` setup/configuration/fatal failure, `2` partial results or access/model pause, `130` interrupted with Ctrl+C. Add `--verbose` for tracebacks.

## Development

```bash
python -m unittest discover -s tests -t . -v
python bot.py demo
```

The tests use synthetic fixtures and fake transports; they make no Reddit or Gemini requests and need no keys. They cover crash/restart persistence, queue rotation, duplicate evidence, deletion/cache invalidation, payment classification, model failures/budgets, citation validation, and safe exports. The DOM extractor was also checked against live Reddit feed, search, and thread markup during implementation. Sign-in and full local-browser collection must be verified on your own PC.

Technical references: [Playwright browser installation](https://playwright.dev/python/docs/browsers), [persistent browser contexts](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context), [Gemini model IDs](https://ai.google.dev/gemini-api/docs/models), and [Gemini content generation](https://ai.google.dev/api/generate-content).
