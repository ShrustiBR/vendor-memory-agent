"""Diagnose Hindsight setup. Run: python scripts/check_hindsight.py"""
import os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
from hindsight_client import Hindsight

url, key = os.getenv("HINDSIGHT_BASE_URL"), os.getenv("HINDSIGHT_API_KEY", "")
print("Base URL :", url)
print("API key  :", (key[:6] + "..." + key[-4:]) if len(key) > 12 else repr(key), f"(length {len(key)})")
c = Hindsight(base_url=url, api_key=key or None, timeout=60.0)
for label, fn in [
    ("version", lambda: c.get_version()),
    ("create bank", lambda: c.create_bank(bank_id="vendor-healthcheck", name="healthcheck")),
    ("retain", lambda: c.retain(bank_id="vendor-healthcheck", content="Healthcheck: QuickCart quoted Rs 10.")),
    ("recall", lambda: c.recall(bank_id="vendor-healthcheck", query="QuickCart")),
]:
    try:
        fn(); print(f"[OK]   {label}")
    except Exception as e:
        print(f"[FAIL] {label}: status={getattr(e,'status',None)} body={str(getattr(e,'body',e))[:400]}")
c.close()
