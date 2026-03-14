import math
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from rift.core.database import get_db
from rift.models import User, Job, Payment, Upload, JobStatus, PaymentStatus
from rift.core.schemas import AdminUserOut, AdminMetricsOut, Msg
from rift.api.deps import admin_user, pagination

router = APIRouter(prefix="/admin", tags=["Admin"])


@router.get("/users", response_model=dict)
async def list_users(
    user: User = Depends(admin_user),
    db: AsyncSession = Depends(get_db),
    pg: dict = Depends(pagination),
    search: Optional[str] = None,
    plan: Optional[str] = None,
):
    q = select(User)
    if search:
        q = q.where(User.email.ilike(f"%{search}%"))
    if plan:
        from rift.models import Plan
        try:
            q = q.where(User.plan == Plan(plan))
        except ValueError:
            pass
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    users = (await db.execute(q.order_by(User.created_at.desc())
                               .offset(pg["offset"]).limit(pg["per_page"]))).scalars().all()
    return {
        "users": [u.to_dict() for u in users],
        "total": total, "page": pg["page"], "per_page": pg["per_page"],
        "pages": math.ceil(total / pg["per_page"]) if pg["per_page"] else 0,
    }


@router.get("/users/{user_id}", response_model=dict)
async def get_user(user_id: str, admin: User = Depends(admin_user),
                   db: AsyncSession = Depends(get_db)):
    u = await db.get(User, user_id)
    if not u:
        raise HTTPException(404, detail={"error": "NOT_FOUND", "message": "User not found"})
    jobs = (await db.execute(select(Job).where(Job.user_id == user_id)
                              .order_by(Job.created_at.desc()).limit(20))).scalars().all()
    pmts = (await db.execute(select(Payment).where(Payment.user_id == user_id)
                              .order_by(Payment.created_at.desc()).limit(10))).scalars().all()
    return {
        "user": u.to_dict(),
        "recent_jobs": [j.to_dict() for j in jobs],
        "recent_payments": [p.to_dict() for p in pmts],
    }


@router.patch("/users/{user_id}", response_model=Msg)
async def update_user(user_id: str, body: dict, admin: User = Depends(admin_user),
                       db: AsyncSession = Depends(get_db)):
    u = await db.get(User, user_id)
    if not u:
        raise HTTPException(404, detail={"error": "NOT_FOUND", "message": "User not found"})
    allowed = {"is_active", "is_verified", "is_admin", "credits", "renders_this_month"}
    for k, v in body.items():
        if k in allowed:
            setattr(u, k, v)
    if "plan" in body:
        from rift.models import Plan
        try:
            u.plan = Plan(body["plan"])
        except ValueError:
            raise HTTPException(400, detail={"error": "INVALID_PARAMETER", "message": f"Invalid plan: {body['plan']}"})
    await db.commit()
    return Msg(message="User updated")


@router.delete("/users/{user_id}", response_model=Msg)
async def deactivate_user(user_id: str, admin: User = Depends(admin_user),
                           db: AsyncSession = Depends(get_db)):
    if user_id == admin.id:
        raise HTTPException(400, detail={"error": "INVALID_OPERATION", "message": "Cannot deactivate yourself"})
    u = await db.get(User, user_id)
    if not u:
        raise HTTPException(404, detail={"error": "NOT_FOUND", "message": "User not found"})
    u.is_active = False
    await db.commit()
    return Msg(message="User deactivated")


@router.get("/metrics", response_model=dict)
async def get_metrics(admin: User = Depends(admin_user), db: AsyncSession = Depends(get_db)):
    import torch, psutil

    total_users = (await db.execute(select(func.count(User.id)))).scalar_one()
    verified_users = (await db.execute(select(func.count(User.id)).where(User.is_verified == True))).scalar_one()

    from rift.models import Plan
    users_by_plan = {}
    for plan in Plan:
        cnt = (await db.execute(select(func.count(User.id)).where(User.plan == plan))).scalar_one()
        users_by_plan[plan.value] = cnt

    total_jobs = (await db.execute(select(func.count(Job.id)))).scalar_one()
    completed_jobs = (await db.execute(select(func.count(Job.id)).where(Job.status == JobStatus.complete))).scalar_one()
    failed_jobs = (await db.execute(select(func.count(Job.id)).where(Job.status == JobStatus.failed))).scalar_one()
    active_jobs = (await db.execute(select(func.count(Job.id)).where(Job.status.in_([JobStatus.queued, JobStatus.processing])))).scalar_one()

    revenue = (await db.execute(
        select(func.sum(Payment.amount_cents)).where(Payment.status == PaymentStatus.paid)
    )).scalar_one() or 0

    success_rate = round((completed_jobs / total_jobs * 100) if total_jobs > 0 else 0, 1)

    gpu_info = {"available": False, "name": "CPU only"}
    if torch.cuda.is_available():
        props = torch.cuda.get_device_properties(0)
        gpu_info = {
            "available": True, "name": props.name,
            "memory_gb": round(props.total_memory / 1e9, 2),
            "allocated_mb": round(torch.cuda.memory_allocated(0) / 1e6, 1),
        }

    ram = psutil.virtual_memory()
    try:
        from rift.core.config import settings
        disk = psutil.disk_usage(str(settings.STORAGE_ROOT))
        storage_info = {"total_gb": round(disk.total / 1e9, 2), "used_gb": round(disk.used / 1e9, 2),
                        "free_gb": round(disk.free / 1e9, 2), "percent": disk.percent}
    except Exception:
        storage_info = {}

    return {
        "users": {"total": total_users, "verified": verified_users, "by_plan": users_by_plan},
        "jobs": {"total": total_jobs, "completed": completed_jobs, "failed": failed_jobs,
                 "active": active_jobs, "success_rate": success_rate},
        "revenue": {"total_cents": revenue, "total_dollars": round(revenue / 100, 2)},
        "system": {
            "gpu": gpu_info,
            "cpu_percent": psutil.cpu_percent(interval=0.1),
            "ram": {"total_gb": round(ram.total / 1e9, 2), "used_percent": ram.percent},
            "storage": storage_info,
        },
    }


@router.get("/jobs", response_model=dict)
async def list_all_jobs(admin: User = Depends(admin_user), db: AsyncSession = Depends(get_db),
                         pg: dict = Depends(pagination), status: Optional[str] = None):
    q = select(Job)
    if status and status in [s.value for s in JobStatus]:
        q = q.where(Job.status == JobStatus(status))
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    jobs = (await db.execute(q.order_by(Job.created_at.desc())
                              .offset(pg["offset"]).limit(pg["per_page"]))).scalars().all()
    return {
        "jobs": [j.to_dict() for j in jobs],
        "total": total, "page": pg["page"], "per_page": pg["per_page"],
        "pages": math.ceil(total / pg["per_page"]) if pg["per_page"] else 0,
    }