from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, EmailStr, Field, field_validator


class Cfg(BaseModel):
    model_config = {"from_attributes": True}


class Msg(Cfg):
    message: str
    success: bool = True


class ErrResp(Cfg):
    error: str
    message: str
    detail: Any = None


class RegisterIn(Cfg):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: Optional[str] = Field(default=None, max_length=255)

    @field_validator("password")
    @classmethod
    def pw_strength(cls, v):
        if not any(c.isupper() for c in v):
            raise ValueError("Password needs at least one uppercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password needs at least one digit")
        return v


class LoginIn(Cfg):
    email: EmailStr
    password: str


class RefreshIn(Cfg):
    refresh_token: str


class TokenOut(Cfg):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class AccessTokenOut(Cfg):
    access_token: str
    token_type: str = "bearer"


class VerifyIn(Cfg):
    token: str


class ForgotIn(Cfg):
    email: EmailStr


class ResetIn(Cfg):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


class ChangePwIn(Cfg):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


class CreateKeyIn(Cfg):
    name: str = Field(min_length=1, max_length=100)


class UserOut(Cfg):
    id: str
    email: str
    full_name: Optional[str]
    plan: str
    is_verified: bool
    is_active: bool
    is_admin: bool
    credits: int
    renders_this_month: int
    total_renders: int
    storage_bytes: int
    sub_status: Optional[str]
    sub_period_end: Optional[datetime]
    total_spent_cents: int
    created_at: Optional[datetime]


class KeyOut(Cfg):
    id: str
    prefix: str
    name: str
    is_active: bool
    last_used_at: Optional[datetime]
    created_at: Optional[datetime]


class KeyCreatedOut(Cfg):
    key: str
    api_key: KeyOut
    message: str = "Store this key securely — it will not be shown again."


class UploadOut(Cfg):
    id: str
    filename: str
    size_bytes: int
    width: Optional[int]
    height: Optional[int]
    fps: Optional[float]
    frame_count: Optional[int]
    duration: Optional[float]
    codec: Optional[str]
    has_audio: bool
    created_at: Optional[datetime]
    expires_at: Optional[datetime]


class AudioUploadOut(Cfg):
    success: bool = True
    path: str
    filename: str
    size_bytes: int


class RenderParams(Cfg):
    extract_fps: int = Field(10, ge=1, le=60)
    output_fps: int = Field(30, ge=1, le=60)
    target_resolution: str = "Original"
    upscale_model: str = "RealESRGAN_x4plus"
    output_format: str = "mp4"
    ink_technique: str = "graphic_pen"
    ink_color: str = "#000000"
    paper_color: str = "#FFFFFF"
    ink_density: float = Field(0.8, ge=0.0, le=2.0)
    ink_contrast: float = Field(1.3, ge=0.5, le=3.0)
    ink_length: int = Field(12, ge=1, le=100)
    ink_angle: float = Field(45.0, ge=0.0, le=360.0)
    ink_angle1: float = Field(45.0, ge=0.0, le=360.0)
    ink_angle2: float = Field(135.0, ge=0.0, le=360.0)
    stroke_spacing: float = Field(4.0, ge=1.0, le=30.0)
    dot_size_min: int = Field(1, ge=1, le=10)
    dot_size_max: int = Field(3, ge=1, le=15)
    levels: int = Field(6, ge=2, le=32)
    edge_strength: float = Field(0.7, ge=0.0, le=3.0)
    halftone_pattern: str = "dot"
    halftone_frequency: float = Field(10.0, ge=2.0, le=100.0)
    halftone_angle: float = Field(45.0, ge=0.0, le=180.0)
    dither_type: str = "floyd"
    dither_intensity: float = Field(0.8, ge=0.0, le=1.0)
    overlay_type: str = "none"
    overlay_intensity: float = Field(0.5, ge=0.0, le=1.0)
    overlay_grain_size: float = Field(50.0, ge=5.0, le=300.0)
    overlay_color_temp: float = Field(0.0, ge=-1.0, le=1.0)
    overlay_line_spacing: int = Field(4, ge=1, le=50)
    overlay_line_thickness: int = Field(1, ge=1, le=10)
    overlay_bloom: float = Field(0.2, ge=0.0, le=1.0)
    overlay_spacing: int = Field(10, ge=2, le=60)
    overlay_angle1: float = Field(45.0, ge=0.0, le=180.0)
    overlay_angle2: float = Field(135.0, ge=0.0, le=180.0)
    overlay_distortion: float = Field(0.1, ge=0.0, le=1.0)
    overlay_aberration: float = Field(0.05, ge=0.0, le=0.5)
    overlay_refraction: float = Field(1.5, ge=1.0, le=3.0)
    overlay_frost: float = Field(0.2, ge=0.0, le=1.0)
    overlay_wavelength: float = Field(20.0, ge=5.0, le=100.0)
    overlay_amplitude: float = Field(10.0, ge=0.0, le=50.0)
    overlay_turbulence: float = Field(0.2, ge=0.0, le=1.0)
    overlay_drop_ripples: float = Field(0.0, ge=0.0, le=1.0)
    overlay_bokeh_size: float = Field(3.0, ge=0.5, le=15.0)
    overlay_bokeh_shape: str = "circular"
    overlay_highlight_boost: float = Field(0.3, ge=0.0, le=1.0)
    overlay_texture_type: str = "paper"
    overlay_scale: float = Field(1.0, ge=0.1, le=5.0)
    overlay_texture_contrast: float = Field(1.0, ge=0.1, le=5.0)
    include_audio: bool = True
    normalize_audio: bool = False
    custom_audio_path: Optional[str] = None
    apply_trim: bool = False
    trim_start: float = Field(0.0, ge=0.0)
    trim_end: float = Field(0.0, ge=0.0)
    apply_crop: bool = False
    crop_x: int = Field(0, ge=0)
    crop_y: int = Field(0, ge=0)
    crop_w: int = Field(0, ge=0)
    crop_h: int = Field(0, ge=0)

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class CreateJobIn(Cfg):
    upload_id: str
    params: RenderParams = Field(default_factory=RenderParams)


