"""Record failed, authenticated inbound HTTP attempts without changing their response."""

import functools
import json
import logging
import time

from odoo.http import request

_logger = logging.getLogger(__name__)


def audit_failed_http_attempt(operation, resolve_connection):
    """Audit a rejected request when its authenticated connection is known."""

    def decorate(endpoint):
        @functools.wraps(endpoint)
        def wrapped(*args, **kwargs):
            started = time.monotonic()
            response = endpoint(*args, **kwargs)
            if response.status_code < 400:
                return response
            try:
                connection = resolve_connection()
                if not connection:
                    return response
                connection.ensure_one()
                body = request.httprequest.get_json(silent=True)
                order = body.get("order") if isinstance(body, dict) else None
                values = order if isinstance(order, dict) else body if isinstance(body, dict) else {}
                external_id = next((str(values[key]) for key in (
                    "provider_order_id", "feedus_order_id", "aggregator_order_id",
                    "order_id", "_id",
                ) if values.get(key)), None)
                if not external_id and kwargs.get("order_id"):
                    external_id = str(kwargs["order_id"])
                response_body = response.get_data(as_text=True)
                try:
                    result = json.loads(response_body)
                except (TypeError, ValueError):
                    result = {}
                message = next((str(result[key]) for key in ("error", "message")
                                if isinstance(result, dict) and result.get(key)),
                               "HTTP %s" % response.status_code)
                with request.env.cr.savepoint():
                    log = request.env["bs.delivery.sync.log"].start_attempt(
                        connection,
                        direction="incoming",
                        operation=operation,
                        external_id=external_id,
                        request_url=request.httprequest.path,
                        request_method=request.httprequest.method,
                        request_payload=request.httprequest.get_data(cache=True),
                    )
                    log.mark_failed(
                        message,
                        response_payload=response_body,
                        http_status=response.status_code,
                        duration_ms=(time.monotonic() - started) * 1000,
                    )
            except Exception:
                _logger.exception("Could not audit rejected %s request", operation)
            return response

        return wrapped

    return decorate
