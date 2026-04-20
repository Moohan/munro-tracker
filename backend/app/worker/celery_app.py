from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "munrostream",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)
celery_app.conf.task_default_queue = "munrostream"
celery_app.conf.task_track_started = True
celery_app.conf.imports = ("app.tasks.strava",)
celery_app.autodiscover_tasks(["app"])
