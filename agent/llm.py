"""Groq LLM layer: model fallback, JSON validation, retries, rule-based safety net.

Fallback ladder for advice (never raises to the UI):
  1. JSON prompt, up to 3 tries (stricter reminder each retry), primary model -> fallback model
  2. plain-text prompt parsed leniently
  3. deterministic rule-based recommendation built from the computed analysis
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent import prompts  # noqa: E402
from agent.analysis import PriceAnalysis, ReliabilityReport  # noqa: E402
from schema import SUPPLIERS, AgentRecommendation, Quote, Supplier, Tactic  # noqa: E402

_client = None


class LLMError(RuntimeError):
    """The LLM could not be reached or returned nothing usable."""


def models() -> list[str]:
    return [os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
            os.getenv("GROQ_FALLBACK_MODEL", "qwen/qwen3-32b")]


def _get_client():
    global _client
    if _client is None:
        key = os.getenv("GROQ_API_KEY")
        if not key:
            raise LLMError("GROQ_API_KEY is not set. Add it to your .env file.")
        try:
            from groq import Groq
        except ImportError as e:
            raise LLMError("groq package missing. Run: pip install -r requirements.txt") from e
        _client = Groq(api_key=key)
    return _client


def chat(messages: list[dict], json_mode: bool = False, temperature: float = 0.3, max_tokens: int = 1800) -> str:
    """One completion with model fallback. Raises LLMError if every model fails."""
    client, last = _get_client(), None
    for model in models():
        for use_json in ([True, False] if json_mode else [False]):
            kwargs = dict(model=model, messages=messages, temperature=temperature, max_tokens=max_tokens)
            if use_json:
                kwargs["response_format"] = {"type": "json_object"}
            try:
                out = client.chat.completions.create(**kwargs).choices[0].message.content or ""
                out = re.sub(r"<think>.*?</think>", "", out, flags=re.S).strip()
                if out:
                    return out
            except Exception as e:  # noqa: BLE001 - rate limits, tool/format errors, outages
                last = e
                time.sleep(0.8)
    raise LLMError(f"All Groq models failed: {str(last)[:200]}")


def extract_json(text: str) -> dict:
    """Strip code fences / chatter and parse the first JSON object."""
    text = re.sub(r"```(?:json)?", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found")
    return json.loads(text[start:end + 1])


# ------------------------------------------------------------ quote parsing
def _regex_parse(raw: str, supplier_hint: Optional[str]) -> Quote:
    """Best-effort parser used when the LLM is unavailable."""
    from agent import ledger
    sup = ledger.get_supplier(supplier_hint) if supplier_hint else ledger.find_supplier_by_text(raw)
    price = re.search(r"(?:rs\.?|₹|inr)\s*([\d,]+(?:\.\d+)?)", raw, re.I)
    qty = re.search(r"([\d,]{3,})\s*(?:pcs|pc|pieces|units|nos|rolls|boxes)", raw, re.I) or \
        re.search(r"(?:qty|quantity)[:\s]*([\d,]+)", raw, re.I)
    if not (sup and price and qty):
        raise ValueError("Could not read supplier, price and quantity from the message. "
                         "Use the quick form or include 'Rs <price>' and '<qty> pcs'.")
    item = next((s for s in prompts.CANONICAL_SKUS if any(w in raw.lower() for w in
                 {"Corrugated": ["box", "carton"], "BOPP": ["tape"], "Bubble": ["bubble"], "Poly": ["mailer"]}[s.split()[0]])),
                prompts.CANONICAL_SKUS[0])
    today = date.today()
    return Quote(quote_id=f"QT-NEW-{int(time.time())}", supplier_id=sup.supplier_id, item=item,
                 quantity=int(qty.group(1).replace(",", "")), unit_price=float(price.group(1).replace(",", "")),
                 quote_date=today, valid_until=today + timedelta(days=15))


def parse_quote_text(raw: str, supplier_hint: Optional[str] = None) -> Quote:
    """Turn a messy email/WhatsApp message into a Quote (LLM first, regex fallback)."""
    today = date.today().isoformat()
    sups = [{"supplier_id": s.supplier_id, "name": s.name} for s in SUPPLIERS]
    try:
        data = extract_json(chat([{"role": "system", "content": prompts.PARSER_SYSTEM},
                                  {"role": "user", "content": prompts.parser_prompt(raw, sups, today)}],
                                 json_mode=True, temperature=0))
        sid = supplier_hint or data.get("supplier_id")
        if sid not in {s.supplier_id for s in SUPPLIERS}:
            from agent import ledger
            found = ledger.find_supplier_by_text(raw)
            sid = found.supplier_id if found else None
        if not sid:
            raise ValueError("Could not identify the supplier. Pick one in the sidebar.")
        qd = date.fromisoformat(data.get("quote_date") or today)
        vu = data.get("valid_until")
        return Quote(quote_id=f"QT-NEW-{int(time.time())}", supplier_id=sid, item=str(data["item"]),
                     quantity=int(data["quantity"]), unit_price=float(data["unit_price"]), quote_date=qd,
                     valid_until=date.fromisoformat(vu) if vu else qd + timedelta(days=15))
    except ValueError as e:
        if "supplier" in str(e).lower():
            raise
    except Exception:  # noqa: BLE001 - LLM down / malformed JSON -> regex fallback
        pass
    return _regex_parse(raw, supplier_hint)


# ------------------------------------------------------------- recommendation
def _numbers(price: Optional[PriceAnalysis], rel: Optional[ReliabilityReport]) -> dict:
    """Authoritative numeric fields: always computed in Python, never by the LLM."""
    if price is None or rel is None:
        return {"price_change_percent": 0.0, "price_flag": "unknown", "reliability_score": 0.0}
    return {"price_change_percent": price.pct_vs_last or 0.0, "price_flag": price.flag,
            "reliability_score": rel.score}


def template_email(quote: Quote, supplier: Supplier, tactic: str, points: list[str]) -> str:
    asks = "\n".join(f"- {p}" for p in points[:3])
    return (f"Subject: Re: Quotation for {quote.item} - request to revisit pricing\n\n"
            f"Dear {supplier.name} team,\n\nThank you for your quotation of Rs {quote.unit_price:.2f} per unit "
            f"for {quote.quantity} units. Before we confirm, we would like to discuss the following:\n{asks}\n\n"
            f"We value the relationship and would like to proceed on {tactic.replace('_', ' ')} terms. "
            f"Please share a revised offer.\n\nRegards,\n{prompts.BUYER}")


def write_email(quote: Quote, supplier: Supplier, tactic: str, points: list[str]) -> str:
    try:
        return chat([{"role": "system", "content": prompts.EMAIL_SYSTEM},
                     {"role": "user", "content": prompts.email_prompt(quote.model_dump(mode="json"), supplier.name, tactic, points)}])
    except LLMError:
        return template_email(quote, supplier, tactic, points)


def rule_based_recommendation(quote: Quote, supplier: Supplier, price: Optional[PriceAnalysis],
                              rel: Optional[ReliabilityReport], patterns: Optional[dict]) -> AgentRecommendation:
    """Deterministic fallback, grounded only in the computed analysis."""
    points: list[str] = []
    tactic = Tactic.NONE
    if price is not None and rel is not None and patterns is not None:
        if price.flag == "spike":
            points.append(f"Price jumped {price.pct_vs_last:+.1f}% vs last quote (Rs {price.last_price:.2f}); "
                          f"ask them to justify it or hold the old rate.")
        elif price.n_history:
            points.append(price.summary)
        if rel.late_count:
            points.append(f"Reliability: {rel.summary}. Ask for a delivery-penalty clause.")
        best = patterns.get("best_tactic")
        if best:
            st = patterns["by_tactic"][best]
            tactic = Tactic(best)
            points.append(f"'{best}' has worked {st['successes']}/{st['attempts']} times with this supplier "
                          f"(avg saving {st['avg_saving_pct']}%). Lead with it.")
        for t in patterns.get("failed_tactics", [])[:1]:
            points.append(f"Avoid '{t}': it has not worked with this supplier before.")
    if len(points) < 3:
        points += ["Request a volume-tiered price break.", "Ask for better payment terms or early-payment discount.",
                   "Get a competing quote to use as leverage."][: 3 - len(points)]
        tactic = tactic if tactic != Tactic.NONE else Tactic.COMPETING_QUOTE
    return AgentRecommendation(supplier_id=supplier.supplier_id, negotiation_points=points[:5],
                               suggested_tactic=tactic, draft_email=template_email(quote, supplier, tactic.value, points),
                               **_numbers(price, rel))


def _from_dict(data: dict, quote: Quote, supplier: Supplier, price, rel) -> AgentRecommendation:
    points = [str(p).strip() for p in (data.get("negotiation_points") or []) if str(p).strip()][:5]
    if not points:
        raise ValueError("no negotiation points")
    tactic = Tactic(str(data.get("suggested_tactic", "none")).strip())  # ValueError if invalid
    email = str(data.get("draft_email") or "").strip()
    if len(email) < 40:
        email = write_email(quote, supplier, tactic.value, points)
    return AgentRecommendation(supplier_id=supplier.supplier_id, negotiation_points=points,
                               suggested_tactic=tactic, draft_email=email, **_numbers(price, rel))


def _parse_plain(text: str, quote, supplier, price, rel) -> AgentRecommendation:
    m = re.search(r"TACTIC:\s*([a-z_]+)", text, re.I)
    tactic = next((t for t in Tactic if m and t.value == m.group(1).lower()), Tactic.NONE)
    points = [l.lstrip("-* ").strip() for l in text.splitlines() if l.strip().startswith(("-", "*"))][:5]
    if not points:
        raise ValueError("no bullet points")
    return AgentRecommendation(supplier_id=supplier.supplier_id, negotiation_points=points, suggested_tactic=tactic,
                               draft_email=write_email(quote, supplier, tactic.value, points), **_numbers(price, rel))


def advise(quote: Quote, supplier: Supplier, memories: list[dict], reflection: str,
           price: Optional[PriceAnalysis], rel: Optional[ReliabilityReport], patterns: Optional[dict],
           memory_enabled: bool = True) -> tuple[AgentRecommendation, str]:
    """Return (recommendation, mode) where mode is llm-json | llm-text | rule-based."""
    q = quote.model_dump(mode="json")
    if memory_enabled:
        analysis = {"price": price.to_dict() if price else None, "reliability": rel.to_dict() if rel else None,
                    "negotiation_patterns": patterns}
        prompt = prompts.advisor_prompt_with_memory(q, {"name": supplier.name, "personality": supplier.personality},
                                                    memories, reflection, analysis)
    else:
        prompt = prompts.advisor_prompt_no_memory(q, supplier.name)
    msgs = [{"role": "system", "content": prompts.ADVISOR_SYSTEM}, {"role": "user", "content": prompt}]

    try:
        for attempt in range(3):
            try:
                return _from_dict(extract_json(chat(msgs, json_mode=True)), quote, supplier, price, rel), "llm-json"
            except LLMError:
                raise
            except Exception:  # noqa: BLE001 - bad JSON / schema -> stricter retry
                msgs = msgs[:2] + [{"role": "user", "content": prompts.STRICT_REMINDER}]
        text = chat([{"role": "system", "content": prompts.ADVISOR_SYSTEM},
                     {"role": "user", "content": prompts.advisor_prompt_plain(prompt)}])
        return _parse_plain(text, quote, supplier, price, rel), "llm-text"
    except Exception:  # noqa: BLE001 - LLM down or unusable output
        if memory_enabled:
            return rule_based_recommendation(quote, supplier, price, rel, patterns), "rule-based"
        return rule_based_recommendation(quote, supplier, None, None, None), "rule-based"
