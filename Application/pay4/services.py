# pay4/services.py
import os
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Dict, Any

import boto3
from botocore.client import Config

# ---- AWS config ----
AWS_REGION = os.getenv("AWS_REGION", "us-west-2")
BUCKET = os.getenv("AWS_STORAGE_BUCKET_NAME", "")
TEXTRACT_MODE = os.getenv("TEXTRACT_MODE", "stub")  # "stub" or "live"


# -----------------------------
# S3: presigned POST for uploads
# -----------------------------
def _s3_client():
    """
    S3 client with SigV4 (required for presigned POST).
    Credentials are taken from the default provider chain (env/cred file/role).
    """
    return boto3.client("s3", region_name=AWS_REGION, config=Config(signature_version="s3v4"))


def presign_upload(key: str, content_type: str = "application/octet-stream", expires_seconds: int = 900) -> Dict[str, Any]:
    """
    Generate a presigned POST so the browser can upload directly to S3.

    Returns a dict like:
    {
      "url": "https://<bucket>.s3.amazonaws.com",
      "fields": {...}   # include these as form fields plus the file itself
    }
    """
    if not BUCKET:
        raise RuntimeError("AWS_STORAGE_BUCKET_NAME is not set")

    client = _s3_client()

    # Conditions: enforce bucket, key prefix, content-type, and size
    conditions = [
        {"bucket": BUCKET},
        ["starts-with", "$key", key],
        {"Content-Type": content_type},
        ["content-length-range", 0, 15_000_000],  # 15MB cap (adjust as needed)
    ]
    fields = {"Content-Type": content_type, "key": key}

    post = client.generate_presigned_post(
        Bucket=BUCKET,
        Key=key,
        Fields=fields,
        Conditions=conditions,
        ExpiresIn=expires_seconds,
    )
    return post


# -----------------------------
# Textract: AnalyzeExpense
# -----------------------------
def textract_analyze_expense(bucket: str, key: str) -> List[Dict[str, Any]]:
    """
    Call Amazon Textract (AnalyzeExpense) to parse a receipt stored in S3.
    In local dev (stub mode), returns hardcoded line items.
    Each item is: {"description": str, "quantity": int, "unit_price": float, "total_price": float}
    """
    if TEXTRACT_MODE != "live":
        # Stubbed example data for local development
        return [
            {"description": "Burger", "quantity": 1, "unit_price": 12.50, "total_price": 12.50},
            {"description": "Fries", "quantity": 1, "unit_price": 4.00, "total_price": 4.00},
            {"description": "Soda", "quantity": 2, "unit_price": 3.00, "total_price": 6.00},
        ]

    client = boto3.client("textract", region_name=AWS_REGION)
    resp = client.analyze_expense(Document={"S3Object": {"Bucket": bucket, "Name": key}})

    items: List[Dict[str, Any]] = []
    for doc in resp.get("ExpenseDocuments", []):
        for group in doc.get("LineItemGroups", []):
            for line in group.get("LineItems", []):
                fields = {
                    f["Type"]["Text"]: f.get("ValueDetection", {}).get("Text")
                    for f in line.get("LineItemExpenseFields", [])
                    if f.get("Type")
                }
                desc = fields.get("ITEM") or fields.get("DESCRIPTION") or "Item"
                qty_str = fields.get("QUANTITY") or "1"
                unit_str = fields.get("PRICE") or "0"
                amt_str = fields.get("AMOUNT") or "0"

                # Use Decimal for safe math, then cast to float for storage/JSON
                qty = Decimal(qty_str or "1")
                unit = Decimal(unit_str or "0")
                total = Decimal(amt_str or "0")
                if total == 0 and unit != 0:
                    total = unit * qty

                items.append(
                    {
                        "description": str(desc),
                        "quantity": int(qty),
                        "unit_price": float(unit),
                        "total_price": float(total),
                    }
                )
    return items


# -----------------------------
# Split computation
# -----------------------------
def compute_split(
    items: List[Dict[str, Any]],
    selections: List[Dict[str, Any]],
    tax_rate: float,
    tip_rate: float,
) -> Dict[str, Dict[str, Decimal]]:
    """
    Compute per-user subtotal/tax/tip/total.

    items:       list of dicts {id, total_price, quantity}
    selections:  list of dicts {user_email, item_id, quantity_selected}
    tax_rate:    e.g., 0.0925
    tip_rate:    e.g., 0.18

    Returns dict: { user_email: {"subtotal": D, "tax": D, "tip": D, "total": D}, ... }
    """
    D = Decimal

    def to_cents(x: D) -> int:
        return int((x * 100).quantize(D("1"), rounding=ROUND_HALF_UP))

    def from_cents(c: int) -> D:
        return (D(c) / D(100)).quantize(D("0.01"))

    # Validate inputs
    if not items:
        return {}

    # Per-item unit cost
    per_unit: Dict[Any, Decimal] = {}
    for i in items:
        qty = D(str(i.get("quantity", 1) or 1))
        total_price = D(str(i.get("total_price", 0)))
        per_unit[i["id"]] = (total_price / qty) if qty != 0 else D("0")

    # Accumulate per-user subtotals
    from collections import defaultdict

    user_sub = defaultdict(lambda: D("0"))
    for sel in selections:
        unit = per_unit.get(sel["item_id"], D("0"))
        qty = D(str(sel.get("quantity_selected", 1)))
        user_sub[sel["user_email"]] += unit * qty

    total_sub = sum(user_sub.values(), D("0"))
    if total_sub == 0:
        return {}

    # Compute tax and tip based on subtotal (not including tax/tip)
    tax_total = (total_sub * D(str(tax_rate or 0))).quantize(D("0.01"), rounding=ROUND_HALF_UP)
    tip_total = (total_sub * D(str(tip_rate or 0))).quantize(D("0.01"), rounding=ROUND_HALF_UP)

    # Allocate tax & tip proportionally by user's subtotal
    # Do the split in cents to minimize rounding issues; reconcile residuals.
    results: Dict[str, Dict[str, Decimal]] = {}
    user_order = list(user_sub.keys())

    # Subtotals to cents
    sub_cents = {u: to_cents(v.quantize(D("0.01"))) for u, v in user_sub.items()}
    total_sub_cents = sum(sub_cents.values()) or 1  # avoid div-by-zero

    tax_cents_total = to_cents(tax_total)
    tip_cents_total = to_cents(tip_total)

    # Proportional allocation in cents
    tax_alloc = {}
    tip_alloc = {}
    running_tax = 0
    running_tip = 0
    for idx, u in enumerate(user_order):
        # For the last user, assign the residual to ensure sums match exactly
        if idx == len(user_order) - 1:
            tax_c = tax_cents_total - running_tax
            tip_c = tip_cents_total - running_tip
        else:
            frac = Decimal(sub_cents[u]) / Decimal(total_sub_cents)
            tax_c = int((Decimal(tax_cents_total) * frac).quantize(D("1"), rounding=ROUND_HALF_UP))
            tip_c = int((Decimal(tip_cents_total) * frac).quantize(D("1"), rounding=ROUND_HALF_UP))
            running_tax += tax_c
            running_tip += tip_c
        tax_alloc[u] = tax_c
        tip_alloc[u] = tip_c

    # Build final results
    for u in user_order:
        subtotal = from_cents(sub_cents[u])
        tax = from_cents(tax_alloc[u])
        tip = from_cents(tip_alloc[u])
        total = (subtotal + tax + tip).quantize(D("0.01"))
        results[u] = {"subtotal": subtotal, "tax": tax, "tip": tip, "total": total}

    return results
