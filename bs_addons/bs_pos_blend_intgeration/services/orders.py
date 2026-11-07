from .exceptions import BlendAPIError


class BlendOrderService:
    STATUS_ALIASES = {
        "canceled": "cancelled",
        "cancelled": "cancelled",
        "pending": "pending",
        "accepted": "accepted",
        "preparing": "preparing",
        "prepared": "prepared",
        "ready_to_pick_up": "ready_to_pick_up",
        "rejected": "rejected",
        "time_out": "time_out",
        "timeout": "time_out",
        "assigned": "assigned",
        "at_pick_up": "at_pick_up",
        "on_the_way": "on_the_way",
        "delivered": "delivered",
        "completed": "completed",
    }

    def __init__(self, env, config):
        self.env = env
        self.config = config.sudo().with_company(config.company_id)

    def apply_inbound_status(self, order_reference, status):
        reference = str(order_reference or "").strip()
        normalized = self.STATUS_ALIASES.get(str(status or "").strip().lower())
        if not reference:
            raise BlendAPIError("order_id is required.")
        if not normalized:
            raise BlendAPIError("Unsupported Blend order status '%s'." % status)
        order_model = self.env["pos.order"].sudo().with_company(self.config.company_id)
        order = order_model.search(
            [
                ("config_id", "=", self.config.id),
                ("company_id", "=", self.config.company_id.id),
                "|", ("blend_partner_reference", "!=", False), ("blend_order_id", "!=", False),
                "|",
                ("blend_order_id", "=", reference),
                "|",
                ("uuid", "=", reference),
                "|",
                ("pos_reference", "=", reference),
                ("name", "=", reference),
            ],
            limit=1,
        )
        if not order:
            raise BlendAPIError("Blend order '%s' was not found." % reference, 404)
        order.flush_recordset()
        order.lock_for_update()
        order.invalidate_recordset()
        order.with_context(blend_skip_status_sync=True).write({"blend_status": normalized})
        if normalized in ("cancelled", "rejected", "time_out"):
            order._blend_mark_refund_review()
        order.message_post(body="Blend status updated to <b>%s</b>." % normalized)
        self.config._blend_notify(
            {
                "kind": "order",
                "branch_id": self.config.blend_branch_id,
                "order_id": reference,
                "status": normalized,
                "pos_order_id": order.id,
            }
        )
        return {"order_id": reference, "status": normalized, "pos_order_id": order.id}

