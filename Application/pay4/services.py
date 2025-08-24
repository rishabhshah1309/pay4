# pay4/services.py
import os
from decimal import Decimal, ROUND_HALF_UP
import boto3

AWS_REGION = os.getenv("AWS_REGION", "us-west-2")
TEXTRACT_MODE = os.getenv("TEXTRACT_MODE", "stub")  # "stub" or "live"

def textract_analyze_expense(bucket: str, key: str):
    """
    Call Amazon Textract (AnalyzeExpense) to parse a receipt stored in S3.
    In local dev (stub mode), return hardcoded line items.
    """
    if TEXTRACT_MODE != "live":
        return [
            {"description": "Burger", "quantity": 1, "unit_price": 12.50, "total_price": 12.50},
            {"description": "Fries", "quantity": 1, "unit_price": 4.00, "total_price": 4.00},
            {"description": "Soda", "quantity": 2, "unit_price": 3.00, "total_price": 6.00},
        ]

    client = boto3.client("textract", region_name=AWS_REGION)
    resp = client.analyze_expense(Document={"S3Object": {"Bucket": bucket, "Name": key}})

    items = []
    for doc in resp.get("ExpenseDocuments", []):
        for group in doc.get("LineItemGroups", []):
            for line in group.get("LineItems", []):
                fields = {
                    f["Type"]["Text"]: f.get("ValueDetection", {}).get("Text")
                    for f in line.get("LineItemExpenseFields", [])
                    if f.get("Type")
                }
                desc = fields.get("ITEM", "Item")
                qty = Decimal(fields.get("QUANTITY") or "1")
                unit = Decimal(fields.get("PRICE") or "0")
                tot = Decimal(fields.get("AMOUNT") or "0") or (unit * qty)
                items.append({
                    "description": str(desc),
                    "quantity": int(qty),
                    "unit_price": float(unit),
                    "total_price": float(tot),
                })
    return items


def compute_split(items, selections, tax_rate: float, tip_rate: float):
    """
    items: list of dicts {id, total_price, quantity}
    selections: list of dicts {user_email, item_id, quantity_selected}
    """
    D = Decimal

    def to_cents(x: D) -> int:
        return int((x * 100).quantize(D("1"), rounding=ROUND_HALF_UP))

    def from_cents(c: int) -> D:
        return (D(c) / D(100)).quantize(D("0.01"))

    # Per-item unit cost
    per_unit = {i["id"]: D(str(i["total_price"])) / D(str(i.get("quantity", 1))) for i in items}

    # Build per-user subtotals
    from collections import defaultdict
    user_sub = defaultdict(lambda: D("0"))
    for sel in selections:
        unit = per_unit[sel["item_id"]]
        qty = D(str(sel.get("quantity_selected", 1)))
        user_sub[sel["user_email"]] += unit * qty

    total_sub = sum(user_sub.values(), D("0"))
    if total_sub == 0:
        return {}

    tax_total = (total_sub * D(str(tax_rate))).quantize(D("0.01"))
    tip_total = (total_sub * D(str(tip_rate))).quantize(D("0.01"))

    # Allocate tax & tip proportionally
    results = {}
    for user, subtotal in user_sub.items():
        frac = subtotal / total_sub
        tax_share = (tax_total * frac).quantize(D("0.01"))
        tip_share = (tip_total * frac).quantize(D("0.01"))
        results[user] = {
            "subtotal": subtotal.quantize(D("0.01")),
            "tax": tax_share,
            "tip": tip_share,
            "total": subtotal + tax_share + tip_share,
        }
    return results
