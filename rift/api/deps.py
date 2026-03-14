from typing import Optional
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from rift.core.database import get_db
from rift.core.config import settings
from rift.models import User, APIKey
from rift.services.billing import BillingService
from rift.services.storage import get_storage
from rift.services.audio import AudioService
from rift.services.video import VideoService
from rift.services.effects import Effects
from rift.services.overlays import Overlays
from rift.services.upscaler import UpscalerService
import hashlib
import logging

log = logging.getLogger("rift.deps")

_billing = None
_storage = None
_audio = None
_video = None
_effects = None
_overlays = None
_upscaler = None


def get_billing() -> BillingService:
    global _billing
    if _billing is None: _billing = BillingService()
    return _billing


def get_storage_svc():
    global _storage
    if _storage is None: _storage = get_storage()
    return _storage


def get_audio_svc() -> AudioService:
    global _audio
    if _audio is None: _audio = AudioService()
    return _audio


def get_video_svc() -> VideoService:
    global _video
    if _video is None: _video = VideoService()
    return _video


def get_effects_engine() -> Effects:
    global _effects
    if _effects is None: _effects = Effects()
    return _effects


def get_overlays_engine() -> Overlays:
    global _overlays
    if _overlays is None: _overlays = Overlays()
    return _overlays


def get_upscaler_svc() -> UpscalerService:
    global _upscaler
    if _upscaler is None: _upscaler = UpscalerService()
    return _upscaler


async def _user_from_token(token: str, db: AsyncSession) -> Optional[User]:
    from jose import jwt, JWTError
    try:
        p = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        if p.get("type") != "access":
            return None
        return await db.get(User, p.get("sub"))
    except JWTError:
        return None


async def _user_from_api_key(key: str, db: AsyncSession) -> Optional[User]:
    from datetime import datetime, timezone
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    r = await db.execute(select(APIKey).where(APIKey.key_hash == key_hash, APIKey.is_active == True))
    kobj = r.scalar_one_or_none()
    if not kobj:
        return None
    if kobj.last_used_at:
        from datetime import datetime, timezone
        kobj.last_used_at = datetime.now(timezone.utc)
        await db.commit()
    return await db.get(User, kobj.user_id)


async def current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    user = None
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        user = await _user_from_token(auth[7:], db)
    if user is None:
        api_key = request.headers.get("X-API-Key", "")
        if api_key:
            user = await _user_from_api_key(api_key, db)
    if user is None:
        raise HTTPException(401, detail={"error": "UNAUTHORIZED", "message": "Authentication required"},
                            headers={"WWW-Authenticate": "Bearer"})
    if not user.is_active:
        raise HTTPException(403, detail={"error": "FORBIDDEN", "message": "Account deactivated"})
    return user


async def verified_user(user: User = Depends(current_user)) -> User:
    if not user.is_verified:
        raise HTTPException(403, detail={"error": "FORBIDDEN", "message": "Email verification required"})
    return user


async def admin_user(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, detail={"error": "FORBIDDEN", "message": "Admin required"})
    return user


async def quota_user(user: User = Depends(verified_user),
                     billing: BillingService = Depends(get_billing)) -> User:
    can, msg = billing.check_quota(user)
    if not can:
        raise HTTPException(402, detail={"error": "QUOTA_EXCEEDED", "message": msg, "upgrade_required": True})
    return user


def pagination(page: int = 1, per_page: int = 20) -> dict:
    page = max(1, page)
    per_page = min(max(1, per_page), 100)
    return {"page": page, "per_page": per_page, "offset": (page - 1) * per_page}