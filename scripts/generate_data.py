"""Generate reproducible, realistic synthetic procurement history.

Run:  python scripts/generate_data.py

Writes data/{suppliers,quotes,discounts,deliveries,outcomes,demo_quotes}.json.
Every record is validated with the pydantic models in schema.py.
The history is deliberately engineered so memory-powered advice is provably
better than generic advice (e.g. QuickCart's delivery-penalty clause has
worked 3 times; VolumeMax always gives 7-10% for a volume commitment).
"""
from __future__ import annotations

import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from schema import (  # noqa: E402
    SUPPLIERS, Delivery, Discount, NegotiationOutcome, Quote, Tactic,
)

SEED = 42
DATA_DIR = ROOT / "data"
START = date(2025, 11, 5)
LAST_ALLOWED = date(2026, 10, 28)
N_QUOTES = 12

# item key -> (canonical SKU name, base unit price INR, typical quantities)
ITEMS: dict[str, tuple[str, float, list[int]]] = {
    "box": ("Corrugated Box 12x10x8 (5-ply, 150 GSM)", 18.50, [2000, 3000, 5000]),
    "tape": ("BOPP Packing Tape 48mm x 65m - Clear", 36.00, [500, 1000]),
    "wrap": ("Bubble Wrap Roll 1m x 50m (Air Bubble 10mm)", 395.00, [100, 200]),
    "mailer": ("Poly Mailer 10x14in (Tamper-proof)", 4.20, [5000, 10000]),
}
ITEM_CYCLE = ["box", "tape", "box", "wrap", "box", "mailer"] * 2  # box = 6 of 12 quotes

PROFILE = {
    "SUP-001": dict(mult=0.91, step_every=None, step_pct=0.0, spikes={7: 0.11}),
    "SUP-002": dict(mult=1.13, step_every=None, step_pct=0.0, spikes={9: 0.06}),
    "SUP-003": dict(mult=1.00, step_every=None, step_pct=0.0, spikes={5: 0.08}),
    "SUP-004": dict(mult=1.03, step_every=2.5, step_pct=0.045, spikes={}),
}

# supplier -> {delivery index: late days}, {delivery index: quality issue}
LATE = {
    "SUP-001": {1: 4, 3: 6, 6: 3, 8: 7},      # 4 of 10 late (40%)
    "SUP-002": {7: 1},
    "SUP-003": {2: 2, 5: 3},
    "SUP-004": {1: 3, 4: 2, 8: 4},
}
ISSUES = {
    "SUP-001": {3: "12% of bubble wrap rolls under-inflated", 8: "9% of cartons crushed in transit"},
    "SUP-002": {},
    "SUP-003": {4: "Minor print misalignment on about 3% of cartons"},
    "SUP-004": {6: "5% of poly mailers had weak adhesive strips"},
}

