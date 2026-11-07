"""Advance Blend delivery orders from completed kitchen display actions."""

from odoo import models


class PosPrepState(models.Model):
    _inherit = "pos.prep.state"

    def change_state_stage(self, stages, prep_display_id):
        result = super().change_state_stage(stages, prep_display_id)
        self._sync_blend_kitchen_status(prep_display_id)
        return result

    def change_state_status(self, todos, prep_display_id):
        result = super().change_state_status(todos, prep_display_id)
        self._sync_blend_kitchen_status(prep_display_id)
        return result

    def _sync_blend_kitchen_status(self, prep_display_id):
        display = self.env["pos.prep.display"].browse(int(prep_display_id)).exists()
        if not display:
            return

        changed_states = self.filtered(
            lambda state: state.stage_id.prep_display_id == display
        )
        pos_orders = changed_states.prep_line_id.prep_order_id.pos_order_id
        DeliveryOrder = self.env["bs.delivery.order"].sudo()
        PrepState = self.env["pos.prep.state"].sudo()
        for pos_order in pos_orders.sorted("id"):
            if pos_order.company_id != display.company_id:
                continue
            if display.pos_config_ids and pos_order.config_id not in display.pos_config_ids:
                continue
            order = DeliveryOrder.search([
                ("pos_order_id", "=", pos_order.id),
                ("provider_id.code", "=", "blend"),
                ("company_id", "=", pos_order.company_id.id),
                ("pos_config_id", "=", pos_order.config_id.id),
            ], limit=1)
            if not order:
                continue
            # Serialize concurrent kitchen stations before reading their states.
            self.env.cr.execute(
                "SELECT id FROM bs_delivery_order WHERE id = %s FOR UPDATE",
                [order.id],
            )
            order.invalidate_recordset(["state"])
            if order.state not in ("accepted", "preparing"):
                continue

            states = PrepState.search([
                ("prep_line_id.prep_order_id.pos_order_id", "=", pos_order.id),
            ]).filtered(
                lambda state: state.prep_line_id.quantity > state.prep_line_id.cancelled
            )
            if not states:
                continue
            if not all(
                len(state.stage_id.prep_display_id.stage_ids) >= 3
                and state.stage_id in state.stage_id.prep_display_id.stage_ids[-2:]
                for state in states
            ):
                continue

            if order.state == "accepted":
                order.transition_to(
                    "preparing", source="system", expected_state="accepted",
                    idempotency_key="blend-kitchen:%s:preparing" % order.id,
                    reason="Kitchen Display: Ready",
                )
            if all(
                state.stage_id == state.stage_id.prep_display_id.stage_ids[-1]
                and not state.todo
                for state in states
            ) and order.state == "preparing":
                order.transition_to(
                    "ready", source="system", expected_state="preparing",
                    idempotency_key="blend-kitchen:%s:ready" % order.id,
                    reason="Kitchen Display: Done",
                )
