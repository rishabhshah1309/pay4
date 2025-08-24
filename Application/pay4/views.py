import json, uuid
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.http import require_http_methods
from django.db import transaction
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404, redirect
from receipts.models import Receipt, ReceiptItem, Selection, Invite
from .services import presign_upload, textract_analyze_expense, compute_split, send_invite_email
from decimal import Decimal
import os
@login_required
def dashboard(request):
    receipts = Receipt.objects.filter(owner=request.user).order_by("-created_at")
    return render(request, "pay4/dashboard.html", {"receipts": receipts})

@login_required
def new_receipt(request):
    """
    Create an empty receipt and show the upload page (direct S3 upload).
    """
    receipt = Receipt.objects.create(
        owner=request.user,
        merchant="(untitled)",
        tax_rate=Decimal("0.0925"),
        tip_rate=Decimal("0.18"),
        status="uploaded",
    )
    return redirect("upload_receipt", receipt_id=receipt.id)

@login_required
def upload_receipt(request, receipt_id: int):
    r = get_object_or_404(Receipt, id=receipt_id, owner=request.user)
    return render(request, "pay4/upload.html", {"receipt": r})

@login_required
@require_http_methods(["POST"])
def presign_endpoint(request, receipt_id: int):
    """
    POST JSON: {"content_type":"image/jpeg"|"application/pdf"}
    Returns {url, fields} for S3 POST.
    """
    r = get_object_or_404(Receipt, id=receipt_id, owner=request.user)
    try:
        payload = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Bad JSON")
    content_type = payload.get("content_type") or "application/octet-stream"

    # key: receipts/{owner_id}/{receipt_id}/{uuid}
    key = f"receipts/{request.user.id}/{r.id}/{uuid.uuid4().hex}"
    post = presign_upload(key, content_type=content_type)
    # store key so we know where it landed
    r.s3_key = key
    r.save(update_fields=["s3_key"])
    return JsonResponse(post)

@login_required
def process_receipt(request, receipt_id: int):
    """
    Calls Textract (stub/live) to populate ReceiptItem rows, then goes to selection.
    """
    r = get_object_or_404(Receipt, id=receipt_id, owner=request.user)
    if not r.s3_key:
        return HttpResponseBadRequest("Upload the file first.")
    items = textract_analyze_expense(
        bucket=os.getenv("AWS_STORAGE_BUCKET_NAME", ""),
        key=r.s3_key,
    )
    # refresh items
    with transaction.atomic():
        r.items.all().delete()
        for it in items:
            ReceiptItem.objects.create(
                receipt=r,
                description=it["description"],
                quantity=it.get("quantity", 1),
                unit_price=it.get("unit_price", 0.0),
                total_price=it.get("total_price", 0.0),
            )
        r.status = "processed"
        r.merchant = r.merchant or "Parsed Receipt"
        r.save(update_fields=["status", "merchant"])
    return redirect("select_items", receipt_id=r.id)

@login_required
def select_items(request, receipt_id: int):
    """
    Shows items and lets the current user select quantities they consumed.
    """
    r = get_object_or_404(Receipt, id=receipt_id, owner=request.user)
    items = r.items.all().order_by("id")

    if request.method == "POST":
        # Save/update selections for this user
        email = request.user.email or f"user-{request.user.id}@local"
        with transaction.atomic():
            Selection.objects.filter(receipt=r, user_email=email).delete()
            for it in items:
                qty = int(request.POST.get(f"qty_{it.id}", "0") or "0")
                if qty > 0:
                    Selection.objects.create(
                        receipt=r, item=it, user_email=email, quantity_selected=qty
                    )
        return redirect("split", receipt_id=r.id)

    return render(request, "pay4/select.html", {"receipt": r, "items": items})

@login_required
def split_view(request, receipt_id: int):
    """
    Computes the split from ALL saved selections on this receipt.
    """
    r = get_object_or_404(Receipt, id=receipt_id, owner=request.user)
    items = [
        {"id": i.id, "description": i.description, "quantity": i.quantity, "total_price": float(i.total_price)}
        for i in r.items.all()
    ]
    selections = [
        {"user_email": s.user_email, "item_id": s.item_id, "quantity_selected": s.quantity_selected}
        for s in Selection.objects.filter(receipt=r)
    ]
    results = compute_split(items, selections, float(r.tax_rate or 0), float(r.tip_rate or 0))
    return render(request, "pay4/split.html", {"receipt": r, "results": results})

@login_required
def invite_manager(request, receipt_id: int):
    """Owner manages invites and can send new ones."""
    r = get_object_or_404(Receipt, id=receipt_id, owner=request.user)
    invites = r.invites.order_by("-created_at")
    if request.method == "POST":
        email = request.POST.get("email", "").strip().lower()
        if not email:
            return HttpResponseBadRequest("Email required")
        inv = Invite.objects.create(receipt=r, invitee_email=email)
        link = send_invite_email(email, inv.token, r.id, r.merchant or "Receipt")
        # surface link to UI too
        return render(request, "pay4/invites.html", {"receipt": r, "invites": invites, "new_link": link})
    return render(request, "pay4/invites.html", {"receipt": r, "invites": invites})

@require_http_methods(["GET", "POST"])
def invite_select(request, token: str):
    """
    Invitee (no login) selects items using a one-time token.
    Each submit overwrites their prior selections for this receipt.
    """
    inv = get_object_or_404(Invite, token=token)
    r = inv.receipt
    items = r.items.all().order_by("id")
    email = inv.invitee_email

    if request.method == "POST":
        with transaction.atomic():
            Selection.objects.filter(receipt=r, user_email=email).delete()
            for it in items:
                qty = int(request.POST.get(f"qty_{it.id}", "0") or "0")
                if qty > 0:
                    Selection.objects.create(receipt=r, item=it, user_email=email, quantity_selected=qty)
            inv.status = "accepted"
            inv.save(update_fields=["status"])
        # simple thank-you page showing a summary
        selections = Selection.objects.filter(receipt=r, user_email=email)
        return render(request, "pay4/invite_thanks.html", {"receipt": r, "email": email, "selections": selections})

    return render(request, "pay4/invite_select.html", {"receipt": r, "items": items, "email": email})