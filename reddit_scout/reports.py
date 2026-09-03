from __future__ import annotations

import csv
import html
import io
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .analysis import build_opportunities
from .models import reddit_url


def atomic_write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=path.parent,
                                         prefix=".scout-", delete=False) as handle:
            name = handle.name
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def safe_cell(value) -> str:
    value = str(value)
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")) else value


def md(value) -> str:
    return re.sub(r"([\\`*_[\]<>|])", r"\\\1", str(value).replace("\n", " "))


def source_link(e: dict, *, markdown: bool = False) -> str:
    label = f"r/{e['subreddit']} · {e['id']}"
    if e["source"] == "demo":
        return md(label + " · SYNTHETIC") if markdown else html.escape(label + " · SYNTHETIC")
    try:
        url = reddit_url(e["url"])
    except ValueError:
        return md(label) if markdown else html.escape(label)
    return f"[{md(label)}]({url})" if markdown else f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">{html.escape(label)}</a>'


def write_reports(store, config: dict) -> dict:
    opportunities = build_opportunities(store, config)
    stats = store.stats()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    demo = bool(opportunities) and all(o["synthetic"] for o in opportunities)
    data = {"schema_version": 1, "generated_at": now, "synthetic": demo,
            "notice": "SYNTHETIC DEMO — no real Reddit market evidence" if demo else
                      "Research leads from a bounded sample; prices and buyer segments are hypotheses, not validated demand.",
            "stats": stats, "opportunities": opportunities}
    output = Path(config["storage"]["reports"])
    paths = {"json": output / "opportunities.json", "markdown": output / "opportunities.md",
             "csv": output / "opportunities.csv", "html": output / "opportunities.html"}
    # No source snippets live in persistent report history: replace the current files.
    atomic_write(paths["json"], json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    atomic_write(paths["markdown"], render_markdown(data))
    atomic_write(paths["csv"], render_csv(opportunities))
    atomic_write(paths["html"], render_html(data))
    return {"count": len(opportunities), "paths": {k: str(v) for k, v in paths.items()}}


def render_markdown(data: dict) -> str:
    stats = data["stats"]
    lines = ["# Reddit opportunity report", "", f"Generated: {data['generated_at']}", "", data["notice"], "",
             f"Collected {stats['posts']} posts and {stats['comments']} comments. "
             f"Queued: {stats['due_feeds']} feeds, {stats['due_threads']} threads.", ""]
    collection = stats.get("last_collection") or {}
    if collection.get("blocked"):
        lines += [f"Collection paused: {md(collection['blocked'])}", ""]
    if collection.get("fatal_error"):
        lines += [f"Collection error: {md(collection['fatal_error'])}", ""]
    if collection.get("interrupted"):
        lines += ["The last collection was interrupted; committed observations are saved.", ""]
    if collection.get("errors"):
        lines += [f"Collection had {len(collection['errors'])} errors; see status/logs. Coverage is partial.", ""]
    analysis = stats.get("last_analysis") or {}
    if analysis.get("gemini_pending"):
        lines += [f"Gemini analysis pending for {analysis['gemini_pending']} records; local findings remain available.", ""]
    if not data["opportunities"]:
        lines += ["No qualifying problems found yet. Collect more threads or review your subreddit selection.", ""]
    for index, o in enumerate(data["opportunities"], 1):
        lines += [f"## {index}. {md(o['problem'])}", "",
                  f"Score: **{o['score']}/100** · {o['stage'].replace('_', ' ')} · "
                  f"{o['independent_threads']} independent threads · {o['mentions']} mentions", "",
                  f"**Possible buyer:** {md(o['audience'])}. {md(o['audience_basis'])}.", "",
                  f"**Tool idea:** {md(o['tool_idea'])}.", "",
                  f"**Unvalidated price experiment:** {o['pricing_hypothesis']['test_range']}. "
                  f"{o['pricing_hypothesis']['basis']}", "",
                  f"**Payment evidence:** {o['payment_evidence']['explicit_willingness_threads']} threads with explicit willingness; "
                  f"{o['payment_evidence']['existing_spend_threads']} with existing spend.", "",
                  "**MVP scope**", "", *[f"- {md(s)}" for s in o["mvp_scope"]], "",
                  "**Source evidence**", ""]
        for e in o["evidence"][:12]:
            lines += [f"- {source_link(e, markdown=True)} ({e['kind']}; {e['method']} analysis): “{md(e['quote'])}”"]
            if e["money_quote"]:
                lines += [f"  Payment signal ({e['money_kind']}): “{md(e['money_quote'])}”"]
            if e["negative_pay_quote"]:
                lines += [f"  Price objection: “{md(e['negative_pay_quote'])}”"]
        if len(o["evidence"]) > 12:
            lines += [f"- {len(o['evidence']) - 12} more evidence records in opportunities.json."]
        lines += ["", "**Mentioned alternatives:** " + (", ".join(md(x) for x in o["mentioned_alternatives"]) or "None recorded"), "",
                  "**What to validate**", "", *[f"- {md(s)}" for s in o["validation_steps"]], "",
                  "**Uncertainties**", "", *[f"- {md(s)}" for s in o["risks"]], ""]
    return "\n".join(lines)


def render_csv(opportunities: list[dict]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(["rank", "score", "stage", "problem", "possible_buyer", "tool_idea", "threads", "mentions",
                     "explicit_willingness_threads", "existing_spend_threads", "price_experiment_UNVALIDATED_USD",
                     "subreddits", "source_urls", "synthetic"])
    for rank, o in enumerate(opportunities, 1):
        writer.writerow([safe_cell(v) for v in [rank, o["score"], o["stage"], o["problem"], o["audience"], o["tool_idea"],
            o["independent_threads"], o["mentions"], o["payment_evidence"]["explicit_willingness_threads"],
            o["payment_evidence"]["existing_spend_threads"], o["pricing_hypothesis"]["test_range"],
            "; ".join(o["subreddits"]), "; ".join(e["url"] for e in o["evidence"] if e["url"]), o["synthetic"]]])
    return "\ufeff" + buffer.getvalue()


def render_html(data: dict) -> str:
    esc = html.escape
    cards = []
    for o in data["opportunities"]:
        evidence = []
        for e in o["evidence"][:12]:
            payment = f'<p class="money">{esc(e["money_kind"].replace("_", " "))}: {esc(e["money_quote"])}</p>' if e["money_quote"] else ""
            negative = f'<p class="warning">Price objection: {esc(e["negative_pay_quote"])}</p>' if e["negative_pay_quote"] else ""
            evidence.append(f'<li><div>{source_link(e)} <span class="muted">{e["kind"]} · {e["method"]}</span></div>'
                            f'<blockquote>{esc(e["quote"])}</blockquote>{payment}{negative}</li>')
        evidence_note = f'<p class="muted">{len(o["evidence"]) - 12} more records in the JSON export.</p>' if len(o["evidence"]) > 12 else ""
        risks = "".join(f"<li>{esc(s)}</li>" for s in o["risks"])
        checks = "".join(f"<li>{esc(s)}</li>" for s in o["validation_steps"])
        scope = "".join(f"<li>{esc(s)}</li>" for s in o["mvp_scope"])
        score_details = "".join(f'<tr><td>{esc(k.replace("_", " "))}</td><td>{v}</td></tr>' for k, v in o["score_components"].items())
        search = esc(" ".join([o["problem"], o["audience"], o["tool_idea"], *o["subreddits"]]).lower(), quote=True)
        cards.append(f'''<article class="card" data-search="{search}" data-stage="{o['stage']}">
<div class="card-head"><span class="badge">{o['stage'].replace('_',' ')}</span><strong class="score">{o['score']}<small>/100</small></strong></div>
<h2>{esc(o['problem'])}</h2><p class="muted">{o['independent_threads']} independent threads · {o['mentions']} mentions · {esc(', '.join('r/'+s for s in o['subreddits']))}</p>
<p><b>Possible buyer:</b> {esc(o['audience'])}</p><p>{esc(o['tool_idea'])}</p>
<div class="price"><b>Price experiment: {esc(o['pricing_hypothesis']['test_range'])}</b><span>Unvalidated hypothesis · USD</span></div>
<p class="muted">{esc(o['pricing_hypothesis']['basis'])}</p>
<p class="money">{o['payment_evidence']['explicit_willingness_threads']} threads with explicit willingness to pay · {o['payment_evidence']['existing_spend_threads']} with existing spend</p>
<details open><summary>Read the evidence</summary><ul class="evidence">{''.join(evidence)}</ul>{evidence_note}</details>
<details><summary>MVP and validation</summary><ul>{scope}</ul><ol>{checks}</ol><p><b>Mentioned alternatives:</b> {esc(', '.join(o['mentioned_alternatives']) or 'None recorded')}</p></details>
<details><summary>Uncertainties and score</summary><ul>{risks}</ul><table>{score_details}</table><p class="muted">Early signals are capped at 45. Upvotes do not increase the score.</p></details></article>''')
    stats = data["stats"]
    last = stats.get("last_collection") or {}
    warning = esc(last.get("blocked") or last.get("fatal_error") or
                  ("Last collection was interrupted; committed work is saved." if last.get("interrupted") else "") or
                  (f"Partial coverage: {len(last['errors'])} collection errors. Check the console/status." if last.get("errors") else ""))
    analysis = stats.get("last_analysis") or {}
    if analysis.get("error"):
        warning += " " + esc("Gemini paused: " + analysis["error"])
    return '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Reddit opportunity report</title><style>
:root{font-family:system-ui,-apple-system,Segoe UI,sans-serif;color:#172923;background:#f3f6f3;line-height:1.55}*{box-sizing:border-box}body{margin:0}header,main{max-width:1100px;margin:auto;padding:32px 24px}header{padding-bottom:12px}h1{font-size:clamp(28px,5vw,44px);line-height:1.2;margin:12px 0}.eyebrow{font-size:12px;text-transform:uppercase;letter-spacing:.15em;font-weight:700;color:#356749}.muted,small{color:#5c6f66}.notice{padding:14px 18px;background:#e4eee5;border-radius:10px}.warning{color:#874716}.toolbar{display:flex;gap:12px;flex-wrap:wrap;margin:24px 0 0}input,select{font:inherit;padding:12px;border:1px solid #b8cabb;border-radius:8px;background:white}input{flex:1;min-width:180px}a{color:#145a40;text-underline-offset:3px}.card{background:white;border:1px solid #d7e2d9;border-radius:14px;padding:26px;margin-bottom:22px;box-shadow:0 3px 12px #10201805}.card[hidden]{display:none}.card-head{display:flex;align-items:center;justify-content:space-between;gap:16px}.badge{font-size:12px;text-transform:uppercase;letter-spacing:.05em;background:#edf2ec;padding:5px 10px;border-radius:5px}.score{font-size:27px;color:#1d6245}.score small{font-size:13px;font-weight:400}h2{font-size:23px;margin:12px 0}.price{display:flex;gap:12px;flex-wrap:wrap;background:#f4f7f1;padding:12px 16px;border-left:3px solid #6c8d46;margin-top:20px}.price span{font-size:13px;align-self:center;color:#626e59}.money{color:#236046}.evidence{list-style:none;padding:0}.evidence li{padding:14px 0;border-bottom:1px solid #edf0eb;overflow-wrap:anywhere}blockquote{margin:8px 0;padding-left:14px;border-left:2px solid #d5dfd4;color:#34483c}details{margin-top:16px}summary{cursor:pointer;font-weight:650}table{border-collapse:collapse;font-size:14px}td{padding:5px 28px 5px 0}footer{text-align:center;color:#69796e;padding:12px 24px 40px}.downloads{display:flex;gap:18px;flex-wrap:wrap}.empty{padding:24px;background:white;border-radius:12px}li{margin:5px 0}@media(max-width:550px){.card{padding:19px}header,main{padding:22px 16px}}
</style></head><body><header><div class="eyebrow">Problem discovery / Research shortlist</div><h1>Find problems worth solving.</h1>
<p class="muted">Evidence from conversations, with clear gaps to validate before building.</p>''' + f'''
<p class="notice">{esc(data['notice'])}</p><p class="muted">{stats['posts']} posts · {stats['comments']} comments · Generated {esc(data['generated_at'])}</p>
<p class="warning">{warning}</p><div class="downloads"><a href="opportunities.md">Markdown</a><a href="opportunities.json">JSON</a><a href="opportunities.csv">CSV</a></div>
<div class="toolbar"><input id="search" aria-label="Filter opportunities" placeholder="Filter by problem, buyer, or subreddit"><select id="stage" aria-label="Evidence filter"><option value="">All evidence</option><option value="recurring_signal">Recurring signals</option><option value="early_signal">Early signals</option></select></div></header>
<main><p id="count" class="muted" aria-live="polite">{len(cards)} opportunities</p>{''.join(cards)}<p id="empty" class="empty" {'hidden' if cards else ''}>No matching opportunities yet. Collect more threads or adjust the filter.</p></main>
<footer>Buyer segments and prices are hypotheses. Counts refer to sampled threads, not unique people.</footer>
''' + '''<script>
const input=document.getElementById('search'),stage=document.getElementById('stage'),cards=[...document.querySelectorAll('.card')];
function filter(){let n=0;for(const card of cards){card.hidden=!(card.dataset.search.includes(input.value.toLowerCase())&&(!stage.value||card.dataset.stage===stage.value));if(!card.hidden)n++;}document.getElementById('count').textContent=n+' opportunities';document.getElementById('empty').hidden=n>0;}
input.addEventListener('input',filter);stage.addEventListener('change',filter);
</script></body></html>'''
