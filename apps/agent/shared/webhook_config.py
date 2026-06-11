"""Webhook configuration for CRM event notifications."""

from dataclasses import dataclass


@dataclass
class WebhookConfig:
    """URL targets per webhook event type. Loaded from settings or tenant config."""

    lead_transition_url: str = ""

    @classmethod
    def from_settings(cls, settings) -> "WebhookConfig":
        return cls(
            lead_transition_url=settings.webhook_lead_url,
        )
