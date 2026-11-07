import logging

from odoo import _, api, models

from odoo.addons.bs_pos_delivery_core.exceptions import DeliveryIntegrationError


_logger = logging.getLogger(__name__)


class BlendCoreBridge(models.AbstractModel):
    """Keep legacy POS fields and provider-neutral Core records in lockstep."""

    _name = "bs.delivery.blend.bridge"
    _description = "Blend Delivery Core Compatibility Bridge"

    STATUS_TO_BINDING = {
        "pending": "pending",
        "accepted": "accepted",
        "preparing": "preparing",
        "prepared": "ready",
        "rejected": "rejected",
        "cancelled": "cancelled",
        "time_out": "cancelled",
        "assigned": "accepted",
        "at_pick_up": "ready",
        "on_the_way": "dispatched",
        "delivered": "completed",
    }

    @api.model
    def connection_for_config(self, config, *, connected=False):
        if not config:
            return self.env["bs.delivery.connection"]
        return config._blend_connection(connected=connected)

    @api.model
    def store_for_config(self, connection, config):
        connection.ensure_one()
        store = connection._blend_store_for_config(config)
        if store or not config.blend_branch_id:
            return store
        connection._blend_sync_stores()
        return connection._blend_store_for_config(config)

    @api.model
    def ensure_order_binding(
        self,
        order,
        connection=None,
        *,
        external_order_id=None,
        sync_state=None,
        webhook_event=None,
    ):
        order.ensure_one()
        remote_reference = str(external_order_id or order.blend_order_id or "").strip()
        if not remote_reference:
            return self.env["bs.delivery.order.binding"]
        connection = connection or self.connection_for_config(order.config_id)
        if not connection:
            return self.env["bs.delivery.order.binding"]
        connection.ensure_one()
        local_reference = str(order.uuid or order.pos_reference or order.id)
        Binding = self.env["bs.delivery.order.binding"].sudo()
        binding = Binding.search(
            [
                ("connection_id", "=", connection.id),
                ("pos_order_id", "=", order.id),
            ],
            limit=1,
        )
        if not binding:
            binding, _created = Binding.get_or_create(
                connection,
                remote_reference,
                {
                    "pos_order_id": order.id,
                    "external_reference": local_reference,
                    "external_source": "blend",
                    "external_status": order.blend_status or "pending",
                    "odoo_status": self.map_status(order.blend_status),
                },
            )
        if binding.pos_order_id and binding.pos_order_id != order:
            raise DeliveryIntegrationError(
                _("The Blend order is already linked to another POS order."),
                code="business_error",
            )
        values = {
            "pos_order_id": order.id,
            "external_order_id": remote_reference,
            "external_reference": local_reference,
            "external_source": "blend",
            "external_status": order.blend_status or binding.external_status or "pending",
            "odoo_status": self.map_status(order.blend_status or binding.external_status),
        }
        if sync_state:
            values["sync_state"] = sync_state
        binding.write(values)
        canonical_order = self.env["bs.delivery.order"].sudo().upsert_from_binding(
            binding,
            webhook_event=webhook_event,
        )
        if canonical_order and webhook_event:
            canonical_order.sudo()._with_delivery_internal_update().write(
                {"webhook_event_id": webhook_event.id}
            )
        return binding

    @api.model
    def sync_order(self, order, *, connection=None, webhook_event=None):
        try:
            order.ensure_one()
            empty_binding = self.env["bs.delivery.order.binding"]
            if not order.blend_order_id:
                return empty_binding
            connection = connection or self.connection_for_config(order.config_id)
            if not connection:
                return empty_binding
            binding = empty_binding.sudo().search([
                ("connection_id", "=", connection.id),
                ("pos_order_id", "=", order.id),
            ], limit=1)
            if not binding or binding.external_order_id != order.blend_order_id:
                return empty_binding
            canonical_order = self.env["bs.delivery.order"].sudo().search([
                ("binding_id", "=", binding.id),
            ], limit=1)
            if canonical_order and canonical_order.contract_version == 1:
                return binding
            return self.ensure_order_binding(
                order,
                connection,
                webhook_event=webhook_event,
            )
        except Exception:
            _logger.exception("Failed to synchronize Blend Core records for POS order %s", order.id)
            return self.env["bs.delivery.order.binding"]

    @api.model
    def map_status(self, status):
        normalized = str(status or "pending").strip().lower()
        return self.STATUS_TO_BINDING.get(normalized, "pending")

