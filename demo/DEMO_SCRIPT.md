# 3-Minute Demo Script

**Persona:** Arjun, procurement manager at ShipEasy Commerce, buying packaging from 4 suppliers.
**Before you start:** `.env` filled, `python scripts/generate_data.py`, `python memory/load_seed_data.py`,
`streamlit run app/streamlit_app.py`. Click **Reset demo** in the sidebar so the story starts clean.
(Seed data: QuickCart asked ₹17.35 last time but you negotiated it down to ₹16.66. The agent compares against the price you actually PAID.)

## 0:00 The hook (20s)
"Arjun gets 30 supplier quotes a month on WhatsApp and email. He cannot remember who is always late, who gives
discounts for volume, or what he already tried. So he overpays. Let's give his agent a memory."

## 0:20 Step 1, Memory OFF = generic (30s)
1. Sidebar: supplier auto-detect, **Memory OFF**. Load demo **Step 1** (QuickCart).
   ```
   Hi sir, QuickCart this side. New rate for 5-ply 12x10x8 corrugated box Rs 19.43 per pc for 3000 pcs. Valid till 18 Nov. Delivery in 7 days. Pls confirm. - Ramesh
   ```
2. Click **Analyze quote**.
- *Say:* "Metrics are n/a. Advice is textbook: get competing quotes, ask for a volume discount. Nothing about QuickCart."

## 0:50 Step 2, Memory ON = specific (60s)
1. Switch **Memory ON**, analyze the same quote (or use the **Before / After** tab for the side-by-side).
- *Expect:* price **+16.6% spike** vs your last negotiated price (₹16.66 -> ₹19.43), reliability **~67/100** ("late on 4 of 10 orders, avg 5.0 days;
  quality issues on 2 orders"), tactic **delivery_penalty_clause**, and points like
  "delivery penalty has worked 3 of 3 times (avg ~3.5% saving)" and "volume commitment was refused before".
- Point at the **What the agent remembered** panel: dated memories + Hindsight reflection.
- *Say:* "Every point is grounded in a dated memory. It even says what NOT to try."

## 1:50 Step 3, Log an outcome (30s)
Tab **Log outcome**: tactic `competing_quote`, worked **Yes**, final price **₹18.10**, note "Showed Prime quote". Save.
- *Say:* "That outcome and the discount are retained into QuickCart's Hindsight bank."

## 2:20 Step 4, The learning curve (30s)
Load demo **Step 5** (QuickCart follow-up), Memory ON, analyze.
```
Sir QuickCart again. Okay we can do box 12x10x8 at Rs 18.39 for 3000 pcs. This is our best. Dispatch in 7 days.
```
- *Expect:* the baseline is now the ₹18.10 you negotiated, so ₹18.39 shows only about **+1.6% (normal)**, not a spike.
  competing_quote now shows **2/2 worked**, recalled memories include today's outcome, and the advice references it.
- *Say:* "Interaction 1: generic. Interaction 2: specific. Interaction 3: it knows what worked last week."

## 2:50 Close (10s)
"One bank per supplier, retain every quote, delivery and outcome, recall before every negotiation, reflect for
strategy. Memory is the product."

## Optional extra quotes (show the personalities)
| Step | Supplier | Expected advice |
|---|---|---|
| 2 | VolumeMax, ₹19.84 x 5000 (+14.5% vs your last paid price) | Offer volume commitment: 4 of 4 worked, 7 to 10% off |
| 3 | Deccan, ₹25.94 (+9% spike) | Offer early payment: 3 of 3 worked, 3 to 5% off; contract freeze failed |
| 4 | Prime, ₹22.12 | Propose a long-term contract: 2 of 2 worked, 5 to 6% off; competing quote failed |

## If something breaks live
- LLM down: the app shows rule-based advice (still history-grounded) with a warning.
- Hindsight slow: recall runs 3 queries in parallel; keep the Before/After tab for the money shot.