# (quote index, tactic, worked, % saving, notes, discount reason if worked)
OUTCOME_SPEC: dict[str, list[tuple]] = {
    "SUP-001": [
        (1, Tactic.DELIVERY_PENALTY, True, 3.0, "Agreed 1% credit per day of delay beyond day 2", "delivery penalty clause accepted"),
        (3, Tactic.COMPETING_QUOTE, True, 2.0, "Showed Prime Industrial quote; matched 2% lower after two calls", "matched competing quote"),
        (4, Tactic.VOLUME_COMMITMENT, False, 0.0, "Refused; said margins too thin for volume discounts", ""),
        (6, Tactic.DELIVERY_PENALTY, True, 3.5, "Penalty clause accepted again, priority dispatch promised", "delivery penalty clause accepted"),
        (8, Tactic.EARLY_PAYMENT, False, 0.0, "Declined early-payment discount; prefers advance payment", ""),
        (10, Tactic.DELIVERY_PENALTY, True, 4.0, "Penalty clause plus hint of shifting 30% volume to Prime", "delivery penalty clause plus volume-shift warning"),
    ],
    "SUP-002": [
        (1, Tactic.LONG_TERM_CONTRACT, True, 5.0, "Signed 12-month rate card at 5% below list", "12-month contract commitment"),
        (3, Tactic.COMPETING_QUOTE, False, 0.0, "Did not budge; said quality justifies the premium", ""),
        (5, Tactic.VOLUME_COMMITMENT, False, 0.0, "Volume alone not enough; wants an annual contract", ""),
        (7, Tactic.LONG_TERM_CONTRACT, True, 6.0, "Extended to 18 months for 6% off and price held for 6 months", "18-month contract extension"),
        (9, Tactic.EARLY_PAYMENT, False, 0.0, "Offered only 0.5%, not worth the cash tie-up", ""),
    ],
    "SUP-003": [
        (1, Tactic.VOLUME_COMMITMENT, True, 7.0, "Committed 5000 units per quarter; 7% off", "volume commitment of 5000 units"),
        (2, Tactic.EARLY_PAYMENT, False, 0.0, "Said volume discount already covers it", ""),
        (4, Tactic.VOLUME_COMMITMENT, True, 8.5, "Committed 6000 units; 8.5% off", "volume commitment of 6000 units"),
        (6, Tactic.COMPETING_QUOTE, True, 3.0, "Matched part of a rival quote", "matched competing quote"),
        (8, Tactic.VOLUME_COMMITMENT, True, 9.0, "Committed 8000 units; 9% off", "volume commitment of 8000 units"),
        (10, Tactic.VOLUME_COMMITMENT, True, 10.0, "Quarterly commitment of 10000 units; 10% off", "volume commitment of 10000 units"),
    ],
    "SUP-004": [
        (1, Tactic.EARLY_PAYMENT, True, 3.0, "Paid in full within 5 days; 3% off", "early payment within 5 days"),
        (3, Tactic.LONG_TERM_CONTRACT, False, 0.0, "Refused a price freeze; cites volatile paper prices", ""),
        (5, Tactic.EARLY_PAYMENT, True, 4.0, "Paid 100% advance; 4% off", "early payment (100% advance)"),
        (7, Tactic.COMPETING_QUOTE, False, 0.0, "Matched only 0.5%", ""),
        (9, Tactic.EARLY_PAYMENT, True, 5.0, "50% advance, balance in 7 days; 5% off", "early payment (50% advance)"),
    ],
}


def unit_price(sup_id: str, item_key: str, idx: int, rng: random.Random) -> float:
    """Price follows supplier personality: inflation, step-ups, spikes, noise."""
    p = PROFILE[sup_id]
    price = ITEMS[item_key][1] * p["mult"] * (1.004 ** idx)
    if p["step_every"]:
        price *= (1 + p["step_pct"]) ** int(idx / p["step_every"])
    if idx in p["spikes"]:
        price *= 1 + p["spikes"][idx]
    price *= 1 + rng.uniform(-0.012, 0.012)
    return round(price, 2)


def build_quotes(rng: random.Random) -> dict[str, list[Quote]]:
    out: dict[str, list[Quote]] = {}
    for s in SUPPLIERS:
        quotes, d = [], START
        for idx in range(N_QUOTES):
            key = ITEM_CYCLE[idx]
            name, _, qtys = ITEMS[key]
            qty = rng.choice(qtys) * (2 if s.supplier_id == "SUP-003" and key == "box" else 1)
            d = min(START + timedelta(days=30 * idx + rng.randint(-2, 2)), LAST_ALLOWED)
            quotes.append(Quote(
                quote_id=f"QT-{s.supplier_id[-3:]}-{idx + 1:02d}", supplier_id=s.supplier_id,
                item=name, quantity=qty, unit_price=unit_price(s.supplier_id, key, idx, rng),
                quote_date=d, valid_until=d + timedelta(days=15)))
        out[s.supplier_id] = quotes
    return out


def build_deliveries(quotes: dict[str, list[Quote]]) -> list[Delivery]:
    rows = []
    for sid, qs in quotes.items():
        for i in range(10):
            promised = qs[i].quote_date + timedelta(days=10)
            rows.append(Delivery(
                supplier_id=sid, order_id=f"PO-{qs[i].quote_date:%y%m}-{sid[-1]}{i + 1:02d}",
                promised_date=promised, actual_date=promised + timedelta(days=LATE[sid].get(i, 0)),
                quality_issue=ISSUES[sid].get(i)))
    return rows


