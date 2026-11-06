"""All LLM prompts for the Vendor Memory & Negotiation Agent."""
from __future__ import annotations

import json

BUYER = "Arjun Rao, Procurement Manager, ShipEasy Commerce Pvt Ltd"
TACTICS = ["volume_commitment", "early_payment", "competing_quote", "long_term_contract",
           "delivery_penalty_clause", "none"]

CANONICAL_SKUS = [
    "Corrugated Box 12x10x8 (5-ply, 150 GSM)",
    "BOPP Packing Tape 48mm x 65m - Clear",
    "Bubble Wrap Roll 1m x 50m (Air Bubble 10mm)",
    "Poly Mailer 10x14in (Tamper-proof)",
]

# ------------------------------------------------------------- (a) parser
PARSER_SYSTEM = "You extract structured data from messy supplier messages. Output JSON only."


def parser_prompt(raw: str, suppliers: list[dict], today: str) -> str:
    return f"""Extract the quotation from this supplier message.

Known suppliers: {json.dumps(suppliers)}
Canonical item names (use the closest match exactly if it fits): {json.dumps(CANONICAL_SKUS)}
Today's date: {today}

Message:
\"\"\"{raw}\"\"\"

Return ONLY a JSON object with keys:
"supplier_id" (one of the known ids, or null if unclear), "item" (string), "quantity" (integer),
"unit_price" (number, INR per unit), "quote_date" (YYYY-MM-DD, default today), "valid_until" (YYYY-MM-DD or null)."""


# ---------------------------------------------------------- (b) advisor
ADVISOR_SYSTEM = (
    "You are a senior procurement negotiation advisor for an Indian e-commerce company. "
    "Be concrete and concise. Output JSON only, no markdown fences.")

OUTPUT_SPEC = f"""Return ONLY a JSON object:
{{
  "negotiation_points": ["3 to 5 short, specific points"],
  "suggested_tactic": one of {TACTICS},
  "draft_email": "a polite, firm counter-offer email from {BUYER}, ready to send"
}}"""

STRICT_REMINDER = ("Your previous reply was not valid JSON. Reply with ONE raw JSON object only: "
                   "no prose, no code fences, no comments, double quotes, and exactly the keys requested.")


def advisor_prompt_with_memory(quote: dict, supplier: dict, memories: list[dict],
                               reflection: str, analysis: dict) -> str:
    mem = "\n".join(f"{i}. [{m.get('date') or 'n/a'}] {m['text']}" for i, m in enumerate(memories, 1)) \
        or "(no memories recalled)"
    return f"""A new quote arrived from {supplier['name']} ({supplier['personality']}).

NEW QUOTE:
{json.dumps(quote)}

RECALLED MEMORIES FROM HINDSIGHT (verbatim, dated):
{mem}

HINDSIGHT REFLECTION (what approach has worked best):
{reflection or '(none)'}

COMPUTED ANALYSIS (exact numbers, trust these):
{json.dumps(analysis, indent=2)}

RULES:
- Ground EVERY negotiation point in the memories or analysis above. Cite the fact (dates, %, counts).
- NEVER invent facts, prices, or history that is not supplied. If evidence is thin, say so.
- Prefer tactics that worked for THIS supplier; avoid tactics that failed.
- Quantify asks in INR and % where possible.
- The email must reference real history (e.g. past late deliveries or earlier prices) and make one clear ask.

{OUTPUT_SPEC}"""


def advisor_prompt_no_memory(quote: dict, supplier_name: str) -> str:
    return f"""A new quote arrived from a supplier named {supplier_name}.

NEW QUOTE:
{json.dumps(quote)}

You have NO history about this supplier (no past prices, deliveries, discounts or negotiations).
Give general procurement negotiation advice only.

{OUTPUT_SPEC}"""


def advisor_prompt_plain(base_prompt: str) -> str:
    """Simpler non-JSON fallback prompt."""
    head = base_prompt.split("Return ONLY a JSON object")[0]
    return (head + "\nReply in plain text with: a line 'TACTIC: <one of "
            f"{', '.join(TACTICS)}>', then 3 to 5 lines each starting with '- ' (negotiation points).")


# ------------------------------------------------------------ (c) email
EMAIL_SYSTEM = "You write concise, professional B2B negotiation emails for Indian procurement teams."


def email_prompt(quote: dict, supplier_name: str, tactic: str, points: list[str]) -> str:
    return f"""Write a counter-offer email to {supplier_name} signed by {BUYER}.
Quote: {json.dumps(quote)}
Tactic to use: {tactic}
Points to include:
{chr(10).join('- ' + p for p in points)}
Keep it under 160 words. Include a subject line. Output the email text only."""
