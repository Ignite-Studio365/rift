from celery import Celery
from rift.core.config import settings

celery_app = Celery("rift")

celery_app.conf.update(
    broker_url=settings.CELERY_BROKER,
    result_backend=settings.CELERY_BACKEND,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_soft_time_limit=settings.RENDER_TIMEOUT - 600,
    task_time_limit=settings.RENDER_TIMEOUT + 300,
    task_routes={
        "rift.worker.tasks.render": {"queue": "render"},
        "rift.worker.tasks.preview": {"queue": "preview"},
    },
    beat_schedule={
        "cleanup-outputs": {"task": "rift.worker.tasks.cleanup", "schedule": 3600.0},
        "reset-quotas":    {"task": "rift.worker.tasks.reset_quotas", "schedule": 86400.0},
    },
)

celery_app.autodiscover_tasks(["rift.worker"])