def build_outcomes_and_discounts(quotes):
    outcomes, discounts = [], []
    for sid, spec in OUTCOME_SPEC.items():
        for idx, tactic, worked, pct, notes, reason in spec:
            q = quotes[sid][idx]
            nd = q.quote_date + timedelta(days=3)
            final = round(q.unit_price * (1 - pct / 100), 2) if worked else q.unit_price
            outcomes.append(NegotiationOutcome(
                supplier_id=sid, negotiation_date=nd, quote_id=q.quote_id, tactic_used=tactic,
                worked=worked, original_price=q.unit_price, final_price=final, notes=notes))
            if worked:
                discounts.append(Discount(supplier_id=sid, percent=pct, reason=reason, discount_date=nd))
    discounts.append(Discount(supplier_id="SUP-002", percent=1.5, discount_date=date(2026, 6, 10),
                              reason="annual loyalty rebate on contract anniversary"))
    return outcomes, discounts


def build_demo_quotes(quotes) -> list[dict]:
    """Five NEW incoming quotes (not in history), one per demo step."""
    box = ITEMS["box"][0]

    def last_box(sid: str) -> float:
        return [q for q in quotes[sid] if q.item == box][-1].unit_price

    spec = [
        (1, "SUP-001", 1.12, 3000, "2026-11-03", "QuickCart sends a price spike",
         "Hi sir, QuickCart this side. New rate for 5-ply 12x10x8 corrugated box Rs {p} per pc for {q} pcs. Valid till 18 Nov. Delivery in 7 days. Pls confirm. - Ramesh"),
        (2, "SUP-003", 1.03, 5000, "2026-11-04", "VolumeMax quote for a bigger lot",
         "Dear Sir, VolumeMax Traders quotation: 12x10x8 5-ply box @ Rs {p}/pc, qty {q}. Payment 30 days. Valid 15 days. Regards, Sales Desk"),
        (3, "SUP-004", 1.09, 3000, "2026-11-04", "Deccan raises prices again",
         "Hello, Deccan Wholesale here. Revised price for 12x10x8 5-ply box is Rs {p} per piece for {q} pcs due to kraft paper cost. Kindly confirm by Friday."),
        (4, "SUP-002", 1.025, 3000, "2026-11-05", "Prime Industrial routine quote",
         "Prime Industrial Co. Quotation: Corrugated box 12x10x8, 5 ply 150 GSM. Rate Rs {p}/pc. Qty {q} pcs. Validity 15 days. Delivery 7-10 days."),
        (5, "SUP-001", 1.06, 3000, "2026-11-06", "QuickCart follow-up quote (after logging the outcome)",
         "Sir QuickCart again. Okay we can do box 12x10x8 at Rs {p} for {q} pcs. This is our best. Dispatch in 7 days."),
    ]
    out = []
    for step, sid, mult, qty, dt, title, text in spec:
        price = round(last_box(sid) * mult, 2)
        q = Quote(quote_id=f"QT-DEMO-{step}", supplier_id=sid, item=box, quantity=qty, unit_price=price,
                  quote_date=date.fromisoformat(dt), valid_until=date.fromisoformat(dt) + timedelta(days=15))
        out.append({"step": step, "title": title, "supplier_id": sid,
                    "raw_text": text.format(p=f"{price:.2f}", q=qty), "quote": q.model_dump(mode="json")})
    return out


def write(name: str, rows) -> None:
    payload = [r.model_dump(mode="json") if hasattr(r, "model_dump") else r for r in rows]
    (DATA_DIR / name).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    rng = random.Random(SEED)
    DATA_DIR.mkdir(exist_ok=True)
    quotes = build_quotes(rng)
    deliveries = build_deliveries(quotes)
    outcomes, discounts = build_outcomes_and_discounts(quotes)
    write("suppliers.json", SUPPLIERS)
    write("quotes.json", [q for qs in quotes.values() for q in qs])
    write("discounts.json", discounts)
    write("deliveries.json", deliveries)
    write("outcomes.json", outcomes)
    write("demo_quotes.json", build_demo_quotes(quotes))
    for s in SUPPLIERS:
        i = s.supplier_id
        print(f"{s.name:20s} quotes={len(quotes[i])} discounts={sum(d.supplier_id == i for d in discounts)} "
              f"deliveries={sum(d.supplier_id == i for d in deliveries)} "
              f"outcomes={sum(o.supplier_id == i for o in outcomes)}")


if __name__ == "__main__":
    main()
