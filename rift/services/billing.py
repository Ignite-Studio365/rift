from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
import stripe
import logging

from rift.core.config import settings
from rift.core.exceptions import StripeError, WebhookError, QuotaExceeded

log = logging.getLogger("rift.billing")
stripe.api_key = settings.STRIPE_SECRET_KEY

PLANS = {
    "free":          {"name": "Free",          "renders": 0,    "price_cents": 0,    "features": ["Preview only", "No renders"]},
    "starter":       {"name": "Starter",        "renders": settings.PLAN_STARTER_RENDERS, "price_cents": 1900, "features": [f"{settings.PLAN_STARTER_RENDERS} renders/mo", "4K output", "All 11 effects", "All 7 overlays", "Audio sync"]},
    "pro":           {"name": "Pro",            "renders": settings.PLAN_PRO_RENDERS,     "price_cents": 4900, "features": [f"{settings.PLAN_PRO_RENDERS} renders/mo", "8K AI Enhanced", "All effects", "Custom audio", "Trim & crop", "API access"]},
    "studio":        {"name": "Studio",         "renders": settings.PLAN_STUDIO_RENDERS,  "price_cents": 9900, "features": [f"{settings.PLAN_STUDIO_RENDERS} renders/mo", "8K AI Enhanced", "All features", "Priority queue", "SLA support", "API access"]},
    "pay_per_video": {"name": "Pay Per Video",  "renders": None, "price_cents": settings.PRICE_PER_VIDEO_CENTS, "features": ["No subscription", "8K AI Enhanced", "All effects", "Credits never expire", "API access"]},
}


class BillingService: