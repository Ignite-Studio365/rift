import math
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from rift.core.database import get_db
from rift.core.config import settings
from rift.models import User, Payment, PaymentStatus, Plan
from rift.core.schemas import (
    CheckoutIn, CheckoutOut, PortalIn, PortalOut, BillingOut,
    PlansOut, PaymentOut, PaymentListOut, Msg,
)
from rift.api.deps import current_user, verified_user, get_billing, pagination
from rift.services.billing import BillingService
import logging

log = logging.getLogger("rift.billing_routes")
router = APIRouter(prefix="/billing", tags=["Billing"])


@router.get("/plans", response_model=PlansOut)
async def get_plans(billing: BillingService = Depends(get_billing)):
    return PlansOut(plans=billing.plans_list())


@router.get("/status", response_model=BillingOut)
async def billing_status(user: User = Depends(current_user),
                          billing: BillingService = Depends(get_billing)):
    can, msg = billing.check_quota(user)
    plan = user.plan.value if user.plan else "free"
    limit = settings.plan_render_limit(plan)
    used = user.renders_this_month or 0
    return BillingOut(
        plan=plan,
        plan_name={"free": "Free", "starter": "Starter", "pro": "Pro",
                   "studio": "Studio", "pay_per_video": "Pay Per Video"}.get(plan, "Free"),
        sub_status=user.sub_status,
        sub_expires=user.sub_period_end,
        credits=user.credits or 0,
        renders_this_month=used,
        monthly_limit=limit,
        renders_left=max(0, limit - used),
        can_render=can,
        quota_message=None if can else msg,
        total_spent_dollars=round((user.total_spent_cents or 0) / 100, 2),
    )


@router.post("/checkout", response_model=CheckoutOut)
async def create_checkout(body: CheckoutIn, user: User = Depends(verified_user),
                           db: AsyncSession = Depends(get_db),
                           billing: BillingService = Depends(get_billing)):
    valid = {"free", "starter", "pro", "studio", "pay_per_video"}
    if body.plan_id not in valid:
        raise HTTPException(400, detail={"error": "INVALID_PARAMETER", "message": f"Invalid plan: {body.plan_id}"})
    try:
        cid = billing.get_or_create_customer(user.id, user.email, user.full_name, user.stripe_customer_id)
        if not user.stripe_customer_id:
            user.stripe_customer_id = cid
            await db.commit()
        result = billing.create_checkout(user.id, cid, body.plan_id, body.quantity,
                                          body.success_url, body.cancel_url)
        return CheckoutOut(checkout_url=result["checkout_url"], session_id=result["session_id"])
    except Exception as e:
        log.error(f"Checkout error: {e}", exc_info=True)
        raise HTTPException(502, detail={"error": "STRIPE_ERROR", "message": "Payment provider error"})


@router.post("/portal", response_model=PortalOut)
async def billing_portal(body: PortalIn, user: User = Depends(current_user),
                          billing: BillingService = Depends(get_billing)):
    if not user.stripe_customer_id:
        raise HTTPException(404, detail={"error": "NOT_FOUND", "message": "No billing account found"})
    try:
        url = billing.create_portal(user.stripe_customer_id,
                                     body.return_url or f"{settings.FRONTEND_URL}/dashboard")
        return PortalOut(portal_url=url)
    except Exception as e:
        raise HTTPException(502, detail={"error": "STRIPE_ERROR", "message": "Cannot open billing portal"})


@router.post("/webhook")
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db),
                          billing: BillingService = Depends(get_billing)):
    payload = await request.body()
    sig = request.headers.get("Stripe-Signature", "")
    try:
        event = billing.verify_webhook(payload, sig)
    except Exception as e:
        log.warning(f"Webhook rejected: {e}")
        raise HTTPException(400, detail={"error": "WEBHOOK_ERROR", "message": str(e)})

    etype = event["type"]
    data = event["data"]["object"]
    log.info(f"Webhook: {etype}")

    try:
        if etype == "checkout.session.completed":
            await _apply_checkout(billing.handle_checkout_complete(data), db)
        elif etype == "invoice.paid":
            await _apply_invoice_paid(billing.handle_invoice_paid(data), db)
        elif etype == "invoice.payment_failed":
            await _apply_past_due(billing.handle_invoice_failed(data), db)
        elif etype == "customer.subscription.updated":
            await _apply_sub_updated(billing.handle_sub_updated(data), db)
        elif etype == "customer.subscription.deleted":
            await _apply_sub_deleted(billing.handle_sub_deleted(data), db)
        elif etype == "charge.dispute.created":
            await _apply_dispute(billing.handle_dispute(data), db)
    except Exception as e:
        log.error(f"Webhook handler error for {etype}: {e}", exc_info=True)

    return {"received": True}


async def _apply_checkout(result: dict, db: AsyncSession):
    if not result or not result.get("user_id"):
        return
    user = await db.get(User, result["user_id"])
    if not user:
        return
    action = result.get("action")
    if action == "add_credits":
        qty = result.get("quantity", 1)
        user.credits = (user.credits or 0) + qty
        user.plan = Plan.pay_per_video
        user.total_spent_cents = (user.total_spent_cents or 0) + result.get("amount_cents", 0)
        db.add(Payment(user_id=user.id, stripe_pi=result.get("stripe_pi"),
                        stripe_session=result.get("stripe_session"),
                        amount_cents=result.get("amount_cents", 0),
                        status=PaymentStatus.paid, payment_type="credits",
                        description=f"{qty} render credit(s)"))
    elif action == "activate_sub":
        plan_map = {"starter": Plan.starter, "pro": Plan.pro, "studio": Plan.studio}
        user.plan = plan_map.get(result.get("plan", "starter"), Plan.starter)
        user.stripe_subscription_id = result.get("stripe_sub")
        user.sub_status = "active"
        user.renders_this_month = 0
    await db.commit()


async def _apply_invoice_paid(result: dict, db: AsyncSession):
    if not result or result.get("action") != "renew_sub":
        return
    r = await db.execute(select(User).where(User.stripe_customer_id == result.get("stripe_customer")))
    user = r.scalar_one_or_none()
    if not user:
        return
    user.sub_status = "active"
    user.sub_period_end = result.get("period_end")
    user.renders_this_month = 0
    user.total_spent_cents = (user.total_spent_cents or 0) + result.get("amount_cents", 0)
    db.add(Payment(user_id=user.id, stripe_invoice=result.get("stripe_invoice"),
                    stripe_charge=result.get("stripe_charge"),
                    amount_cents=result.get("amount_cents", 0),
                    status=PaymentStatus.paid, payment_type="subscription_renewal",
                    description=f"Renewal - {user.plan.value if user.plan else 'unknown'}"))
    await db.commit()


async def _apply_past_due(result: dict, db: AsyncSession):
    if not result:
        return
    r = await db.execute(select(User).where(User.stripe_customer_id == result.get("stripe_customer")))
    user = r.scalar_one_or_none()
    if user:
        user.sub_status = "past_due"
        await db.commit()


async def _apply_sub_updated(result: dict, db: AsyncSession):
    if not result or result.get("action") != "update_sub":
        return
    r = await db.execute(select(User).where(User.stripe_subscription_id == result.get("stripe_sub")))
    user = r.scalar_one_or_none()
    if not user:
        return
    user.sub_status = result.get("status")
    if result.get("period_end"):
        user.sub_period_end = result["period_end"]
    plan_map = {"starter": Plan.starter, "pro": Plan.pro, "studio": Plan.studio}
    if result.get("plan") in plan_map:
        user.plan = plan_map[result["plan"]]
    await db.commit()


async def _apply_sub_deleted(result: dict, db: AsyncSession):
    if not result:
        return
    r = await db.execute(select(User).where(User.stripe_subscription_id == result.get("stripe_sub")))
    user = r.scalar_one_or_none()
    if user:
        user.plan = Plan.free
        user.sub_status = "cancelled"
        user.stripe_subscription_id = None
        await db.commit()


async def _apply_dispute(result: dict, db: AsyncSession):
    if not result:
        return
    r = await db.execute(select(Payment).where(Payment.stripe_charge == result.get("stripe_charge")))
    pmt = r.scalar_one_or_none()
    if pmt:
        pmt.status = PaymentStatus.disputed
        await db.commit()


@router.get("/history", response_model=PaymentListOut)
async def payment_history(user: User = Depends(current_user), db: AsyncSession = Depends(get_db),
                           pg: dict = Depends(pagination)):
    total = (await db.execute(select(func.count(Payment.id)).where(Payment.user_id == user.id))).scalar_one()
    pmts = (await db.execute(select(Payment).where(Payment.user_id == user.id)
                              .order_by(Payment.created_at.desc())
                              .offset(pg["offset"]).limit(pg["per_page"]))).scalars().all()
    return PaymentListOut(
        payments=[PaymentOut(**p.to_dict()) for p in pmts],
        total=total, page=pg["page"], per_page=pg["per_page"],
        pages=math.ceil(total / pg["per_page"]) if pg["per_page"] else 0,
    )