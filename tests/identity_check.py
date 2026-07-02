"""Cross-check all accounting identities for all samples."""
import sys, json
sys.path.insert(0, r"C:\Users\c-leroy.phan\Downloads\ai\ocr_project")
from src.main import process_file

SAMPLES = {
    "S1": r"C:\Users\c-leroy.phan\Downloads\ai\AA_SAMPLE1.pdf",
    "S2": r"C:\Users\c-leroy.phan\Downloads\ai\AA_SAMPLE2.pdf",
    "S3": r"C:\Users\c-leroy.phan\Downloads\ai\AA_SAMPLE3.pdf",
}

IDENTITIES = [
    ("Total Assets",     ["Current Assets", "Non-Current Assets"]),
    ("Total Liabilities", ["Current Liabilities", "Non-Current Liabilities"]),
    ("Total Equity",     ["Paid Up Capital", "Retained Earnings", "Other Reserves"]),
    ("Total Assets",     ["Total Liabilities", "Total Equity"]),
    ("Gross Profit/Loss", ["Revenue", "Cost of Sales"]),
    ("Profit/Loss Before Tax", ["Net Profit/Loss", "Income Tax Expense"]),
]

errors = 0
for label, path in SAMPLES.items():
    print(f"=== {label} ===")
    result = process_file(path, optimization_mode=True)
    
    for target, summands in IDENTITIES:
        t_yrs = result.get(target, {}).get("years", {})
        if not t_yrs:
            continue
        for yr in sorted(t_yrs.keys()):
            tv = t_yrs[yr].get("value")
            if tv is None:
                continue
            sv = 0
            missing = False
            for s in summands:
                s_yrs = result.get(s, {}).get("years", {})
                sv2 = s_yrs.get(yr, {}).get("value")
                if sv2 is None:
                    print(f"  {target}[{yr}]: summand {s} missing")
                    missing = True
                    break
                sv += abs(sv2) if s == "Income Tax Expense" else sv2
            if missing:
                continue
            # For PBT = NP + |ITE|, use sign-aware check
            if target == "Profit/Loss Before Tax":
                sv = 0
                for s in summands:
                    s_yrs = result.get(s, {}).get("years", {})
                    s_val = s_yrs.get(yr, {}).get("value", 0)
                    if s == "Income Tax Expense":
                        s_val = -abs(s_val) if s_val < 0 else abs(s_val)
                    sv += s_val
            diff = abs(tv - sv)
            ok = diff < 1.0
            if not ok:
                print(f"  FAIL {target}[{yr}]: {tv} != {sv} (diff={diff:.0f})")
                errors += 1
            else:
                print(f"  OK {target}[{yr}]: {tv} = {sv}")

print(f"\nErrors: {errors}")
