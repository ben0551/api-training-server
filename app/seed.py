"""
Default training datasets.

Every instance gets a *seed snapshot* stored as JSON. "Reset" restores from that
snapshot, so whatever participants break is recoverable in one click.

Three presets are available:
  standard  - the dataset used in the course workbook (5 customers, 5 applications,
              4 loans) with deliberately odd cases for the exercises
  small     - 3 customers / 2 applications / 2 loans, for a quick demo
  large     - 60 customers / 90 applications / 80 loans, for pagination and
              reporting exercises where a handful of rows teaches nothing
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta, timezone

PRODUCTS = ["Fixed Rate Home Loan", "Variable Rate Home Loan", "Investment Loan", "Personal Loan"]
PURPOSES = ["Purchase", "Refinance", "Investment", "Car", "Other"]
STATES = ["NSW", "VIC", "QLD", "WA", "SA", "TAS", "ACT", "NT"]
EMPLOYMENTS = ["PAYG", "Self-employed", "Contractor", "Retired"]
FIRST_NAMES = ["Margaret", "Tomas", "Priya", "Daniel", "Yuki", "Amelia", "Ravi", "Chloe", "Ibrahim",
               "Sophie", "Nguyen", "Liam", "Fatima", "Ethan", "Isla", "Marco", "Aisha", "Hugo",
               "Grace", "Nikolai", "Leila", "Owen", "Mei", "Anders", "Rosa", "Kofi", "Elena",
               "Jonah", "Anika", "Pedro", "Hannah", "Yusuf", "Clara", "Dmitri", "Nia", "Felix"]
SURNAMES = ["Whitfield", "Nowak", "Raman", "Okafor", "Tanaka", "Bell", "Chandra", "Doyle",
            "Hassan", "Kaur", "Le", "Murphy", "Nasser", "O'Brien", "Petrov", "Quinn", "Reyes",
            "Silva", "Tremblay", "Ueda", "Vargas", "Walsh", "Xu", "Yilmaz", "Zhang", "Novak",
            "Fischer", "Hughes", "Ibrahim", "Jensen"]


def _iso(d: date) -> str:
    return d.isoformat()


def _stamp(days_ago: int, hour: int = 10) -> str:
    d = datetime.now(timezone.utc).astimezone() - timedelta(days=days_ago)
    return d.replace(hour=hour, minute=12, second=0, microsecond=0).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# The dataset used throughout the course
# --------------------------------------------------------------------------

STANDARD = {
    "customers": [
        {"id": "CUS-100001", "first_name": "Margaret", "surname": "Whitfield", "dob": "1978-04-12",
         "email": "m.whitfield@example.com", "mobile": "+61400111222", "state": "NSW",
         "employment": "PAYG", "annual_income": 142000, "credit_score": 812, "created_at": "2024-03-11"},
        {"id": "CUS-100002", "first_name": "Tomas", "surname": "Nowak", "dob": "1985-09-30",
         "email": "tomas.nowak@example.com", "mobile": "+61400333444", "state": "VIC",
         "employment": "Self-employed", "annual_income": 98000, "credit_score": 694, "created_at": "2024-05-02"},
        {"id": "CUS-100003", "first_name": "Priya", "surname": "Raman", "dob": "1992-01-22",
         "email": "priya.raman@example.com", "mobile": "+61400555666", "state": "QLD",
         "employment": "PAYG", "annual_income": 118500, "credit_score": 775, "created_at": "2025-01-19"},
        {"id": "CUS-100004", "first_name": "Daniel", "surname": "Okafor", "dob": "1989-11-05",
         "email": "d.okafor@example.com", "mobile": "+61400777888", "state": "WA",
         "employment": "PAYG", "annual_income": 76000, "credit_score": 611, "created_at": "2025-06-27"},
        {"id": "CUS-100005", "first_name": "Yuki", "surname": "Tanaka", "dob": "1996-07-17",
         "email": "yuki.tanaka@example.com", "mobile": "+61400999000", "state": "NSW",
         "employment": "Contractor", "annual_income": 156000, "credit_score": 731, "created_at": "2026-02-08"},
    ],
    "applications": [
        {"id": "APP-2026-000411", "customer_id": "CUS-100001", "product": "Fixed Rate Home Loan",
         "amount": 620000, "term_months": 360, "rate_pct": 6.14, "purpose": "Purchase",
         "status": "APPROVED", "submitted_at": _stamp(8, 9), "decision_at": _stamp(6, 16),
         "assessor": "Ben Fischer"},
        {"id": "APP-2026-000412", "customer_id": "CUS-100002", "product": "Variable Rate Home Loan",
         "amount": 415000, "term_months": 300, "rate_pct": 6.39, "purpose": "Refinance",
         "status": "REFERRED", "submitted_at": _stamp(5, 11), "decision_at": None, "assessor": None},
        {"id": "APP-2026-000413", "customer_id": "CUS-100003", "product": "Investment Loan",
         "amount": 780000, "term_months": 360, "rate_pct": 6.72, "purpose": "Investment",
         "status": "IN_REVIEW", "submitted_at": _stamp(3, 14), "decision_at": None,
         "assessor": "Ava Nguyen"},
        {"id": "APP-2026-000414", "customer_id": "CUS-100004", "product": "Personal Loan",
         "amount": 28000, "term_months": 60, "rate_pct": 11.90, "purpose": "Car",
         "status": "DECLINED", "submitted_at": _stamp(12, 8), "decision_at": _stamp(11, 10),
         "assessor": "Ben Fischer"},
        {"id": "APP-2026-000415", "customer_id": "CUS-100005", "product": "Fixed Rate Home Loan",
         "amount": 910000, "term_months": 360, "rate_pct": 6.09, "purpose": "Purchase",
         "status": "SUBMITTED", "submitted_at": _stamp(1, 7), "decision_at": None, "assessor": None},
    ],
    "loans": [
        {"id": "LON-90001", "application_id": "APP-2026-000411", "customer_id": "CUS-100001",
         "product": "Fixed Rate Home Loan", "original_amount": 620000, "balance": 598412.77,
         "rate_pct": 6.14, "term_months": 360, "payments_made": 4,
         "next_payment_due": _iso(date.today().replace(day=1) + timedelta(days=32)),
         "repayment_frequency": "MONTHLY", "repayment_amount": 3775.41, "status": "CURRENT",
         "opened_at": _iso(date.today() - timedelta(days=144)), "arrears_days": 0},
        {"id": "LON-90002", "application_id": None, "customer_id": "CUS-100002",
         "product": "Variable Rate Home Loan", "original_amount": 288000, "balance": 231884.10,
         "rate_pct": 6.39, "term_months": 240, "payments_made": 61,
         "next_payment_due": _iso(date.today() + timedelta(days=9)),
         "repayment_frequency": "FORTNIGHTLY", "repayment_amount": 918.22, "status": "CURRENT",
         "opened_at": "2021-10-15", "arrears_days": 0},
        {"id": "LON-90003", "application_id": None, "customer_id": "CUS-100004",
         "product": "Personal Loan", "original_amount": 42000, "balance": 14875.63,
         "rate_pct": 12.40, "term_months": 84, "payments_made": 39,
         "next_payment_due": _iso(date.today() - timedelta(days=14)),
         "repayment_frequency": "MONTHLY", "repayment_amount": 758.90, "status": "ARREARS",
         "opened_at": "2023-02-20", "arrears_days": 14},
        {"id": "LON-90004", "application_id": None, "customer_id": "CUS-100003",
         "product": "Investment Loan", "original_amount": 540000, "balance": 512330.05,
         "rate_pct": 6.72, "term_months": 360, "payments_made": 11,
         "next_payment_due": _iso(date.today() + timedelta(days=21)),
         "repayment_frequency": "MONTHLY", "repayment_amount": 3489.12, "status": "CURRENT",
         "opened_at": "2025-12-05", "arrears_days": 0},
    ],
    "payments": [
        {"id": "PAY-500001", "loan_id": "LON-90001", "amount": 3775.41, "paid_at": _stamp(21, 9),
         "method": "DIRECT_DEBIT", "reference": "DD NOV", "reversed": False},
        {"id": "PAY-500002", "loan_id": "LON-90001", "amount": 3775.41, "paid_at": _stamp(51, 9),
         "method": "DIRECT_DEBIT", "reference": "DD OCT", "reversed": False},
        {"id": "PAY-500003", "loan_id": "LON-90002", "amount": 918.22, "paid_at": _stamp(8, 9),
         "method": "BPAY", "reference": "91822", "reversed": False},
        {"id": "PAY-500004", "loan_id": "LON-90003", "amount": 400.00, "paid_at": _stamp(16, 9),
         "method": "BPAY", "reference": "PART PAY", "reversed": False},
    ],
}


SMALL = {
    "customers": STANDARD["customers"][:3],
    "applications": [a for a in STANDARD["applications"] if a["customer_id"] in
                     {"CUS-100001", "CUS-100002", "CUS-100003"}][:2],
    "loans": [l for l in STANDARD["loans"] if l["customer_id"] in
              {"CUS-100001", "CUS-100003"}][:2],
    "payments": [p for p in STANDARD["payments"] if p["loan_id"] in {"LON-90001", "LON-90004"}],
}


def generate_large(customers: int = 60, seed: int = 7) -> dict:
    """A bigger book, for exercises where five rows teach nothing.

    Deterministic for a given seed, so a regenerated instance looks the same.
    """
    rng = random.Random(seed)
    cust, apps, loans, pays = [], [], [], []
    today = date.today()

    for i in range(customers):
        cid = f"CUS-{200000 + i:06d}"
        first, last = rng.choice(FIRST_NAMES), rng.choice(SURNAMES)
        cust.append({
            "id": cid, "first_name": first, "surname": last,
            "dob": f"{rng.randint(1960, 2002)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
            "email": f"{first.lower()}.{last.lower().replace(chr(39), '')}@example.com",
            "mobile": f"+614{rng.randint(10000000, 99999999)}",
            "state": rng.choice(STATES), "employment": rng.choice(EMPLOYMENTS),
            "annual_income": rng.choice([62000, 74000, 88000, 96000, 105000, 118000, 132000, 145000, 168000, 210000]),
            "credit_score": rng.randint(480, 880),
            "created_at": _iso(today - timedelta(days=rng.randint(20, 1400))),
        })

    app_no = 500
    loan_no = 70000
    pay_no = 600000
    for c in cust:
        for _ in range(rng.randint(1, 2)):
            product = rng.choice(PRODUCTS)
            amount = {"Personal Loan": rng.randrange(8000, 60000, 500),
                      "Fixed Rate Home Loan": rng.randrange(250000, 1200000, 5000),
                      "Variable Rate Home Loan": rng.randrange(200000, 950000, 5000),
                      "Investment Loan": rng.randrange(300000, 1400000, 5000)}[product]
            status = rng.choices(["SUBMITTED", "IN_REVIEW", "REFERRED", "APPROVED", "DECLINED"],
                                 weights=[18, 20, 12, 35, 15])[0]
            days = rng.randint(1, 120)
            app_no += 1
            apps.append({
                "id": f"APP-2026-{app_no:06d}", "customer_id": c["id"], "product": product,
                "amount": amount, "term_months": rng.choice([60, 120, 240, 300, 360]),
                "rate_pct": round(rng.uniform(5.9, 12.5), 2), "purpose": rng.choice(PURPOSES),
                "status": status,
                "submitted_at": _stamp(days, rng.randint(8, 17)),
                "decision_at": _stamp(days - 2, rng.randint(8, 17)) if status in ("APPROVED", "DECLINED") else None,
                "assessor": rng.choice(["Ben Fischer", "Ava Nguyen", None]) if status in ("APPROVED", "DECLINED") else None,
            })
            if status == "APPROVED" and rng.random() < 0.85:
                loan_no += 1
                rate = round(rng.uniform(5.9, 12.5), 2)
                term = rng.choice([60, 240, 300, 360])
                made = rng.randint(1, min(term - 1, 72))
                balance = round(amount * (1 - made / term) * rng.uniform(0.97, 1.03), 2)
                arrears = rng.choices([0, 0, 0, 0, 7, 14, 35, 62], weights=[50, 15, 10, 8, 6, 5, 4, 2])[0]
                freq = rng.choice(["MONTHLY", "FORTNIGHTLY", "WEEKLY"])
                divisor = {"MONTHLY": 1, "FORTNIGHTLY": 2.17, "WEEKLY": 4.33}[freq]
                loans.append({
                    "id": f"LON-{loan_no}", "application_id": apps[-1]["id"], "customer_id": c["id"],
                    "product": product, "original_amount": amount, "balance": balance,
                    "rate_pct": rate, "term_months": term, "payments_made": made,
                    "next_payment_due": _iso(today + timedelta(days=rng.randint(-arrears, 30) if arrears else rng.randint(1, 30))),
                    "repayment_frequency": freq,
                    "repayment_amount": round(balance * (rate / 100 / 12) / (1 - (1 + rate / 100 / 12) ** -(term - made)) / divisor + 0.001, 2),
                    "status": "ARREARS" if arrears else "CURRENT",
                    "opened_at": _iso(today - timedelta(days=made * 30 + rng.randint(0, 25))),
                    "arrears_days": arrears,
                })
                for _ in range(min(made, 6)):
                    pay_no += 1
                    pays.append({
                        "id": f"PAY-{pay_no}", "loan_id": loans[-1]["id"],
                        "amount": loans[-1]["repayment_amount"],
                        "paid_at": _stamp(rng.randint(1, made * 30), 9),
                        "method": rng.choice(["DIRECT_DEBIT", "BPAY", "TRANSFER", "CARD"]),
                        "reference": rng.choice(["DD", "BPAY", "TOP UP", "EXTRA", "SCHEDULED"]),
                        "reversed": False,
                    })
    return {"customers": cust, "applications": apps, "loans": loans, "payments": pays}


PRESETS = {
    "standard": lambda: STANDARD,
    "small": lambda: SMALL,
    "large": lambda: generate_large(),
}

PRESET_LABELS = {
    "standard": "Standard — the course dataset (5 customers, 5 applications, 4 loans)",
    "small": "Small — 3 customers, 2 applications, 2 loans (quick demo)",
    "large": "Large — 60 customers, ~90 applications, ~80 loans (pagination & reporting)",
}


def get_preset(name: str) -> dict:
    return PRESETS.get(name, PRESETS["standard"])()


def validate_dataset(data: dict) -> list[str]:
    """Return a list of human-readable problems. Empty list means it is usable."""
    problems: list[str] = []
    if not isinstance(data, dict):
        return ["The dataset must be a JSON object with keys: customers, applications, loans, payments."]
    for key in ("customers", "applications", "loans", "payments"):
        if key not in data:
            problems.append(f"Missing the '{key}' list.")
        elif not isinstance(data[key], list):
            problems.append(f"'{key}' must be a list.")
    if problems:
        return problems

    required = {
        "customers": ["id", "first_name", "surname", "state", "annual_income", "credit_score"],
        "applications": ["id", "customer_id", "product", "amount", "term_months", "status", "submitted_at"],
        "loans": ["id", "customer_id", "product", "original_amount", "balance", "rate_pct", "status"],
        "payments": ["id", "loan_id", "amount", "paid_at", "method", "reference"],
    }
    seen: dict[str, set] = {}
    for key, fields in required.items():
        ids = set()
        for i, row in enumerate(data[key]):
            if not isinstance(row, dict):
                problems.append(f"{key}[{i}] is not an object.")
                continue
            for f in fields:
                if f not in row:
                    problems.append(f"{key}[{i}] is missing required field '{f}'.")
            rid = row.get("id")
            if rid in ids:
                problems.append(f"{key}[{i}] has a duplicate id '{rid}'.")
            ids.add(rid)
        seen[key] = ids

    for i, row in enumerate(data["applications"]):
        if row.get("customer_id") not in seen["customers"]:
            problems.append(f"applications[{i}] refers to customer '{row.get('customer_id')}', which does not exist.")
    for i, row in enumerate(data["loans"]):
        if row.get("customer_id") not in seen["customers"]:
            problems.append(f"loans[{i}] refers to customer '{row.get('customer_id')}', which does not exist.")
    for i, row in enumerate(data["payments"]):
        if row.get("loan_id") not in seen["loans"]:
            problems.append(f"payments[{i}] refers to loan '{row.get('loan_id')}', which does not exist.")
    return problems[:40]
