import time

import requests

from odoo import _, models

from ..exceptions import DeliveryIntegrationError, DeliveryNetworkError, DeliveryTimeoutError


class DeliveryHttpClient(models.AbstractModel):
    """Shared observability and network-error mapping for provider HTTP calls."""

    _name = "bs.delivery.http.client"
    _description = "POS Delivery HTTP Client"

    def execute(
        self,
        connection,
        *,
        request_callable,
        operation,
        request_url,
        request_method,
        provider_label,
        binding=None,
        external_id=None,
        request_payload=None,
        attempt_number=1,
        correlation_id=None,
    ):
        connection.ensure_one()
        correlation_id = correlation_id or self.env.context.get("bs_delivery_correlation_id")
        log = self.env["bs.delivery.sync.log"].start_attempt(
            connection,
            direction="outgoing",
            operation=operation,
            binding=binding,
            external_id=external_id,
            request_url=request_url,
            request_method=request_method,
            request_payload=request_payload,
            attempt_number=attempt_number,
            correlation_id=correlation_id,
        )
        started_at = time.monotonic()
        try:
            response = request_callable()
        except requests.Timeout as error:
            mapped = DeliveryTimeoutError(
                _("%(provider)s request timed out.", provider=provider_label)
            )
            log.mark_failed(str(mapped), duration_ms=self.duration_ms(started_at))
            raise mapped from error
        except requests.RequestException as error:
            mapped = DeliveryNetworkError(
                _("%(provider)s request failed to connect.", provider=provider_label)
            )
            log.mark_failed(str(mapped), duration_ms=self.duration_ms(started_at))
            raise mapped from error
        except DeliveryIntegrationError as error:
            log.mark_failed(str(error), duration_ms=self.duration_ms(started_at))
            raise

        return response, self.response_payload(response), log, self.duration_ms(started_at)

    @staticmethod
    def response_payload(response):
        try:
            return response.json()
        except (TypeError, ValueError):
            return response.text or False

    @staticmethod
    def duration_ms(started_at):
        return (time.monotonic() - started_at) * 1000
