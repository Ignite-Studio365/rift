"""initial

Revision ID: 001
Revises:
Create Date: 2025-01-01 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), default=True, nullable=False),
        sa.Column("is_verified", sa.Boolean(), default=False, nullable=False),
        sa.Column("is_admin", sa.Boolean(), default=False, nullable=False),
        sa.Column("verify_token", sa.String(255), nullable=True),
        sa.Column("reset_token", sa.String(255), nullable=True),
        sa.Column("reset_token_exp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("plan", sa.Enum("free", "starter", "pro", "studio", "pay_per_video", name="plan_enum"), nullable=False, server_default="free"),
        sa.Column("stripe_customer_id", sa.String(255), nullable=True),
        sa.Column("stripe_subscription_id", sa.String(255), nullable=True),
        sa.Column("sub_status", sa.String(50), nullable=True),
        sa.Column("sub_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("credits", sa.Integer(), default=0, nullable=False),
        sa.Column("renders_this_month", sa.Integer(), default=0, nullable=False),
        sa.Column("quota_reset_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("storage_bytes", sa.BigInteger(), default=0, nullable=False),
        sa.Column("total_renders", sa.Integer(), default=0, nullable=False),
        sa.Column("total_spent_cents", sa.Integer(), default=0, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_ip", sa.String(45), nullable=True),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_plan", "users", ["plan"])
    op.create_index("ix_users_stripe", "users", ["stripe_customer_id"])

    op.create_table(
        "uploads",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("stored_name", sa.String(500), nullable=False),
        sa.Column("storage_path", sa.String(1000), nullable=False),
        sa.Column("storage_backend", sa.String(20), default="local", nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("fps", sa.Float(), nullable=True),
        sa.Column("frame_count", sa.Integer(), nullable=True),
        sa.Column("duration", sa.Float(), nullable=True),
        sa.Column("codec", sa.String(50), nullable=True),
        sa.Column("has_audio", sa.Boolean(), default=False, nullable=False),
        sa.Column("audio_codec", sa.String(50), nullable=True),
        sa.Column("is_active", sa.Boolean(), default=True, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_uploads_user_id", "uploads", ["user_id"])
    op.create_index("ix_uploads_user_active", "uploads", ["user_id", "is_active"])

    op.create_table(
        "payments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stripe_pi", sa.String(255), nullable=True),
        sa.Column("stripe_invoice", sa.String(255), nullable=True),
        sa.Column("stripe_session", sa.String(255), nullable=True),
        sa.Column("stripe_charge", sa.String(255), nullable=True),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), default="usd", nullable=False),
        sa.Column("status", sa.Enum("pending", "paid", "failed", "refunded", "disputed", name="paymentstatus_enum"), nullable=False, server_default="pending"),
        sa.Column("payment_type", sa.String(50), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_payments_user_id", "payments", ["user_id"])
    op.create_unique_constraint("uq_payments_stripe_pi", "payments", ["stripe_pi"])
    op.create_unique_constraint("uq_payments_stripe_invoice", "payments", ["stripe_invoice"])
    op.create_unique_constraint("uq_payments_stripe_session", "payments", ["stripe_session"])
    op.create_unique_constraint("uq_payments_stripe_charge", "payments", ["stripe_charge"])

    op.create_table(
        "jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("upload_id", sa.String(36), sa.ForeignKey("uploads.id", ondelete="SET NULL"), nullable=True),
        sa.Column("celery_id", sa.String(255), nullable=True),
        sa.Column("status", sa.Enum("pending", "queued", "processing", "complete", "failed", "cancelled", name="jobstatus_enum"), nullable=False, server_default="pending"),
        sa.Column("progress", sa.Float(), default=0.0, nullable=False),
        sa.Column("message", sa.String(500), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("out_path", sa.String(1000), nullable=True),
        sa.Column("out_filename", sa.String(500), nullable=True),
        sa.Column("out_backend", sa.String(20), nullable=True),
        sa.Column("out_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("out_width", sa.Integer(), nullable=True),
        sa.Column("out_height", sa.Integer(), nullable=True),
        sa.Column("out_fps", sa.Float(), nullable=True),
        sa.Column("out_format", sa.String(20), nullable=True),
        sa.Column("out_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("download_count", sa.Integer(), default=0, nullable=False),
        sa.Column("frames_done", sa.Integer(), default=0, nullable=False),
        sa.Column("frames_total", sa.Integer(), default=0, nullable=False),
        sa.Column("render_fps", sa.Float(), default=0.0, nullable=False),
        sa.Column("cost_cents", sa.Integer(), default=0, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_jobs_user_id", "jobs", ["user_id"])
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_user_status", "jobs", ["user_id", "status"])
    op.create_index("ix_jobs_created", "jobs", ["created_at"])
    op.create_index("ix_jobs_expires", "jobs", ["out_expires_at"])
    op.create_unique_constraint("uq_jobs_celery_id", "jobs", ["celery_id"])

    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key_hash", sa.String(255), nullable=False),
        sa.Column("prefix", sa.String(12), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("is_active", sa.Boolean(), default=True, nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"])
    op.create_unique_constraint("uq_api_keys_hash", "api_keys", ["key_hash"])


def downgrade() -> None:
    op.drop_table("api_keys")
    op.drop_table("jobs")
    op.drop_table("payments")
    op.drop_table("uploads")
    op.drop_table("users")
    op.execute("DROP TYPE IF EXISTS plan_enum")
    op.execute("DROP TYPE IF EXISTS jobstatus_enum")
    op.execute("DROP TYPE IF EXISTS paymentstatus_enum")