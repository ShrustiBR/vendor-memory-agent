"""
schema.py - shared data structure for the Vendor Memory & Negotiation Agent.

Everyone imports from this one file:
  - Data Lead      -> generates data that matches these models
  - Memory Lead    -> uses the to_memory_text() helpers to retain records in Hindsight
  - Agent/Analytics-> read these models for comparison and prompts
  - UI Lead        -> displays these models

Install: pip install pydantic
"""

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------
# 1. SUPPLIERS (agree on these first so all data stays consistent)
# ---------------------------------------------------------------
class Supplier(BaseModel):
    supplier_id: str                 # e.g. "SUP-001"
    name: str                        # e.g. "Sri Balaji Traders"
    category: str                    # e.g. "Packaging", "Electronics"
    personality: str                 # e.g. "cheap but unreliable"


# Example personalities (rename the suppliers however you like)
SUPPLIERS = [
    Supplier(supplier_id="SUP-001", name="QuickCart Supplies", category="Packaging",
             personality="cheap but often late"),
    Supplier(supplier_id="SUP-002", name="Prime Industrial Co", category="Packaging",
             personality="expensive but very dependable"),
    Supplier(supplier_id="SUP-003", name="VolumeMax Traders", category="Packaging",
             personality="gives big discounts for volume commitments"),
    Supplier(supplier_id="SUP-004", name="Deccan Wholesale", category="Packaging",
             personality="raises prices often, flexible on early payment"),
]


# ---------------------------------------------------------------
# 2. QUOTE
# ---------------------------------------------------------------
class Quote(BaseModel):
    quote_id: str
    supplier_id: str
    item: str                        # e.g. "Corrugated box 12x10x8"
    quantity: int
    unit_price: float                # in INR
    quote_date: date
    valid_until: Optional[date] = None


# ---------------------------------------------------------------
# 3. DISCOUNT
# ---------------------------------------------------------------
class Discount(BaseModel):
    supplier_id: str
    percent: float                   # e.g. 8.0 means 8%
    reason: str                      # e.g. "volume commitment of 5000 units"
    discount_date: date


# ---------------------------------------------------------------
# 4. DELIVERY
# ---------------------------------------------------------------
class Delivery(BaseModel):
    supplier_id: str
    order_id: str
    promised_date: date
    actual_date: date
    quality_issue: Optional[str] = None   # e.g. "10% boxes damaged"

    @property
    def days_late(self) -> int:
        return max((self.actual_date - self.promised_date).days, 0)


# ---------------------------------------------------------------
# 5. NEGOTIATION OUTCOME (this is what makes the agent learn)
# ---------------------------------------------------------------
class Tactic(str, Enum):
    VOLUME_COMMITMENT = "volume_commitment"
    EARLY_PAYMENT = "early_payment"
    COMPETING_QUOTE = "competing_quote"
    LONG_TERM_CONTRACT = "long_term_contract"
    DELIVERY_PENALTY = "delivery_penalty_clause"
    NONE = "none"


class NegotiationOutcome(BaseModel):
    supplier_id: str
    negotiation_date: date
    quote_id: str
    tactic_used: Tactic
    worked: bool
    original_price: float            # INR per unit
    final_price: float               # INR per unit
    notes: Optional[str] = None


# ---------------------------------------------------------------
# 6. AGENT OUTPUT (what the agent returns for a new quote)
# ---------------------------------------------------------------
class AgentRecommendation(BaseModel):
    supplier_id: str
    price_change_percent: float      # vs last quote
    price_flag: str                  # "spike", "normal", "drop"
    reliability_score: float = Field(ge=0, le=100)
    negotiation_points: list[str]
    suggested_tactic: Tactic
    draft_email: str


# ---------------------------------------------------------------
# 7. MEMORY TEXT HELPERS (what gets sent to Hindsight retain)
# Keep sentences short and factual: Hindsight uses an LLM to
# extract facts, so clear text gives better recall.
# ---------------------------------------------------------------
def quote_to_memory_text(q: Quote, supplier_name: str) -> str:
    return (f"On {q.quote_date.isoformat()}, {supplier_name} quoted "
            f"Rs {q.unit_price:.2f} per unit for {q.quantity} units of {q.item}.")


def discount_to_memory_text(d: Discount, supplier_name: str) -> str:
    return (f"On {d.discount_date.isoformat()}, {supplier_name} gave a "
            f"{d.percent:.1f}% discount because of: {d.reason}.")


def delivery_to_memory_text(d: Delivery, supplier_name: str) -> str:
    status = "on time" if d.days_late == 0 else f"{d.days_late} days late"
    issue = f" Quality issue: {d.quality_issue}." if d.quality_issue else ""
    return (f"Order {d.order_id} from {supplier_name} was delivered {status} "
            f"(promised {d.promised_date.isoformat()}, "
            f"actual {d.actual_date.isoformat()}).{issue}")


def outcome_to_memory_text(o: NegotiationOutcome, supplier_name: str) -> str:
    result = "worked" if o.worked else "did not work"
    return (f"On {o.negotiation_date.isoformat()}, negotiating with {supplier_name}, "
            f"the tactic '{o.tactic_used.value}' {result}. "
            f"Price went from Rs {o.original_price:.2f} to Rs {o.final_price:.2f} per unit."
            f"{' Notes: ' + o.notes if o.notes else ''}")


# ---------------------------------------------------------------
# 8. HINDSIGHT BANK NAMING (Memory Lead: keep this consistent)
# ---------------------------------------------------------------
def bank_id_for(supplier_id: str) -> str:
    """One memory bank per supplier, e.g. 'vendor-sup-001'."""
    return f"vendor-{supplier_id.lower()}"
