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
    def get_or_create_customer(self, user_id: str, email: str,
                                name: Optional[str] = None,
                                existing_id: Optional[str] = None) -> str:
        if existing_id:
            return existing_id
        try:
            c = stripe.Customer.create(email=email, name=name or email,
                                        metadata={"user_id": user_id})
            return c.id
        except stripe.error.StripeError as e:
            raise StripeError(f"Cannot create customer: {e}")

    def create_checkout(self, user_id: str, customer_id: str, plan_id: str,
                        quantity: int = 1, success_url: str = "",
                        cancel_url: str = "") -> Dict[str, str]:
        su = success_url or f"{settings.FRONTEND_URL}/dashboard?checkout=success&session_id={{CHECKOUT_SESSION_ID}}"
        cu = cancel_url or f"{settings.FRONTEND_URL}/pricing?checkout=cancelled"
        try:
            if plan_id == "pay_per_video":
                sess = stripe.checkout.Session.create(
                    customer=customer_id,
                    payment_method_types=["card"],
                    line_items=[{"price_data": {
                        "currency": "usd",
                        "product_data": {"name": "RIFT EFFECT Render Credits",
                                         "description": f"{quantity} render credit(s)"},
                        "unit_amount": settings.PRICE_PER_VIDEO_CENTS,
                    }, "quantity": quantity}],
                    mode="payment",
                    success_url=su, cancel_url=cu,
                    metadata={"user_id": user_id, "type": "credits", "quantity": str(quantity)},
                )
            else:
                price_id = settings.stripe_price_id(plan_id)
                if not price_id:
                    raise StripeError(f"Stripe price not configured for plan: {plan_id}")
                sess = stripe.checkout.Session.create(
                    customer=customer_id,
                    payment_method_types=["card"],
                    line_items=[{"price": price_id, "quantity": 1}],
                    mode="subscription",
                    success_url=su, cancel_url=cu,
                    metadata={"user_id": user_id, "type": "subscription", "plan": plan_id},
                    subscription_data={"metadata": {"user_id": user_id, "plan": plan_id}},
                )
            return {"checkout_url": sess.url, "session_id": sess.id}
        except stripe.error.StripeError as e:
            raise StripeError(f"Checkout failed: {e}")

    def create_portal(self, customer_id: str, return_url: str) -> str:
        try:
            sess = stripe.billing_portal.Session.create(customer=customer_id, return_url=return_url)
            return sess.url
        except stripe.error.StripeError as e:
            raise StripeError(f"Portal failed: {e}")

    def verify_webhook(self, payload: bytes, sig: str) -> Dict[str, Any]:
        try:
            return stripe.Webhook.construct_event(payload, sig, settings.STRIPE_WEBHOOK_SECRET)
        except ValueError:
            raise WebhookError("Invalid payload")
        except stripe.error.SignatureVerificationError:
            raise WebhookError("Invalid signature")

    def check_quota(self, user) -> Tuple[bool, str]:
        from rift.models import Plan
        plan = user.plan
        if plan == Plan.free:
            return False, "Free plan has no renders. Please upgrade."
        if plan == Plan.pay_per_video:
            if (user.credits or 0) <= 0:
                return False, "No render credits. Purchase more to continue."
            return True, "OK"
        if user.sub_status not in ("active", "trialing"):
            return False, "Subscription is not active. Check your billing."
        limit = settings.plan_render_limit(plan.value)
        if (user.renders_this_month or 0) >= limit:
            return False, f"Monthly limit reached ({limit} renders on {plan.value}). Upgrade for more."
        return True, "OK"

    def plans_list(self):
        result = []
        for plan_id, meta in PLANS.items():
            p = meta["price_cents"]
            result.append({
                "id": plan_id,
                "name": meta["name"],
                "price_cents": p,
                "price_display": f"${p/100:.0f}/mo" if plan_id not in ("free", "pay_per_video") else ("Free" if plan_id == "free" else f"${p/100:.0f}/video"),
                "renders_per_month": meta["renders"],
                "features": meta["features"],
            })
        return result

    def handle_checkout_complete(self, data: Dict) -> Dict:
        meta = data.get("metadata", {})
        user_id = meta.get("user_id")
        if not user_id:
            return {}
        t = meta.get("type")
        if t == "credits":
            return {"action": "add_credits", "user_id": user_id,
                    "quantity": int(meta.get("quantity", 1)),
                    "amount_cents": data.get("amount_total", 0),
                    "stripe_session": data.get("id"),
                    "stripe_pi": data.get("payment_intent")}
        elif t == "subscription":
            return {"action": "activate_sub", "user_id": user_id,
                    "plan": meta.get("plan", "starter"),
                    "stripe_sub": data.get("subscription"),
                    "amount_cents": data.get("amount_total", 0),
                    "stripe_session": data.get("id")}
        return {}

    def handle_invoice_paid(self, data: Dict) -> Dict:
        sub_id = data.get("subscription")
        if not sub_id:
            return {}
        try:
            sub = stripe.Subscription.retrieve(sub_id)
            period_end = datetime.fromtimestamp(sub["current_period_end"], tz=timezone.utc)
            return {"action": "renew_sub",
                    "stripe_customer": data.get("customer"),
                    "stripe_sub": sub_id,
                    "plan": sub.get("metadata", {}).get("plan", "starter"),
                    "period_end": period_end,
                    "amount_cents": data.get("amount_paid", 0),
                    "stripe_invoice": data.get("id"),
                    "stripe_charge": data.get("charge")}
        except stripe.error.StripeError as e:
            log.error(f"handle_invoice_paid error: {e}")
            return {}

    def handle_invoice_failed(self, data: Dict) -> Dict:
        return {"action": "past_due", "stripe_customer": data.get("customer")}

    def handle_sub_updated(self, data: Dict) -> Dict:
        ts = data.get("current_period_end")
        return {"action": "update_sub",
                "stripe_sub": data.get("id"),
                "status": data.get("status"),
                "plan": data.get("metadata", {}).get("plan"),
                "period_end": datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None}

    def handle_sub_deleted(self, data: Dict) -> Dict:
        return {"action": "cancel_sub", "stripe_sub": data.get("id")}

    def handle_dispute(self, data: Dict) -> Dict:
        return {"action": "dispute", "stripe_charge": data.get("charge")}