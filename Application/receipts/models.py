from django.db import models
from django.contrib.auth import get_user_model
from django.utils.crypto import get_random_string

User = get_user_model()

class Receipt(models.Model):
    STATUS_CHOICES = [
        ("uploaded", "Uploaded"),
        ("processed", "Processed"),
        ("splitting", "Splitting"),
        ("settled", "Settled"),
    ]
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="receipts")
    s3_key = models.CharField(max_length=512, blank=True, null=True)
    merchant = models.CharField(max_length=255, blank=True, null=True)
    purchase_time = models.DateTimeField(blank=True, null=True)
    currency = models.CharField(max_length=8, default="USD")
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    tip_amount = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    tax_rate = models.DecimalField(max_digits=6, decimal_places=4, blank=True, null=True)
    tip_rate = models.DecimalField(max_digits=6, decimal_places=4, blank=True, null=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default="uploaded")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.merchant or 'Receipt'} ({self.pk})"

class ReceiptItem(models.Model):
    receipt = models.ForeignKey(Receipt, on_delete=models.CASCADE, related_name="items")
    description = models.CharField(max_length=255)
    quantity = models.IntegerField(default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    total_price = models.DecimalField(max_digits=12, decimal_places=2)
    category = models.CharField(max_length=64, blank=True, null=True)

    def __str__(self):
        return f"{self.description} x{self.quantity}"

def generate_token():
    return get_random_string(32)

class Invite(models.Model):
    receipt = models.ForeignKey("receipts.Receipt", on_delete=models.CASCADE, related_name="invites")
    invitee_email = models.EmailField()
    token = models.CharField(max_length=64, default=generate_token, unique=True)  # wider length is safer
    status = models.CharField(max_length=20, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)

class Selection(models.Model):
    receipt = models.ForeignKey(Receipt, on_delete=models.CASCADE, related_name="selections")
    item = models.ForeignKey(ReceiptItem, on_delete=models.CASCADE, related_name="selections")
    user_email = models.EmailField()
    quantity_selected = models.IntegerField(default=1)

class SplitShare(models.Model):
    receipt = models.ForeignKey(Receipt, on_delete=models.CASCADE, related_name="splits")
    user_email = models.EmailField()
    subtotal_share = models.DecimalField(max_digits=12, decimal_places=2)
    tax_share = models.DecimalField(max_digits=12, decimal_places=2)
    tip_share = models.DecimalField(max_digits=12, decimal_places=2)
    total_due = models.DecimalField(max_digits=12, decimal_places=2)
    settled = models.BooleanField(default=False)
    settled_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        unique_together = [("receipt", "user_email")]
