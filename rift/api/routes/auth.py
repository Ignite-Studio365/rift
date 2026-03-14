import hashlib
import secrets
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rift.core.database import get_db
from rift.core.config import settings
from rift.models import User, APIKey, Plan
from rift.core.schemas import (
    RegisterIn, LoginIn, RefreshIn, VerifyIn, ForgotIn, ResetIn,
    ChangePwIn, CreateKeyIn, TokenOut, AccessTokenOut, UserOut,
    KeyOut, KeyCreatedOut, Msg,
)
from rift.api.deps import current_user, verified_user
from rift.services.email import EmailService

router = APIRouter(prefix="/auth", tags=["Auth"])


def _hash(pw: str) -> str:
    from passlib.context import CryptContext
    return CryptContext(schemes=["bcrypt"], deprecated="auto").hash(pw)


def _verify(plain: str, hashed: str) -> bool:
    from passlib.context import CryptContext
    return CryptContext(schemes=["bcrypt"], deprecated="auto").verify(plain, hashed)


def _access(uid: str) -> str:
    from jose import jwt
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": uid, "type": "access", "iat": now,
                       "exp": now + timedelta(minutes=settings.JWT_ACCESS_EXPIRE_MINUTES)},
                      settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def _refresh(uid: str) -> str:
    from jose import jwt
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": uid, "type": "refresh", "iat": now,
                       "exp": now + timedelta(days=settings.JWT_REFRESH_EXPIRE_DAYS)},
                      settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def _decode(token: str) -> dict:
    from jose import jwt, JWTError
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except JWTError:
        raise HTTPException(401, detail={"error": "UNAUTHORIZED", "message": "Invalid token"})


@router.post("/register", response_model=Msg, status_code=201)
async def register(body: RegisterIn, db: AsyncSession = Depends(get_db)):
    email = str(body.email).lower().strip()
    existing = await db.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none():
        raise HTTPException(409, detail={"error": "CONFLICT", "message": "Email already registered"})
    token = secrets.token_urlsafe(32)
    user = User(email=email, password_hash=_hash(body.password), full_name=body.full_name,
                is_verified=settings.ENV == "development",
                verify_token=None if settings.ENV == "development" else token,
                plan=Plan.free)
    db.add(user)
    await db.commit()
    if settings.ENV != "development":
        await EmailService().send_verification(email, token)
    return Msg(message="Account created. Check your email to verify.")


@router.post("/verify-email", response_model=TokenOut)
async def verify_email(body: VerifyIn, db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(User).where(User.verify_token == body.token))
    user = r.scalar_one_or_none()
    if not user:
        raise HTTPException(400, detail={"error": "INVALID_TOKEN", "message": "Invalid verification token"})
    user.is_verified = True
    user.verify_token = None
    await db.commit()
    return TokenOut(access_token=_access(user.id), refresh_token=_refresh(user.id))


@router.post("/login", response_model=TokenOut)
async def login(body: LoginIn, request: Request, db: AsyncSession = Depends(get_db)):
    email = str(body.email).lower().strip()
    r = await db.execute(select(User).where(User.email == email))
    user = r.scalar_one_or_none()
    if not user or not _verify(body.password, user.password_hash):
        raise HTTPException(401, detail={"error": "UNAUTHORIZED", "message": "Invalid email or password"})
    if not user.is_active:
        raise HTTPException(403, detail={"error": "FORBIDDEN", "message": "Account deactivated"})
    user.last_login_at = datetime.now(timezone.utc)
    user.last_login_ip = request.client.host if request.client else None
    await db.commit()
    return TokenOut(access_token=_access(user.id), refresh_token=_refresh(user.id))


