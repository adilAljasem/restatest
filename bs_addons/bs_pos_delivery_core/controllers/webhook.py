import logging

from odoo import http
from odoo.service.model import PG_CONCURRENCY_EXCEPTIONS_TO_RETRY
from odoo.http import request

from ..exceptions import (
    DeliveryAuthenticationError,
    DeliveryIntegrationError,
    DeliveryUnsupportedOperation,
    DeliveryWebhookConflictError,
    DeliveryWebhookNotFoundError,
    DeliveryWebhookValidationError,
)


_logger = logging.getLogger(__name__)

MAX_WEBHOOK_BODY_BYTES = 1024 * 1024


class DeliveryWebhookController(http.Controller):
    @http.route(
        "/bs_delivery/webhook/<string:webhook_token>",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def receive_webhook(self, webhook_token):
        raw_body = request.httprequest.get_data(cache=True)
        if len(raw_body) > MAX_WEBHOOK_BODY_BYTES:
            return self._response("payload_too_large", 413)

        headers = dict(request.httprequest.headers.items())
        try:
            event, created = request.env["bs.delivery.webhook.event"].sudo().ingest(
                webhook_token=webhook_token,
                raw_body=raw_body,
                headers=headers,
            )
        except DeliveryWebhookNotFoundError:
            return self._response("not_found", 404)
        except DeliveryAuthenticationError:
            return self._response("unauthorized", 401)
        except DeliveryWebhookValidationError:
            return self._response("invalid_payload", 400)
        except DeliveryWebhookConflictError:
            return self._response("idempotency_conflict", 409)
        except DeliveryUnsupportedOperation:
            return self._response("adapter_unavailable", 503)
        except DeliveryIntegrationError as error:
            status = 503 if error.retryable else 422
            response_status = "temporarily_unavailable" if error.retryable else "rejected"
            return self._response(response_status, status)
        except PG_CONCURRENCY_EXCEPTIONS_TO_RETRY:
            # Let Odoo roll back and replay the request instead of
            # answering 500 and losing the provider's update.
            raise
        except Exception:
            _logger.exception("Unexpected delivery webhook ingestion failure")
            return self._response("temporarily_unavailable", 503)

        return request.make_json_response(
            {
                "status": "queued" if created else "duplicate",
                "event": event.event_id or event.idempotency_key,
            },
            status=202 if created else 200,
        )

    @staticmethod
    def _response(status, http_status):
        return request.make_json_response({"status": status}, status=http_status)
