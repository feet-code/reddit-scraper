from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import time

from .db import Store
from .models import Item, clean_text
from .signals import (BUYER, LOSS, NEGATED_PAY, PAIN, PROMO, RECURRENCE, REQUEST,
                      TIME_COST, buying_evidence, candidate, cue, priority)

LOG = logging.getLogger(__name__)
LOCAL_VERSION = "local-v2"

# Specific jobs rather than one giant 'business complaints' bucket.
THEMES = [
    ("invoice-followup", r"(?:overdue|unpaid|chasing|late|follow.up).{0,60}invoice|invoice.{0,60}(?:overdue|unpaid|chasing|late|follow.up)",
     "Chasing overdue invoices", "Track unpaid invoices and prepare timely, reviewable payment reminders"),
    ("transaction-reconciliation", r"reconcil\w*|match\w*.{0,30}(?:transaction|bank)|(?:receipt|bookkeep).{0,30}(?:manual|spreadsheet)",
     "Manual transaction reconciliation", "Match transactions, highlight discrepancies, and export a review queue"),
    ("inventory-synchronization", r"(?:inventory|stock).{0,50}(?:sync|mismatch|manual|oversell|spreadsheet)|oversell\w*",
     "Inventory getting out of sync", "Compare stock across sales channels and flag discrepancies before overselling"),
    ("manual-reporting", r"report\w*.{0,60}(?:manual|spreadsheet|hours|copy)|(?:manual|hours|copy).{0,60}report\w*",
     "Repeated manual reporting", "Collect existing metrics and produce a reviewable recurring report"),
    ("lead-followup", r"(?:lead|prospect).{0,50}(?:follow.up|lost|losing|miss|track)|follow.up.{0,50}(?:lead|prospect)",
     "Leads falling through follow-up gaps", "Identify unattended leads and queue the next action for a salesperson"),
    ("appointment-coordination", r"double.book\w*|no.show\w*|(?:appointment|schedul\w*|booking).{0,50}(?:manual|conflict|miss|nightmare)",
     "Appointment coordination failures", "Detect scheduling conflicts and coordinate confirmations"),
    ("client-document-collection", r"(?:chasing|collect\w*|missing).{0,50}(?:document|paperwork|client files)|(?:document|paperwork).{0,50}(?:chasing|missing|manual)",
     "Chasing client documents", "Track required files, surface missing items, and give clients one submission checklist"),
    ("support-triage", r"(?:support|ticket|inbox).{0,50}(?:overwhelm|manual|duplicate|repetiti|hours)|repetiti\w*.{0,30}(?:question|ticket)",
     "Repetitive support triage", "Group repeat requests and draft answers with human review"),
    ("deployment-reliability", r"(?:deploy|backup|monitor|alert).{0,50}(?:fail|broken|unreliable|noise|manual|miss)|alert fatigue",
     "Unreliable operations monitoring", "Verify operational checks and prioritize actionable failures"),
    ("spreadsheet-data-transfer", r"copy.past\w*|data entry|re.key\w*|(?:spreadsheet|csv).{0,40}(?:manual|duplicat|error|sync)",
     "Rekeying data between tools", "Map a specific pair of systems and validate data before transferring it"),
]
THEMES = [(key, re.compile(pattern, re.I), title, idea) for key, pattern, title, idea in THEMES]
STOP_WORDS = set("a an the and or but if then to for of in on at by with without as from is are was were be been being "
                 "i me my we us our you your they them their it its this that these those have has had do does did "
                 "can cant could would should will just really very get got any anyone someone something one "
                 "need looking help problem issue tool tools software app using use work working want time "
                 "thanks thank like know still also much more some about how what when which who not no all ".split())


def tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{3,}", text.lower()) if w not in STOP_WORDS}


def audience(subreddit: str) -> str:
    if subreddit in {"sysadmin", "msp", "devops", "webdev"}:
        return "IT and software teams"
    if subreddit in {"ecommerce", "shopify", "etsysellers", "fulfillmentbyamazon"}:
        return "Online store operators"
    if subreddit in {"ppc", "seo", "marketing", "agency", "sales"}:
        return "Marketing and sales teams"
    if subreddit in {"propertymanagement", "realtors"}:
        return "Property businesses"
    if subreddit in {"smallbusiness", "entrepreneur", "freelance", "bookkeeping", "accounting"}:
        return "Small business operators"
    return f"People in r/{subreddit}"