@router.post("/refresh", response_model=AccessTokenOut)
async def refresh(body: RefreshIn, db: AsyncSession = Depends(get_db)):
    p = _decode(body.refresh_token)
    if p.get("type") != "refresh":
        raise HTTPException(401, detail={"error": "UNAUTHORIZED", "message": "Not a refresh token"})
    user = await db.get(User, p.get("sub"))
    if not user or not user.is_active:
        raise HTTPException(401, detail={"error": "UNAUTHORIZED", "message": "User not found"})
    return AccessTokenOut(access_token=_access(user.id))


@router.post("/logout", response_model=Msg)
async def logout(user: User = Depends(current_user)):
    return Msg(message="Logged out")


@router.get("/me", response_model=UserOut)
async def get_me(user: User = Depends(current_user)):
    return UserOut(**user.to_dict())


@router.put("/me", response_model=UserOut)
async def update_me(body: dict, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    if "full_name" in body:
        user.full_name = str(body["full_name"]).strip()[:255] or None
    await db.commit()
    await db.refresh(user)
    return UserOut(**user.to_dict())


@router.post("/change-password", response_model=Msg)
async def change_password(body: ChangePwIn, user: User = Depends(current_user),
                           db: AsyncSession = Depends(get_db)):
    if not _verify(body.current_password, user.password_hash):
        raise HTTPException(401, detail={"error": "UNAUTHORIZED", "message": "Wrong current password"})
    user.password_hash = _hash(body.new_password)
    await db.commit()
    return Msg(message="Password changed")


@router.post("/forgot-password", response_model=Msg)
async def forgot_password(body: ForgotIn, db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(User).where(User.email == str(body.email).lower().strip()))
    user = r.scalar_one_or_none()
    if user:
        token = secrets.token_urlsafe(32)
        user.reset_token = token
        user.reset_token_exp = datetime.now(timezone.utc) + timedelta(hours=2)
        await db.commit()
        await EmailService().send_reset(user.email, token)
    return Msg(message="If that email is registered you will receive a reset link.")


@router.post("/reset-password", response_model=Msg)
async def reset_password(body: ResetIn, db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(User).where(User.reset_token == body.token))
    user = r.scalar_one_or_none()
    if not user:
        raise HTTPException(400, detail={"error": "INVALID_TOKEN", "message": "Invalid reset token"})
    if user.reset_token_exp and user.reset_token_exp < datetime.now(timezone.utc):
        raise HTTPException(400, detail={"error": "EXPIRED", "message": "Reset token expired"})
    user.password_hash = _hash(body.new_password)
    user.reset_token = None
    user.reset_token_exp = None
    await db.commit()
    return Msg(message="Password reset successfully")


@router.get("/api-keys", response_model=List[KeyOut])
async def list_keys(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(APIKey).where(APIKey.user_id == user.id, APIKey.is_active == True))
    return [KeyOut(**k.to_dict()) for k in r.scalars()]


@router.post("/api-keys", response_model=KeyCreatedOut, status_code=201)
async def create_key(body: CreateKeyIn, user: User = Depends(verified_user),
                     db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(APIKey).where(APIKey.user_id == user.id, APIKey.is_active == True))
    if len(r.scalars().all()) >= 5:
        raise HTTPException(400, detail={"error": "LIMIT_REACHED", "message": "Max 5 API keys per account"})
    raw = "rift_" + secrets.token_urlsafe(40)
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    prefix = raw[:12]
    k = APIKey(user_id=user.id, key_hash=key_hash, prefix=prefix, name=body.name)
    db.add(k)
    await db.commit()
    return KeyCreatedOut(key=raw, api_key=KeyOut(**k.to_dict()))


@router.delete("/api-keys/{key_id}", response_model=Msg)
async def delete_key(key_id: str, user: User = Depends(current_user),
                     db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(APIKey).where(APIKey.id == key_id, APIKey.user_id == user.id))
    k = r.scalar_one_or_none()
    if not k:
        raise HTTPException(404, detail={"error": "NOT_FOUND", "message": "API key not found"})
    k.is_active = False
    await db.commit()
    return Msg(message="API key revoked")