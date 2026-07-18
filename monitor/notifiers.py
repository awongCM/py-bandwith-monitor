"""Notification adapters for fired alerts."""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

import httpx

from monitor.models import AlertEvent

logger = logging.getLogger(__name__)


@runtime_checkable
class Notifier(Protocol):
    """Send a fired alert to an external channel."""

    def notify(self, alert: AlertEvent) -> None:
        """Deliver the alert. Implementations should not raise to callers."""


class WebhookNotifier:
    """POST alert payloads as JSON to a generic webhook URL."""

    def __init__(
        self,
        url: str,
        *,
        client: httpx.Client | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.url = url
        self._client = client
        self._timeout = timeout
        self._owns_client = client is None

    def notify(self, alert: AlertEvent) -> None:
        payload = self.build_payload(alert)
        client = self._client or httpx.Client(timeout=self._timeout)
        try:
            response = client.post(self.url, json=payload)
            response.raise_for_status()
        except Exception:
            logger.warning(
                "Webhook notification failed for rule %s",
                alert.rule_id,
                exc_info=True,
            )
        finally:
            if self._owns_client:
                client.close()

    @staticmethod
    def build_payload(alert: AlertEvent) -> dict[str, Any]:
        body = alert.to_dict()
        return {
            "text": alert.message,
            "alert": body,
        }


class EmailNotifier:
    """Stub email notifier reserved for a future SMTP adapter."""

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    def notify(self, alert: AlertEvent) -> None:
        raise NotImplementedError("Email notifications are not implemented yet.")


class DesktopNotifier:
    """Stub desktop notifier reserved for a future OS notification hook."""

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    def notify(self, alert: AlertEvent) -> None:
        raise NotImplementedError("Desktop notifications are not implemented yet.")


def build_notifiers(webhook_url: str | None) -> list[Notifier]:
    if not webhook_url:
        return []
    return [WebhookNotifier(webhook_url)]