class PreviewIn(Cfg):
    upload_id: str
    frame_index: int = Field(0, ge=0)
    params: RenderParams = Field(default_factory=RenderParams)


class ThumbnailIn(Cfg):
    upload_id: str
    time_seconds: float = Field(0.0, ge=0.0)
    max_width: int = Field(640, ge=32, le=1920)


class PreviewOut(Cfg):
    success: bool = True
    image: str
    width: int
    height: int
    frame_index: int


class ThumbnailOut(Cfg):
    success: bool = True
    image: str
    width: int
    height: int
    time_seconds: float


class JobOut(Cfg):
    id: str
    status: str
    progress: float
    message: Optional[str]
    error: Optional[str]
    frames_done: int
    frames_total: int
    render_fps: float
    out_filename: Optional[str]
    out_size_bytes: Optional[int]
    out_width: Optional[int]
    out_height: Optional[int]
    out_fps: Optional[float]
    out_format: Optional[str]
    out_expires_at: Optional[datetime]
    download_count: int
    cost_cents: int
    created_at: Optional[datetime]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    params: Dict[str, Any]


class CreateJobOut(Cfg):
    success: bool = True
    job_id: str
    celery_id: Optional[str] = None
    status: str = "queued"


class JobListOut(Cfg):
    jobs: List[JobOut]
    total: int
    page: int
    per_page: int
    pages: int


class CheckoutIn(Cfg):
    plan_id: str
    quantity: int = Field(1, ge=1, le=100)
    success_url: str = ""
    cancel_url: str = ""


class CheckoutOut(Cfg):
    checkout_url: str
    session_id: str


class PortalIn(Cfg):
    return_url: str = ""


class PortalOut(Cfg):
    portal_url: str


class BillingOut(Cfg):
    plan: str
    plan_name: str
    sub_status: Optional[str]
    sub_expires: Optional[datetime]
    credits: int
    renders_this_month: int
    monthly_limit: int
    renders_left: int
    can_render: bool
    quota_message: Optional[str]
    total_spent_dollars: float


class PlanOut(Cfg):
    id: str
    name: str
    price_cents: int
    price_display: str
    renders_per_month: Optional[int]
    features: List[str]


class PlansOut(Cfg):
    plans: List[PlanOut]


class PaymentOut(Cfg):
    id: str
    amount_cents: int
    currency: str
    status: str
    payment_type: str
    description: Optional[str]
    created_at: Optional[datetime]


class PaymentListOut(Cfg):
    payments: List[PaymentOut]
    total: int
    page: int
    per_page: int
    pages: int


class AdminUserOut(Cfg):
    id: str
    email: str
    full_name: Optional[str]
    plan: str
    is_verified: bool
    is_active: bool
    is_admin: bool
    credits: int
    renders_this_month: int
    total_renders: int
    total_spent_cents: int
    sub_status: Optional[str]
    created_at: Optional[datetime]


class AdminMetricsOut(Cfg):
    total_users: int
    verified_users: int
    users_by_plan: Dict[str, int]
    total_jobs: int
    completed_jobs: int
    failed_jobs: int
    success_rate: float
    revenue_cents: int
    revenue_dollars: float
    active_jobs: int
    gpu_info: Dict[str, Any]
    storage_info: Dict[str, Any]