import logging
from typing import Optional
from rift.core.config import settings

log = logging.getLogger("rift.email")


class EmailService:
    def __init__(self):
        self.enabled = bool(settings.SENDGRID_API_KEY)
        if not self.enabled:
            log.warning("SendGrid not configured — emails disabled")

    async def send(self, to: str, subject: str, html: str) -> bool:
        if not self.enabled:
            log.info(f"[EMAIL SUPPRESSED] To:{to} Subject:{subject}")
            return True
        try:
            from sendgrid import SendGridAPIClient
            from sendgrid.helpers.mail import Mail
            msg = Mail(
                from_email=(settings.EMAIL_FROM, settings.EMAIL_FROM_NAME),
                to_emails=to,
                subject=subject,
                html_content=html,
            )
            sg = SendGridAPIClient(settings.SENDGRID_API_KEY)
            resp = sg.send(msg)
            return resp.status_code in (200, 202)
        except Exception as e:
            log.error(f"Email send failed: {e}")
            return False

    async def send_verification(self, email: str, token: str) -> bool:
        url = f"{settings.FRONTEND_URL}/verify-email?token={token}"
        html = f"""
        <div style="font-family:sans-serif;max-width:600px;margin:0 auto">
          <h1 style="color:#6366f1">RIFT EFFECT</h1>
          <h2>Verify your email address</h2>
          <p>Click the button below to verify your account.</p>
          <a href="{url}" style="background:#6366f1;color:white;padding:12px 24px;border-radius:6px;text-decoration:none;display:inline-block;margin:16px 0">Verify Email</a>
          <p style="color:#666;font-size:14px">Link expires in 24 hours. If you didn't create an account, ignore this email.</p>
        </div>"""
        return await self.send(email, "Verify your RIFT EFFECT account", html)

    async def send_reset(self, email: str, token: str) -> bool:
        url = f"{settings.FRONTEND_URL}/reset-password?token={token}"
        html = f"""
        <div style="font-family:sans-serif;max-width:600px;margin:0 auto">
          <h1 style="color:#6366f1">RIFT EFFECT</h1>
          <h2>Reset your password</h2>
          <p>Click below to set a new password. This link expires in 2 hours.</p>
          <a href="{url}" style="background:#6366f1;color:white;padding:12px 24px;border-radius:6px;text-decoration:none;display:inline-block;margin:16px 0">Reset Password</a>
          <p style="color:#666;font-size:14px">If you didn't request this, ignore this email.</p>
        </div>"""
        return await self.send(email, "Reset your RIFT EFFECT password", html)

    async def send_render_complete(self, email: str, job_id: str, filename: str) -> bool:
        url = f"{settings.FRONTEND_URL}/dashboard?job={job_id}"
        html = f"""
        <div style="font-family:sans-serif;max-width:600px;margin:0 auto">
          <h1 style="color:#6366f1">RIFT EFFECT</h1>
          <h2>Your render is ready ✓</h2>
          <p><strong>{filename}</strong> has finished rendering.</p>
          <a href="{url}" style="background:#6366f1;color:white;padding:12px 24px;border-radius:6px;text-decoration:none;display:inline-block;margin:16px 0">Download Now</a>
          <p style="color:#666;font-size:14px">Files are available for 7 days.</p>
        </div>"""
        return await self.send(email, "Your RIFT EFFECT render is ready", html)