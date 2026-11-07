import hashlib
import json
import uuid

import requests

from odoo import _, fields, models

from odoo.addons.bs_pos_delivery_core.exceptions import (
    DeliveryAuthenticationError,
    DeliveryBusinessError,
    DeliveryIntegrationError,
    DeliveryProviderServerError,
    DeliveryWebhookValidationError,
)

from ..services.exceptions import BlendAPIError
from ..services.menu import BlendMenuService


class BlendDeliveryAdapter(models.AbstractModel):
    _name = "bs.delivery.adapter.blend"
    _inherit = "bs.delivery.adapter"
    _description = "Blend Delivery Provider Adapter"

    def get_capabilities(self):
        return super().get_capabilities() | {
            "test_connection",
            "cancel_order",
            "update_order",
            "get_order_status",
            "pull_menu",
            "push_menu",
            "update_product_availability",
            "update_store_availability",
            "sync_products",
            "accept_order",
            "reject_order",
            "mark_order_preparing",
            "mark_order_ready",
            "verify_webhook_signature",
            "get_webhook_event_metadata",
            "process_webhook",
        }

    def test_connection(self, connection):
        connection.ensure_one()
        connection._blend_validate_configuration()
        stores = self.env["bs.delivery.store.binding"].sudo().search(
            [("connection_id", "=", connection.id), ("active", "=", True)]
        )
        if len(stores) != len(connection.pos_config_ids):
            raise DeliveryBusinessError(
                _("Every linked POS must have an active Blend store binding.")
            )
        return {
            "message": _("Blend configuration is valid for %s POS branch(es).")
            % len(stores)
        }

    def verify_webhook_signature(self, connection, *, raw_body, headers, **kwargs):
        self._ensure_blend_connection(connection)
        try:
            payload = json.loads(bytes(raw_body or b"{}").decode("utf-8"))
        except (UnicodeDecodeError, TypeError, ValueError) as error:
            raise DeliveryWebhookValidationError(
                _("The Blend webhook body must be valid JSON.")
            ) from error
        provided = self._bearer_token(headers)
        if not connection.webhook_secret and connection.auth_scheme != "basic":
            raise DeliveryAuthenticationError(
                _("Blend inbound webhook authentication is not configured.")
            )
        if not provided or not connection._blend_matches_inbound_token(provided):
            raise DeliveryAuthenticationError(_("Invalid Blend Bearer token."))
        self._single_store_for_payload(connection, payload, allow_many=True)
        return True

    def get_webhook_event_metadata(
        self, connection, *, payload, raw_body=None, headers=None, **kwargs
    ):
        self._ensure_blend_connection(connection)
        if not isinstance(payload, dict):
            raise DeliveryWebhookValidationError(_("The Blend webhook body must be an object."))
        event = str(payload.get("event") or "").strip()
        if not event:
            raise DeliveryWebhookValidationError(_("Blend event is required."))
        nested = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
        external_id = str(
            nested.get("order_id")
            or payload.get("order_id")
            or nested.get("item_id")
            or nested.get("modifier_id")
            or payload.get("product_id")
            or payload.get("branch_id")
            or ""
        ).strip()
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
        )
        return {
            "event_id": str(payload.get("event_id") or "").strip() or False,
            "event_type": event,
            "external_id": external_id or False,
            "idempotency_key": "blend:%s" % hashlib.sha256(canonical.encode()).hexdigest(),
        }

    def process_webhook(self, connection, event):
        self._ensure_blend_connection(connection)
        try:
            payload = json.loads(event.raw_payload or "{}")
        except (TypeError, ValueError) as error:
            raise DeliveryWebhookValidationError(
                _("The stored Blend webhook body is invalid JSON.")
            ) from error
        self._single_store_for_payload(connection, payload, allow_many=True)
        try:
            result, bindings = self.env["bs.delivery.blend.webhook.service"].dispatch_for_connection(
                connection,
                payload,
                webhook_event=event,
            )
        except BlendAPIError as error:
            raise DeliveryBusinessError(error.message) from error
        if len(bindings) == 1:
            event.sudo().write({"binding_id": bindings.id})
        return result

    def send_order(self, connection, order_or_binding, **kwargs):
        raise DeliveryBusinessError(
            _("Blend receives platform orders; POS orders cannot be exported to Blend."),
            code="unsupported_operation",
        )

    def update_order(self, connection, order_or_binding, new_status, reason=None, **kwargs):
        self._ensure_blend_connection(connection)
        order, binding = self._resolve_order_and_binding(connection, order_or_binding)
        config = order.config_id if order._name == "pos.order" else order.pos_config_id
        connection._blend_store_for_config(config)
        status = str(new_status or "").strip()
        payload = (
            order._blend_status_payload(status, connection)
            if order._name == "pos.order"
            else self._blend_status_payload_for_binding(connection, binding, status)
        )
        response = self._post(
            connection,
            connection.blend_outbound_webhook_url,
            payload,
            operation="blend_order_status",
            binding=binding,
            external_id=binding.external_order_id,
        )
        if isinstance(response, dict) and response.get("status") not in (None, "success"):
            raise DeliveryProviderServerError(
                _("Blend did not accept the order status update.")
            )
        return {"success": True, "status": new_status, "response": response}

    def cancel_order(self, connection, order_or_binding, reason=None, **kwargs):
        return self.update_order(connection, order_or_binding, "cancelled", reason=reason)

    def accept_order(self, connection, order_or_binding, **kwargs):
        return self.update_order(connection, order_or_binding, "accepted")

    def reject_order(self, connection, order_or_binding, reason=None, **kwargs):
        return self.update_order(connection, order_or_binding, "rejected", reason=reason)

    def mark_order_preparing(self, connection, order_or_binding, **kwargs):
        return self.update_order(connection, order_or_binding, "preparing")

    def mark_order_ready(self, connection, order_or_binding, **kwargs):
        return self.update_order(connection, order_or_binding, "prepared")

    def get_order_status(self, connection, order_or_binding, **kwargs):
        order, binding = self._resolve_order_and_binding(connection, order_or_binding)
        connection._blend_store_for_config(order.config_id)
        return {
            "external_order_id": binding.external_order_id,
            "status": order.blend_status if order._name == "pos.order" else order.state,
        }

    def pull_menu(self, connection, *, store_binding=None, tolerate_failure=False, **kwargs):
        self._ensure_blend_connection(connection)
        store = store_binding or self._single_store_for_payload(connection, {}, allow_many=False)
        if not connection.blend_menu_url:
            raise DeliveryBusinessError(_("Blend Get Menu URL is not configured."))
        url = connection._blend_validate_url(
            connection.blend_menu_url, label=_("Blend Get Menu URL")
        )
        correlation_id = str(uuid.uuid4())
        log = self.env["bs.delivery.sync.log"]
        try:
            response, response_payload, log, duration_ms = self.env["bs.delivery.http.client"].execute(
                connection,
                operation="blend_menu_pull",
                request_callable=lambda: requests.get(
                    url,
                    headers=self._headers(connection),
                    timeout=connection.blend_timeout_seconds,
                ),
                request_url=url,
                request_method="GET",
                provider_label="Blend",
                request_payload={},
                external_id=store.remote_store_id,
                correlation_id=correlation_id,
            )
            if not 200 <= response.status_code < 300:
                error = self._http_error(response.status_code)
                log.mark_failed(
                    str(error),
                    response_payload=response_payload,
                    http_status=response.status_code,
                    duration_ms=duration_ms,
                )
                if tolerate_failure:
                    return {"log_id": log.id, "failed": True}
                raise error
            event_payload = self._menu_event(response_payload, store.remote_store_id)
            result = BlendMenuService(self.env, connection, store).sync_menu(event_payload)
            log.mark_success(
                response_payload=response_payload,
                http_status=response.status_code,
                duration_ms=duration_ms,
            )
            return {"log_id": log.id, "result": result}
        except DeliveryIntegrationError:
            if tolerate_failure:
                failed_log = self.env["bs.delivery.sync.log"].sudo().search(
                    [
                        ("connection_id", "=", connection.id),
                        ("correlation_id", "=", correlation_id),
                    ],
                    order="id desc",
                    limit=1,
                )
                return {"log_id": failed_log.id if failed_log else False, "failed": True}
            raise
        except BlendAPIError as error:
            if log:
                log.mark_failed(error.message)
            mapped = DeliveryBusinessError(error.message)
            if tolerate_failure:
                return {"log_id": log.id if log else False, "failed": True}
            raise mapped from error

    def push_menu(self, connection, *, store_binding=None, **kwargs):
        self._ensure_blend_connection(connection)
        if not connection.blend_outbound_webhook_url:
            raise DeliveryBusinessError(_("Blend Outbound Webhook URL is not configured."))
        payload = {
            "event": "menu.updated",
            "entity": {
                "type": kwargs.get("entity_type") or "product",
            },
            "event_time": fields.Datetime.to_string(fields.Datetime.now()),
        }
        if "entity_id" in kwargs:
            payload["entity"]["id"] = str(kwargs["entity_id"])
        if connection.blend_brand_id:
            payload["brand_id"] = connection.blend_brand_id
        if store_binding and store_binding.remote_store_id:
            payload["branch_id"] = store_binding.remote_store_id
        response = self._post(
            connection,
            connection.blend_outbound_webhook_url,
            payload,
            operation="blend_menu_push",
            external_id=payload["entity"].get("id") or connection.blend_brand_id or "menu",
        )
        return {
            "success": True,
            "result": response if isinstance(response, dict) else {},
            "response": response,
        }

    def update_product_availability(self, connection, products, is_available=None, **kwargs):
        self._ensure_blend_connection(connection)
        if is_available is None and "is_available" in kwargs:
            is_available = kwargs["is_available"]
        if is_available is None:
            is_available = getattr(products[:1], "delivery_available", True)
        is_available = bool(is_available)

        templates = products if products._name == "product.template" else products.mapped("product_tmpl_id")
        if "blend_is_available" in templates._fields:
            templates.sudo().with_context(blend_inbound_sync=True).write({"blend_is_available": is_available})

        if not connection.blend_outbound_webhook_url:
            raise DeliveryBusinessError(_("Blend Outbound Webhook URL is not configured."))

        results = []
        for product in products:
            product_id = self._blend_product_id(connection, product)
            payload = {
                "event": "item.updated",
                "product_id": product_id,
                "is_stock": is_available,
                "event_time": fields.Datetime.to_string(fields.Datetime.now()),
            }
            if connection.blend_brand_id:
                payload["brand_id"] = connection.blend_brand_id
            response = self._post(
                connection,
                connection.blend_outbound_webhook_url,
                payload,
                operation="blend_item_availability",
                external_id=product_id,
            )
            results.append(response)
        return {"success": True, "is_available": is_available, "results": results}

    def update_store_availability(self, connection, store_binding, is_available, **kwargs):
        self._ensure_blend_connection(connection)
        if not connection.blend_outbound_webhook_url:
            raise DeliveryBusinessError(_("Blend Outbound Webhook URL is not configured."))
        branch_id = store_binding.remote_store_id or store_binding.pos_config_id.blend_branch_id
        if not branch_id:
            raise DeliveryBusinessError(
                _("Store binding %s has no remote store or Blend Branch ID configured.")
                % store_binding.display_name
            )
        payload = {
            "event": "store.updated",
            "branch_id": str(branch_id),
            "status": bool(is_available),
            "event_time": fields.Datetime.to_string(fields.Datetime.now()),
        }
        brand_id = (
            connection.blend_brand_id
            or store_binding.remote_vendor_id
            or store_binding.pos_config_id.blend_brand_id
        )
        if brand_id:
            payload["brand_id"] = str(brand_id)
        response = self._post(
            connection,
            connection.blend_outbound_webhook_url,
            payload,
            operation="blend_store_availability",
            external_id=str(branch_id),
        )
        return {"success": True, "is_available": is_available, "response": response}

    def sync_products(self, connection, products=None, **kwargs):
        self._ensure_blend_connection(connection)
        if not connection.blend_outbound_webhook_url:
            raise DeliveryBusinessError(_("Blend Outbound Webhook URL is not configured."))
        if not products:
            return self.push_menu(connection, **kwargs)
        results = []
        for product in products:
            product_id = self._blend_product_id(connection, product)
            payload = {
                "event": "menu.updated",
                "entity": {
                    "type": "product",
                    "id": product_id,
                },
                "event_time": fields.Datetime.to_string(fields.Datetime.now()),
            }
            if connection.blend_brand_id:
                payload["brand_id"] = connection.blend_brand_id
            response = self._post(
                connection,
                connection.blend_outbound_webhook_url,
                payload,
                operation="blend_sync_products",
                external_id=product_id,
            )
            results.append(response)
        return {"success": True, "results": results}

    def _blend_product_id(self, connection, product):
        if getattr(product, "blend_external_id", False):
            return str(product.blend_external_id)
        if product._name == "product.product" and getattr(product.product_tmpl_id, "blend_external_id", False):
            return str(product.product_tmpl_id.blend_external_id)
        template_id = product.id if product._name == "product.template" else product.product_tmpl_id.id
        binding = self.env["bs.delivery.product.binding"].sudo().search(
            [
                ("connection_id", "=", connection.id),
                ("product_id.product_tmpl_id", "=", template_id),
            ],
            limit=1,
        )
        if binding and binding.external_product_id:
            return str(binding.external_product_id)
        if product.default_code:
            return str(product.default_code)
        return str(product.id)

    def sync_menu(self, connection, **kwargs):
        """Compatibility alias for the pre-Core Blend refresh service.

        It deliberately is not advertised as an adapter capability; generic
        menu jobs dispatch the explicit ``pull_menu`` contract.
        """
        return self.pull_menu(connection, **kwargs)

    def _post(self, connection, url, payload, *, operation, binding=None, external_id=None):
        url = connection._blend_validate_url(url, label=_("Blend endpoint"))
        response, response_payload, log, duration_ms = self.env["bs.delivery.http.client"].execute(
            connection,
            operation=operation,
            request_callable=lambda: requests.post(
                url,
                json=payload,
                headers=self._headers(connection),
                timeout=connection.blend_timeout_seconds,
            ),
            request_url=url,
            request_method="POST",
            provider_label="Blend",
            request_payload=payload,
            binding=binding,
            external_id=external_id,
        )
        if not 200 <= response.status_code < 300:
            error = self._http_error(response.status_code)
            log.mark_failed(
                str(error),
                response_payload=response_payload,
                http_status=response.status_code,
                duration_ms=duration_ms,
            )
            raise error
        if isinstance(response_payload, dict) and response_payload.get("status") == "failed":
            error = DeliveryProviderServerError(_("Blend returned status=failed."))
            log.mark_failed(
                str(error),
                response_payload=response_payload,
                http_status=response.status_code,
                duration_ms=duration_ms,
            )
            raise error
        log.mark_success(
            response_payload=response_payload,
            http_status=response.status_code,
            duration_ms=duration_ms,
        )
        return response_payload

    @staticmethod
    def _menu_event(payload, branch_id):
        if not isinstance(payload, dict):
            raise BlendAPIError("Blend Get Menu response must be a JSON object.")
        if payload.get("event") == "menu_sync":
            return payload
        if isinstance(payload.get("payload"), dict) and "categories" in payload["payload"]:
            menu = payload["payload"]
        elif "categories" in payload:
            menu = payload
        else:
            raise BlendAPIError("Blend Get Menu response does not contain menu data.")
        return {
            "event": "menu_sync",
            "event_time": fields.Datetime.to_string(fields.Datetime.now()),
            "branch_id": branch_id,
            "payload": menu,
        }

    @staticmethod
    def _headers(connection):
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if connection.access_token:
            headers["Authorization"] = "Bearer %s" % connection.access_token
        return headers

    @staticmethod
    def _bearer_token(headers):
        authorization = ""
        for name, value in (headers or {}).items():
            if str(name).casefold() == "authorization":
                authorization = str(value or "")
                break
        return authorization[7:].strip() if authorization.startswith("Bearer ") else False

    def _single_store_for_payload(self, connection, payload, *, allow_many):
        nested = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
        branch_id = str(payload.get("branch_id") or nested.get("branch_id") or "").strip()
        brand_id = str(payload.get("brand_id") or "").strip()
        domain = [("connection_id", "=", connection.id), ("active", "=", True)]
        if branch_id:
            domain.append(("remote_store_id", "=", branch_id))
        elif brand_id:
            domain.extend(
                ["|", ("remote_vendor_id", "=", brand_id), ("connection_id.blend_brand_id", "=", brand_id)]
            )
        stores = self.env["bs.delivery.store.binding"].sudo().search(domain)
        if not stores:
            raise DeliveryWebhookValidationError(
                _("No Blend store binding matches this event.")
            )
        if not allow_many and len(stores) != 1:
            raise DeliveryWebhookValidationError(
                _("The Blend store mapping is ambiguous.")
            )
        return stores

    def _resolve_order_and_binding(self, connection, order_or_binding):
        if order_or_binding._name == "bs.delivery.order.binding":
            binding = order_or_binding
            order = binding.pos_order_id or self.env["bs.delivery.order"].sudo().search([
                ("binding_id", "=", binding.id),
            ], limit=1)
        else:
            order = order_or_binding
            binding = self.env["bs.delivery.order.binding"].sudo().search(
                [
                    ("connection_id", "=", connection.id),
                    ("pos_order_id", "=", order.id),
                ],
                limit=1,
            )
        if not order or not binding or binding.connection_id != connection:
            raise DeliveryBusinessError(_("The order has no Blend binding."))
        config = order.config_id if order._name == "pos.order" else order.pos_config_id
        if config not in connection.pos_config_ids:
            raise DeliveryAuthenticationError(
                _("The POS order does not belong to this Blend connection.")
            )
        return order, binding

    def _blend_status_payload_for_binding(self, connection, binding, status):
        """Use the existing Blend schema while the canonical order has no POS link."""
        canonical = self.env["bs.delivery.order"].sudo().search([
            ("binding_id", "=", binding.id),
        ], limit=1)
        if not canonical or canonical.connection_id != connection:
            raise DeliveryBusinessError(_("The order has no Blend binding."))
        store = canonical.store_binding_id
        if not store or store.pos_config_id not in connection.pos_config_ids:
            raise DeliveryAuthenticationError(_("The POS order does not belong to this Blend connection."))
        event_time = fields.Datetime.to_string(fields.Datetime.now())
        reference = canonical.blend_api_order_id or binding.external_order_id
        if connection.blend_schema == "pos":
            status_map = {
                "cancelled": "CANCELED", "accepted": "ACCEPTED",
                "preparing": "PREPARING", "rejected": "REJECTED",
                "time_out": "TIME_OUT",
            }
            return {
                "brand_id": store.remote_vendor_id or connection.blend_brand_id or "",
                "event_time": event_time,
                "event": "order.updated",
                "branch_id": store.remote_store_id,
                "order_id": reference,
                "status": status_map.get(status, str(status).upper()),
            }
        return {
            "event": "order_updated",
            "event_time": event_time,
            "branch_id": store.remote_store_id,
            "payload": {"order_id": reference, "status": status},
        }

    @staticmethod
    def _http_error(status_code):
        if status_code in (401, 403):
            return DeliveryAuthenticationError(_("Blend rejected the configured token."))
        return DeliveryProviderServerError(
            _("Blend returned HTTP %s.") % status_code
        )

    @staticmethod
    def _ensure_blend_connection(connection):
        connection.ensure_one()
        if connection.provider_id.code != "blend":
            raise DeliveryBusinessError(_("This adapter requires a Blend connection."))
