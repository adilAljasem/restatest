"""Send Delivery Core state changes to the TryOrder gateway."""

from urllib.parse import urlsplit

import requests

from odoo import _, models
from odoo.addons.bs_pos_delivery_core.exceptions import (
    DeliveryAuthenticationError, DeliveryBusinessError, DeliveryProviderServerError,
)


class TryOrderDeliveryAdapter(models.AbstractModel):
    _name = "bs.delivery.adapter.tryorder"
    _inherit = "bs.delivery.adapter"
    _description = "TryOrder Delivery Provider Adapter"

    def get_capabilities(self):
        return super().get_capabilities() | {
            "test_connection", "update_order", "cancel_order", "accept_order",
            "reject_order", "mark_order_preparing", "mark_order_ready",
        }

    def _settings(self, connection):
        connection.ensure_one()
        if not connection.active or connection.provider_id.code != "tryorder":
            raise DeliveryBusinessError(_("An active TryOrder connection is required."))
        base = (connection.base_url or "").strip().rstrip("/")
        try:
            parsed = urlsplit(base)
            port = parsed.port
        except ValueError as error:
            raise DeliveryBusinessError(
                _("Configure a valid HTTPS TryOrder gateway URL.")
            ) from error
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment or port == 0):
            raise DeliveryBusinessError(_("Configure a valid HTTPS TryOrder gateway URL."))
        secret = (connection.access_token or "").strip()
        if not secret:
            raise DeliveryAuthenticationError(_("Configure the TryOrder outbound webhook secret."))
        return base + "/webhook/odoo", secret

    def test_connection(self, connection):
        self._settings(connection)
        return {"message": _("TryOrder status gateway is configured.")}

    def _canonical_order(self, connection, binding):
        connection.ensure_one()
        binding.ensure_one()
        if binding.connection_id != connection:
            raise DeliveryBusinessError(_("The TryOrder order belongs to another connection."))
        order = self.env["bs.delivery.order"].sudo().with_company(connection.company_id).search([
            ("binding_id", "=", binding.id),
            ("connection_id", "=", connection.id),
            ("contract_version", "=", 1),
        ], limit=1)
        if not order:
            raise DeliveryBusinessError(_("The TryOrder Core order was not found."))
        return order

    def update_order(self, connection, binding, new_status, reason=None, **kwargs):
        order = self._canonical_order(connection, binding)
        if new_status not in {
            "received", "accepted", "preparing", "ready", "picked_up",
            "delivered", "rejected", "cancelled",
        }:
            raise DeliveryBusinessError(_("Unsupported TryOrder status."))
        url, secret = self._settings(connection)
        payload = {
            "type": "ORDER_UPDATE_WEBHOOK",
            "source": "pos.order" if order.pos_order_id else "bs.delivery.order",
            "data": {
                "order_id": str(order.pos_order_id.id or order.id),
                "provider_order_id": order.external_order_id,
                "order_status": new_status,
                "name": order.pos_order_id.name or order.external_order_id,
                "amount_total": order.amount_total,
                "customer": order.customer_name or None,
            },
        }
        if reason:
            payload["data"]["reason"] = str(reason)

        def send():
            return requests.post(
                url, json=payload,
                headers={"Content-Type": "application/json", "X-Webhook-Secret": secret},
                timeout=10,
            )

        response, response_payload, log, duration_ms = self.env[
            "bs.delivery.http.client"
        ].execute(
            connection, operation="tryorder_update_order_status",
            request_callable=send, request_url=url, request_method="POST",
            provider_label="TryOrder", request_payload=payload,
            binding=binding, external_id=order.external_order_id,
        )
        if not 200 <= response.status_code < 300:
            error = (
                DeliveryProviderServerError(_("TryOrder status API is temporarily unavailable."))
                if response.status_code == 429 or response.status_code >= 500
                else DeliveryAuthenticationError(_("TryOrder rejected the webhook secret."))
                if response.status_code in (401, 403)
                else DeliveryBusinessError(_("TryOrder rejected the status update."))
            )
            log.mark_failed(
                str(error), response_payload=response_payload,
                http_status=response.status_code, duration_ms=duration_ms,
            )
            raise error
        log.mark_success(
            response_payload=response_payload,
            http_status=response.status_code, duration_ms=duration_ms,
        )
        return {"success": True, "status": new_status, "http_status": response.status_code}

    def accept_order(self, connection, binding, **kwargs):
        return self.update_order(connection, binding, "accepted")

    def reject_order(self, connection, binding, reason=None, **kwargs):
        return self.update_order(connection, binding, "rejected", reason=reason)

    def cancel_order(self, connection, binding, reason=None, **kwargs):
        return self.update_order(connection, binding, "cancelled", reason=reason)

    def mark_order_preparing(self, connection, binding, **kwargs):
        return self.update_order(connection, binding, "preparing")

    def mark_order_ready(self, connection, binding, **kwargs):
        return self.update_order(connection, binding, "ready")
