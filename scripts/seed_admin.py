#!/usr/bin/env python3
"""
Creates the initial admin user.
Run: docker compose run --rm web python scripts/seed_admin.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main():
    from rift.core.database import session_factory, engine
    from rift.core.database import Base
    from rift.models import User, Plan

    # Ensure tables exist
    from rift.core.database import engine as get_engine
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    from passlib.context import CryptContext
    pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
    from sqlalchemy import select

    admin_email = os.getenv("ADMIN_EMAIL", "admin@rifteffect.com")
    admin_password = os.getenv("ADMIN_PASSWORD", "Admin1rift!")

    async with session_factory()() as session:
        r = await session.execute(select(User).where(User.email == admin_email))
        existing = r.scalar_one_or_none()
        if existing:
            print(f"Admin already exists: {admin_email}")
            return

        admin = User(
            email=admin_email,
            password_hash=pwd.hash(admin_password),
            full_name="System Administrator",
            is_active=True,
            is_verified=True,
            is_admin=True,
            plan=Plan.studio,
            credits=9999,
        )
        session.add(admin)
        await session.commit()

    print(f"✓ Admin created: {admin_email}")
    print(f"✓ Password:      {admin_password}")
    print(f"")
    print(f"  CHANGE THIS PASSWORD IMMEDIATELY IN PRODUCTION")
    print(f"  Dashboard: /admin")


if __name__ == "__main__":
    asyncio.run(main())