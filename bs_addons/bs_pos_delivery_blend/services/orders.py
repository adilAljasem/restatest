from datetime import UTC, datetime

from odoo.exceptions import UserError

from odoo.addons.bs_pos_delivery_core.exceptions import DeliveryBusinessError

from .exceptions import BlendAPIError


class BlendOrderService:
    STATUS_ALIASES = {
        "canceled": "cancelled",
        "cancelled": "cancelled",
        "pending": "pending",
        "accepted": "accepted",
        "preparing": "preparing",
        "prepared": "prepared",
        "ready_to_pick_up": "prepared",
        "rejected": "rejected",
        "time_out": "time_out",
        "timeout": "time_out",
        "assigned": "assigned",
        "at_pick_up": "at_pick_up",
        "on_the_way": "on_the_way",
        "delivered": "delivered",
        "completed": "delivered",
        "scheduled": "pending",
    }

    def __init__(self, env, connection, store):
        self.env = env
        self.connection = connection.sudo().with_company(connection.company_id)
        self.store = store.sudo()
        self.config = store.pos_config_id.sudo().with_company(connection.company_id)

    @staticmethod
    def _event_timestamp(value):
        if not value:
            return None
        try:
            timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as error:
            raise BlendAPIError("Invalid Blend event_time.") from error
        if not timestamp.tzinfo:
            raise BlendAPIError("Blend event_time must include a timezone.")
        return timestamp.astimezone(UTC).replace(tzinfo=None)

    def apply_inbound_status(self, order_reference, status, *, webhook_event=None, event_time=None):
        reference = str(order_reference or "").strip()
        normalized = self.STATUS_ALIASES.get(str(status or "").strip().lower())
        if not reference:
            raise BlendAPIError("order_id is required.")
        if not normalized:
            raise BlendAPIError("Unsupported Blend order status '%s'." % status)
        canonical = self.env["bs.delivery.order"].sudo().with_company(
            self.connection.company_id
        ).search([
            ("connection_id", "=", self.connection.id),
            ("store_binding_id", "=", self.store.id),
            ("blend_api_order_id", "=", reference),
            ("contract_version", "=", 1),
        ], limit=1)
        if canonical:
            if not webhook_event:
                raise BlendAPIError("An authenticated Blend event is required.", 401)
            target = self.env["bs.delivery.blend.intake"]._STATUS_STATES.get(
                str(status).strip().upper()
            )
            if not target:
                raise BlendAPIError("Unsupported Blend order status '%s'." % status, 422)
            occurred = self._event_timestamp(event_time)
            timestamp = (occurred.replace(tzinfo=UTC) if occurred
                         else datetime.now(UTC)).isoformat()
            try:
                canonical = self.env["bs.delivery.order.service"]._apply_provider_update(
                    self.connection, self.store, canonical.external_order_id,
                    {
                        "contract_version": 1, "state": target,
                        "occurred_at": timestamp,
                        "external_status": str(status).strip().upper(),
                    }, event=webhook_event,
                )
            except DeliveryBusinessError as error:
                raise BlendAPIError(str(error), 409) from error
            except UserError as error:
                raise BlendAPIError(str(error), 422) from error
            return {
                "order_id": reference,
                "status": canonical.pos_order_id.blend_status or canonical.state,
                "pos_order_id": canonical.pos_order_id.id or False,
            }, canonical.binding_id
        legacy = self.env["pos.order"].sudo().with_company(self.connection.company_id).search([
            ("config_id", "=", self.config.id),
            ("blend_order_id", "=", reference),
        ], limit=1)
        if legacy:
            raise BlendAPIError(
                "This Blend order predates Core intake and requires manual review.", 409,
            )
        raise BlendAPIError("Blend order '%s' was not found." % reference, 404)