def local_analysis(item: Item, min_chars: int = 35) -> dict:
    text = item.text
    if not candidate(text, min_chars):
        return {"findings": [], "method": "local"}
    matching = next(((key, title, idea) for key, pattern, title, idea in THEMES if pattern.search(text)), None)
    if matching:
        key, problem, idea = matching
    else:
        words = sorted(tokens(text), key=lambda w: (-text.lower().count(w), w))[:5]
        key = "emerging-" + "-".join(words[:4])
        problem = "Investigate: " + (", ".join(words) or "unclassified problem")
        idea = "Identify the repeated task in these examples, then prototype one measurable improvement"
    money_kind, money_quote = buying_evidence(text)
    severity = min(5, 1 + int(bool(PAIN.search(text))) + int(bool(TIME_COST.search(text)))
                   + int(bool(LOSS.search(text))) + int(bool(RECURRENCE.search(text))))
    quote = cue(text, PAIN) or cue(text, REQUEST) or cue(text, re.compile(r"."))
    alternatives = [name for name in ("Excel", "Google Sheets", "QuickBooks", "Xero", "Zapier", "HubSpot",
                                     "Salesforce", "Notion", "Shopify", "Jira", "Zendesk")
                    if re.search(r"\b" + re.escape(name) + r"\b", text, re.I)]
    finding = {"problem_key": key, "problem": problem, "audience": audience(item.subreddit),
               "tool_idea": idea, "evidence_quote": quote, "severity": severity,
               "software_fit": 4 if matching else 2, "money_kind": money_kind, "money_quote": money_quote,
               "time_quote": cue(text, TIME_COST), "recurrence_quote": cue(text, RECURRENCE),
               "buyer_quote": cue(text, BUYER), "workaround_quote": cue(text, re.compile(r"manual|spreadsheet|workaround|copy.past", re.I)),
               "alternatives": alternatives, "promotion": bool(PROMO.search(text)),
               "negative_pay_quote": cue(text, NEGATED_PAY), "method": "local"}
    return {"findings": [finding], "method": "local"}


def local_key(config: dict) -> str:
    return f"{LOCAL_VERSION}-{config['collection']['min_text_characters']}"


def analyze(store: Store, config: dict) -> dict:
    a = config["analysis"]
    items = store.items()
    completed = 0
    local = local_key(config)
    for item in items:
        if store.cached(item, local) is None:
            with store.conn:
                store.save_analysis(item, local, local_analysis(item, config["collection"]["min_text_characters"]))
            completed += 1
    result = {"local_processed": completed, "gemini_processed": 0, "gemini_pending": 0,
              "mode": a["mode"], "error": None, "finished_at": time.time()}
    use_gemini = a["mode"] == "gemini" or (a["mode"] == "auto" and bool(os.getenv("GEMINI_API_KEY")))
    if use_gemini:
        from .gemini import GeminiAnalyzer, GeminiUnavailable, analyzer_key
        key = analyzer_key(a)
        pending = [item for item in items if candidate(item.text, config["collection"]["min_text_characters"])
                   and store.cached(item, key) is None]
        result["gemini_pending"] = len(pending)
        work = pending[:a["max_items_per_run"]]
        known_keys = sorted({f["problem_key"] for item in items for f in (store.cached(item, key) or {}).get("findings", [])})[:100]
        try:
            engine = GeminiAnalyzer(a, store)
            for offset in range(0, len(work), a["batch_size"]):
                batch = work[offset:offset + a["batch_size"]]
                LOG.info("Analyzing %d-%d/%d with Gemini", offset + 1, offset + len(batch), len(work))
                responses = engine.analyze(batch, known_keys)
                with store.conn:
                    for item in batch:
                        store.save_analysis(item, key, responses[item.id])
                        known_keys.extend(f["problem_key"] for f in responses[item.id]["findings"] if f["problem_key"] not in known_keys)
                result["gemini_processed"] += len(batch)
                result["gemini_pending"] -= len(batch)
        except GeminiUnavailable as exc:
            result["error"] = str(exc)
            LOG.warning("Gemini paused: %s. Local findings remain available; unfinished Gemini work resumes next run.", exc)
    result["finished_at"] = time.time()
    with store.conn:
        store.set_meta("last_analysis", result)
    return result


def build_opportunities(store: Store, config: dict) -> list[dict]:
    from .gemini import analyzer_key
    a = config["analysis"]
    groups = []
    key = analyzer_key(a)
    for item in store.items():
        payload = None if a["mode"] == "local" else store.cached(item, key)
        payload = payload or store.cached(item, local_key(config))
        if not payload:
            continue
        for finding in payload.get("findings", []):
            if finding["promotion"]:
                continue
            finding = finding | {"item": item}
            match = next((g for g in groups if same_problem(g[0], finding)), None)
            if match is None:
                groups.append([finding])
            else:
                match.append(finding)
    opportunities = [summarize(group, a["min_independent_threads"]) for group in groups]
    opportunities.sort(key=lambda o: (-o["score"], -o["independent_threads"], o["id"]))
    return opportunities[:a["report_limit"]]


def same_problem(left: dict, right: dict) -> bool:
    if left["item"].source != right["item"].source:
        return False
    if left["audience"].casefold() != right["audience"].casefold():
        return False
    if left["problem_key"] == right["problem_key"]:
        return True
    # Restrained lexical merge for independently named emerging/AI categories.
    if left["method"] == right["method"] == "local" and not (
            left["problem_key"].startswith("emerging-") and right["problem_key"].startswith("emerging-")):
        return False
    l, r = tokens(left["problem"]), tokens(right["problem"])
    return len(l & r) >= 3 and len(l & r) / max(1, len(l | r)) >= 0.7


