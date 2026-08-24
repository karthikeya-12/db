"""
One retry policy applied uniformly across every pipeline stage, plus the
dead-letter queue for items that exhaust their retry budget.
"""
from __future__ import annotations

import functools
from datetime import datetime, timezone

from sqlalchemy.orm import Session
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_random_exponential

from ingestion_pipeline.db.models import DeadLetterItem, IngestionItem


class RetryableError(Exception):
    """Raise for errors a retry might fix: timeouts, 5xx responses, rate limits."""


class NonRetryableError(Exception):
    """Raise for errors a retry can never fix: malformed input, validation failures."""


def with_retry(max_attempts: int, base_seconds: float):
    """Decorator factory -- retries only RetryableError with exponential backoff and jitter;
    NonRetryableError (and anything else) propagates immediately, no retry attempted."""

    def decorator(func):
        @retry(
            retry=retry_if_exception_type(RetryableError),
            wait=wait_random_exponential(multiplier=base_seconds, max=60),
            stop=stop_after_attempt(max_attempts),
            reraise=True,
        )
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        return wrapper

    return decorator


# Exceptions from these classes are treated as transient (worth retrying) when
# they surface from an external call wrapped by call_with_retry -- anything
# else propagates immediately as non-retryable, matching the task's
# "retryable errors like timeouts/5xx/rate-limits vs non-retryable like
# malformed input" split.
TRANSIENT_EXCEPTIONS = (TimeoutError, ConnectionError, OSError)


def call_with_retry(func, *args, max_attempts: int, base_seconds: float, **kwargs):
    """
    Call func(*args, **kwargs), retrying with exponential backoff + jitter only
    if it raises RetryableError or one of TRANSIENT_EXCEPTIONS (auto-translated
    to RetryableError). Any other exception propagates on the first attempt --
    no retry budget spent on errors a retry can't fix.
    """

    @with_retry(max_attempts, base_seconds)
    def _attempt():
        try:
            return func(*args, **kwargs)
        except NonRetryableError:
            raise
        except RetryableError:
            raise
        except TRANSIENT_EXCEPTIONS as exc:
            raise RetryableError(str(exc)) from exc

    return _attempt()


def write_dead_letter(
    session: Session, item_id: str, source_id: str, stage_failed: str, error_message: str, attempt_count: int
) -> None:
    """Record a terminally-failed item instead of dropping it or leaving it stuck in PROCESSING."""
    session.add(
        DeadLetterItem(
            item_id=item_id,
            source_id=source_id,
            stage_failed=stage_failed,
            error_message=error_message[:2000],
            attempt_count=attempt_count,
        )
    )
    item = session.get(IngestionItem, item_id)
    if item is not None:
        item.status = "FAILED"
        item.error_reason = error_message[:500]


def requeue_dead_letter(session: Session, dead_letter_id: str) -> None:
    """Mark a dead-lettered item requeued and reset it to NEW so the orchestrator picks it up again."""
    row = session.get(DeadLetterItem, dead_letter_id)
    if row is None:
        raise KeyError(f"No dead-letter row with id={dead_letter_id!r}")
    row.requeued_at = datetime.now(timezone.utc)
    item = session.get(IngestionItem, row.item_id)
    if item is not None:
        item.status = "NEW"
        item.error_reason = None
