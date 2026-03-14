from typing import Any, Optional


class AppError(Exception):
    status_code: int = 500
    code: str = "INTERNAL_ERROR"
    message: str = "An unexpected error occurred"

    def __init__(self, message: Optional[str] = None, detail: Any = None):
        self.message = message or self.__class__.message
        self.detail = detail
        super().__init__(self.message)

    def to_dict(self):
        d = {"error": self.code, "message": self.message}
        if self.detail is not None:
            d["detail"] = self.detail
        return d


class NotFound(AppError):
    status_code = 404
    code = "NOT_FOUND"
    message = "Resource not found"


class Unauthorized(AppError):
    status_code = 401
    code = "UNAUTHORIZED"
    message = "Authentication required"


class Forbidden(AppError):
    status_code = 403
    code = "FORBIDDEN"
    message = "Access denied"


class Conflict(AppError):
    status_code = 409
    code = "CONFLICT"
    message = "Resource already exists"


class ValidationError(AppError):
    status_code = 422
    code = "VALIDATION_ERROR"
    message = "Validation failed"


class PaymentRequired(AppError):
    status_code = 402
    code = "PAYMENT_REQUIRED"
    message = "Payment required"


class QuotaExceeded(PaymentRequired):
    code = "QUOTA_EXCEEDED"
    message = "Render quota exceeded. Please upgrade your plan."


class StorageError(AppError):
    status_code = 500
    code = "STORAGE_ERROR"
    message = "Storage operation failed"


class VideoError(AppError):
    status_code = 400
    code = "VIDEO_ERROR"
    message = "Video processing failed"


class GPUError(AppError):
    status_code = 500
    code = "GPU_ERROR"
    message = "GPU processing error"


class StripeError(AppError):
    status_code = 502
    code = "STRIPE_ERROR"
    message = "Payment provider error"


class WebhookError(StripeError):
    code = "WEBHOOK_ERROR"
    message = "Webhook verification failed"


class JobError(AppError):
    status_code = 500
    code = "JOB_ERROR"
    message = "Job processing failed"


class Expired(AppError):
    status_code = 410
    code = "EXPIRED"
    message = "Resource has expired"