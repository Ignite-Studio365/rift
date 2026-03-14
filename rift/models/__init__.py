import enum
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import (
    BigInteger, Boolean, DateTime, Enum, Float, ForeignKey,
    Index, Integer, JSON, String, Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from rift.core.database import Base


def uid() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


class Plan(str, enum.Enum):
    free = "free"
    starter = "starter"
    pro = "pro"
    studio = "studio"
    pay_per_video = "pay_per_video"


class JobStatus(str, enum.Enum):
    pending = "pending"
    queued = "queued"
    processing = "processing"
    complete = "complete"
    failed = "failed"
    cancelled = "cancelled"


class PaymentStatus(str, enum.Enum):
    pending = "pending"
    paid = "paid"
    failed = "failed"
    refunded = "refunded"
    disputed = "disputed"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[Optional[str]] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    verify_token: Mapped[Optional[str]] = mapped_column(String(255))
    reset_token: Mapped[Optional[str]] = mapped_column(String(255))
    reset_token_exp: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    plan: Mapped[Plan] = mapped_column(Enum(Plan, name="plan_enum"), default=Plan.free)
    stripe_customer_id: Mapped[Optional[str]] = mapped_column(String(255), unique=True, index=True)
    stripe_subscription_id: Mapped[Optional[str]] = mapped_column(String(255), unique=True)
    sub_status: Mapped[Optional[str]] = mapped_column(String(50))
    sub_period_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    credits: Mapped[int] = mapped_column(Integer, default=0)
    renders_this_month: Mapped[int] = mapped_column(Integer, default=0)
    quota_reset_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    storage_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    total_renders: Mapped[int] = mapped_column(Integer, default=0)
    total_spent_cents: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_login_ip: Mapped[Optional[str]] = mapped_column(String(45))

    jobs: Mapped[List["Job"]] = relationship("Job", back_populates="user", cascade="all, delete-orphan")
    uploads: Mapped[List["Upload"]] = relationship("Upload", back_populates="user", cascade="all, delete-orphan")
    payments: Mapped[List["Payment"]] = relationship("Payment", back_populates="user", cascade="all, delete-orphan")
    api_keys: Mapped[List["APIKey"]] = relationship("APIKey", back_populates="user", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_users_plan", "plan"),
        Index("ix_users_stripe", "stripe_customer_id"),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "email": self.email, "full_name": self.full_name,
            "plan": self.plan.value, "is_verified": self.is_verified,
            "is_active": self.is_active, "is_admin": self.is_admin,
            "credits": self.credits, "renders_this_month": self.renders_this_month,
            "total_renders": self.total_renders, "storage_bytes": self.storage_bytes,
            "sub_status": self.sub_status,
            "sub_period_end": self.sub_period_end.isoformat() if self.sub_period_end else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "total_spent_cents": self.total_spent_cents,
        }


class Upload(Base):
    __tablename__ = "uploads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(500))
    stored_name: Mapped[str] = mapped_column(String(500))
    storage_path: Mapped[str] = mapped_column(String(1000))
    storage_backend: Mapped[str] = mapped_column(String(20), default="local")
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    width: Mapped[Optional[int]] = mapped_column(Integer)
    height: Mapped[Optional[int]] = mapped_column(Integer)
    fps: Mapped[Optional[float]] = mapped_column(Float)
    frame_count: Mapped[Optional[int]] = mapped_column(Integer)
    duration: Mapped[Optional[float]] = mapped_column(Float)
    codec: Mapped[Optional[str]] = mapped_column(String(50))
    has_audio: Mapped[bool] = mapped_column(Boolean, default=False)
    audio_codec: Mapped[Optional[str]] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    user: Mapped["User"] = relationship("User", back_populates="uploads")
    jobs: Mapped[List["Job"]] = relationship("Job", back_populates="upload")

    __table_args__ = (Index("ix_uploads_user_active", "user_id", "is_active"),)

    def to_dict(self):
        return {
            "id": self.id, "filename": self.filename, "size_bytes": self.size_bytes,
            "width": self.width, "height": self.height, "fps": self.fps,
            "frame_count": self.frame_count, "duration": self.duration,
            "codec": self.codec, "has_audio": self.has_audio,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    upload_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("uploads.id", ondelete="SET NULL"))
    celery_id: Mapped[Optional[str]] = mapped_column(String(255), unique=True, index=True)

    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus, name="jobstatus_enum"), default=JobStatus.pending, index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[Optional[str]] = mapped_column(String(500))
    error: Mapped[Optional[str]] = mapped_column(Text)
    params: Mapped[Dict] = mapped_column(JSON, default=dict)

    out_path: Mapped[Optional[str]] = mapped_column(String(1000))
    out_filename: Mapped[Optional[str]] = mapped_column(String(500))
    out_backend: Mapped[Optional[str]] = mapped_column(String(20))
    out_size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    out_width: Mapped[Optional[int]] = mapped_column(Integer)
    out_height: Mapped[Optional[int]] = mapped_column(Integer)
    out_fps: Mapped[Optional[float]] = mapped_column(Float)
    out_format: Mapped[Optional[str]] = mapped_column(String(20))
    out_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    download_count: Mapped[int] = mapped_column(Integer, default=0)

    frames_done: Mapped[int] = mapped_column(Integer, default=0)
    frames_total: Mapped[int] = mapped_column(Integer, default=0)
    render_fps: Mapped[float] = mapped_column(Float, default=0.0)
    cost_cents: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    queued_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship("User", back_populates="jobs")
    upload: Mapped[Optional["Upload"]] = relationship("Upload", back_populates="jobs")

    __table_args__ = (
        Index("ix_jobs_user_status", "user_id", "status"),
        Index("ix_jobs_created", "created_at"),
        Index("ix_jobs_expires", "out_expires_at"),
    )

    def to_dict(self):
        return {
            "id": self.id, "status": self.status.value, "progress": round(self.progress, 2),
            "message": self.message, "error": self.error,
            "frames_done": self.frames_done, "frames_total": self.frames_total,
            "render_fps": round(self.render_fps, 2),
            "out_filename": self.out_filename, "out_size_bytes": self.out_size_bytes,
            "out_width": self.out_width, "out_height": self.out_height,
            "out_fps": self.out_fps, "out_format": self.out_format,
            "out_expires_at": self.out_expires_at.isoformat() if self.out_expires_at else None,
            "download_count": self.download_count, "cost_cents": self.cost_cents,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "params": self.params,
        }


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    stripe_pi: Mapped[Optional[str]] = mapped_column(String(255), unique=True, index=True)
    stripe_invoice: Mapped[Optional[str]] = mapped_column(String(255), unique=True)
    stripe_session: Mapped[Optional[str]] = mapped_column(String(255), unique=True)
    stripe_charge: Mapped[Optional[str]] = mapped_column(String(255), unique=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="usd")
    status: Mapped[PaymentStatus] = mapped_column(Enum(PaymentStatus, name="paymentstatus_enum"), default=PaymentStatus.pending)
    payment_type: Mapped[str] = mapped_column(String(50))
    description: Mapped[Optional[str]] = mapped_column(String(500))
    meta: Mapped[Optional[Dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

    user: Mapped["User"] = relationship("User", back_populates="payments")

    __table_args__ = (Index("ix_payments_user", "user_id", "status"),)

    def to_dict(self):
        return {
            "id": self.id, "amount_cents": self.amount_cents, "currency": self.currency,
            "status": self.status.value, "payment_type": self.payment_type,
            "description": self.description,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class APIKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    key_hash: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    prefix: Mapped[str] = mapped_column(String(12))
    name: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    user: Mapped["User"] = relationship("User", back_populates="api_keys")

    def to_dict(self):
        return {
            "id": self.id, "prefix": self.prefix, "name": self.name,
            "is_active": self.is_active,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }