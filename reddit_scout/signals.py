"""Conservative, inspectable cues; these are indicators, never proof of demand."""
from __future__ import annotations

import re

from .models import clean_text

PAIN = re.compile(r"\b(?:frustrat\w*|annoy\w*|painful|pain point|nightmare|struggl\w*|hate|"
                  r"wast\w*|tedious|time.consuming|manual(?:ly)?|broken|unreliable|overwhelmed|"
                  r"can.t|cannot|doesn.t work|not working|no way|hard to|difficult|problem|"
                  r"issue|too expensive|overpriced|alternative|workaround|bottleneck|"
                  r"losing|lost|missed|chasing|overdue|copy.past\w*|duplicat\w*)\b", re.I)
REQUEST = re.compile(r"\b(?:looking for|need (?:a|an|help|software)|any (?:tool|software|recommendation)|"
                     r"is there (?:a|an)|how (?:do|can) (?:you|i|we)|wish (?:there|it|someone)|"
                     r"recommend\w*|automate|automat\w*|spreadsheet)\b", re.I)
WTP = re.compile(r"\b(?:i(?:.d| would)|we(?:.d| would)|willing to|happy to|prepared to) pay\b|"
                 r"\b(?:my|our) budget (?:is|of|for)|\bbudget(?:ed)? (?:up to|of|is)\b", re.I)
SPEND = re.compile(r"\b(?:(?:i|we)(?:.m|.re| am| are)? (?:already |currently )?(?:pay|paying|paid|spend|spending))\b|"
                   r"\b(?:costs? (?:me|us)|(?:our|my) (?:subscription|software bill))\b", re.I)
MONEY = re.compile(r"[$€£¥]\s*\d|\b\d[\d,.]*\s*(?:USD|EUR|GBP|dollars?|euros?|pounds?)\b|"
                   r"\bpay(?:ing|s)? (?:extra |monthly |annually )?for\b|\bsubscription (?:fee|cost|price)\b", re.I)
NON_PRODUCT_SPEND = re.compile(r"\b(?:salary|salaries|wages|rent|mortgage|loan payments?|taxes|utility bills)\b", re.I)
SOFTWARE_SPEND = re.compile(r"\b(?:software|subscription|tool|app|connector|platform|automation|QuickBooks|Xero|HubSpot|Salesforce|Zapier)\b", re.I)
TIME_COST = re.compile(r"\b(?:\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten)\s*"
                       r"(?:hours?|hrs?|minutes?|mins?)\b", re.I)
RECURRENCE = re.compile(r"\b(?:every (?:day|week|month|morning|night)|daily|weekly|monthly|"
                        r"each (?:day|week|month|client|order)|repeatedly|constantly)\b", re.I)
LOSS = re.compile(r"\b(?:lost revenue|losing (?:money|clients|customers)|missed (?:sales|leads|deadlines)|"
                  r"chargebacks?|downtime|penalties|costly (?:mistakes|errors)|double.book\w*|"
                  r"cash flow|overdue|unpaid)\b", re.I)
BUYER = re.compile(r"\b(?:my|our) (?:business|company|team|agency|clients|customers|shop|store|firm)|"
                   r"\bi (?:run|own|manage)|\bwe (?:run|own|manage)\b", re.I)
PROMO = re.compile(r"\b(?:i (?:built|launched|created)|we (?:built|launched)|try (?:my|our)|"
                   r"check out (?:my|our)|use (?:my|our) (?:tool|app)|promo code|"
                   r"lifetime deal|sign up (?:now|today)|dm me for|i can help you|book a demo)\b", re.I)
NEGATED_PAY = re.compile(r"\b(?:won.t|wouldn.t|can.t|cannot|don.t|not willing to|never|refuse to)\b.{0,30}\bpay\b|"
                         r"\bfree (?:only|tool|alternative)|\bno budget\b", re.I)


def sentences(text: str) -> list[str]:
    # Keep each quote a contiguous substring of the normalized source.
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", clean_text(text)) if s.strip()]


def cue(text: str, pattern: re.Pattern, max_chars: int = 300) -> str:
    for sentence in sentences(text):
        match = pattern.search(sentence)
        if match:
            start = max(0, match.start() - 80)
            return sentence[start:start + max_chars]
    return ""


def buying_evidence(text: str) -> tuple[str, str]:
    for sentence in sentences(text):
        if NEGATED_PAY.search(sentence):
            continue
        if NON_PRODUCT_SPEND.search(sentence) and not SOFTWARE_SPEND.search(sentence):
            continue
        if WTP.search(sentence):
            return "explicit_willingness", cue(sentence, WTP)
        if SPEND.search(sentence) and MONEY.search(sentence):
            return "existing_spend", cue(sentence, SPEND)
    return "none", ""


def priority(text: str) -> int:
    money_kind, _ = buying_evidence(text)
    return (min(6, len(PAIN.findall(text))) * 2 + bool(REQUEST.search(text)) * 3
            + (6 if money_kind != "none" else 0) + bool(TIME_COST.search(text)) * 4
            + bool(LOSS.search(text)) * 4 - bool(PROMO.search(text)) * 8)


def candidate(text: str, min_chars: int = 35) -> bool:
    return len(text) >= min_chars and bool(PAIN.search(text) or REQUEST.search(text) or WTP.search(text))