def summarize(group: list[dict], min_threads: int) -> dict:
    # Reposted identical text does not become extra independent evidence.
    unique, fingerprints = [], set()
    for f in sorted(group, key=lambda x: (-priority(x["item"].text), x["item"].id)):
        item = f["item"]
        fingerprint = clean_text(item.body or item.text).casefold()
        if fingerprint in fingerprints:
            continue
        fingerprints.add(fingerprint)
        unique.append(f)
    group = unique
    representative = max(group, key=lambda f: (f["method"] == "gemini", f["severity"], priority(f["item"].text)))
    threads = {f["item"].thread_id for f in group}
    communities = sorted({f["item"].subreddit for f in group})
    money = [f for f in group if f["money_kind"] != "none" and f["money_quote"]]
    quantified = {f["item"].thread_id for f in group if f["time_quote"]}
    wtp = {f["item"].thread_id for f in money if f["money_kind"] == "explicit_willingness"}
    spend = {f["item"].thread_id for f in money if f["money_kind"] == "existing_spend"}
    buyer = {f["item"].thread_id for f in group if f["buyer_quote"]}
    recurring = {f["item"].thread_id for f in group if f["recurrence_quote"]}
    negative = {f["item"].thread_id for f in group if f["negative_pay_quote"]}
    # Average per thread, so one active discussion cannot overwhelm the ranking.
    severity = sum(max(f["severity"] for f in group if f["item"].thread_id == t) for t in threads) / len(threads)
    fit = sum(max(f["software_fit"] for f in group if f["item"].thread_id == t) for t in threads) / len(threads)
    components = {
        "repeated_threads": min(25, 8 * math.log2(len(threads) + 1)),
        "payment_signals": min(25, 10 * len(wtp) + 6 * len(spend)),
        "pain_severity": severity * 3,
        "quantified_time": min(10, 5 * len(quantified)),
        "recurring_task": min(10, 5 * len(recurring)),
        "buyer_context": min(5, 2.5 * len(buyer)),
        "software_fit": fit * 2,
        "negative_payment": -min(20, 5 * len(negative)),
    }
    score = min(100, max(0, sum(components.values())))
    stage = "recurring_signal" if len(threads) >= min_threads else "early_signal"
    if stage == "early_signal":
        score = min(45, score)
    # These are deliberately labeled experiments, not prices inferred from posts.
    premium = len(threads) >= min_threads and bool(money) and bool(buyer) and fit >= 3
    experiment = "$99–$299/month" if premium else "$29–$99/month"
    risk_notes = ["Thread counts are not counts of unique people or prospective customers.",
                  "This sample does not establish market size, SEO volume, or low competition."]
    if not money:
        risk_notes.append("No explicit willingness-to-pay or existing-spend evidence found.")
    if stage == "early_signal":
        risk_notes.append("Only one or too few independent threads; collect corroborating examples.")
    if any(f["method"] == "local" for f in group):
        risk_notes.append("Includes local keyword classification; review the source context before building.")
    if negative:
        risk_notes.append("Some sources express a price objection or no budget.")
    evidence = []
    for f in group:
        item = f["item"]
        evidence.append({"id": item.id, "thread_id": item.thread_id, "subreddit": item.subreddit,
                         "kind": item.kind, "url": item.permalink, "quote": f["evidence_quote"],
                         "created_utc": item.created_utc, "score": item.score, "source": item.source,
                         "money_kind": f["money_kind"], "money_quote": f["money_quote"],
                         "time_quote": f["time_quote"], "workaround_quote": f["workaround_quote"],
                         "negative_pay_quote": f["negative_pay_quote"], "method": f["method"]})
    identifier = hashlib.sha256((representative["problem_key"] + "|" + representative["audience"]).encode()).hexdigest()[:12]
    return {"id": identifier, "problem": representative["problem"], "problem_key": representative["problem_key"],
            "audience": representative["audience"], "audience_basis": "Inferred from community/context; validate the buyer",
            "tool_idea": representative["tool_idea"], "score": round(score, 1), "stage": stage,
            "score_components": {k: round(v, 2) for k, v in components.items()},
            "independent_threads": len(threads), "mentions": len(group), "subreddits": communities,
            "payment_evidence": {"explicit_willingness_threads": len(wtp), "existing_spend_threads": len(spend),
                                 "price_objection_threads": len(negative)},
            "mentioned_alternatives": sorted({name for f in group for name in f["alternatives"]}),
            "pricing_hypothesis": {"test_range": experiment, "currency": "USD", "validated": False,
                                   "basis": "Illustrative subscription test, NOT a quoted budget or market price. "
                                            "Only test this if interviews show at least 5x the price in monthly value; "
                                            "otherwise revise the price or reject the opportunity."},
            "mvp_scope": [representative["tool_idea"], "Start with one buyer type and one integration or file format",
                          "Measure hours saved, errors prevented, or revenue recovered"],
            "validation_steps": ["Read the full linked discussions, including replies that already solve the problem",
                                 "Interview 5 target buyers about their last occurrence, current workaround, and actual spend",
                                 "Offer a narrow paid pilot and obtain a payment commitment before building broadly"],
            "risks": risk_notes, "evidence": evidence, "synthetic": all(f["item"].source == "demo" for f in group)}
