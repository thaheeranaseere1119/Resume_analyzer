"""Celery application. Tasks run in a worker; with CELERY_EAGER=true they run in-process."""
from __future__ import annotations

from celery import Celery

from ..core.config import get_settings

_s = get_settings()
celery = Celery("ats", broker=_s.redis_url, backend=_s.redis_url, include=["app.tasks.jobs"])
celery.conf.update(
    task_always_eager=_s.celery_eager,
    task_eager_propagates=True,
    task_acks_late=True,
    task_time_limit=300,
    task_soft_time_limit=240,
    worker_prefetch_multiplier=1,
    result_expires=3600,
    task_default_retry_delay=5,
    broker_connection_retry_on_startup=True,
)
