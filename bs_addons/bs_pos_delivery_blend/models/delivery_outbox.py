from odoo import _, fields, models

from odoo.addons.bs_pos_delivery_core.models.delivery_outbox import (
    DeliveryOutbox as CoreDeliveryOutbox,
)


class DeliveryOutbox(models.Model):
    _inherit = "bs.delivery.outbox"

    # Order creation is a provider-facing mutation and needs the same durable,
    # observable retry semantics as Core status mutations.
    MUTATING_OPERATIONS = CoreDeliveryOutbox.MUTATING_OPERATIONS | {"send_order"}

    operation = fields.Selection(
        selection_add=[("send_order", "Send Blend Order")],
        ondelete={"send_order": "cascade"},
    )

    def _dispatch_operation(self):
        self.ensure_one()
        if self.provider_id.code == "blend":
            self = self.with_env(self.connection_id._blend_execution_environment()).sudo()
        return super(DeliveryOutbox, self)._dispatch_operation()

    def _process_due_jobs(self, *, batch_size=50, commit_progress=False):
        self._cancel_obsolete_send_orders()
        return super()._process_due_jobs(
            batch_size=batch_size, commit_progress=commit_progress,
        )

    def _cancel_obsolete_send_orders(self):
        """Unblock later status jobs without sending old cashier orders."""
        obsolete = self.sudo().search([
            ("provider_id.code", "=", "blend"),
            ("operation", "=", "send_order"),
            ("state", "not in", ("done", "cancelled")),
        ], order="id")
        cancelled = 0
        for candidate in obsolete:
            job = candidate.try_lock_for_update(limit=1)
            if not job or job.state in ("done", "cancelled"):
                continue
            job.write({
                "state": "cancelled",
                "next_attempt_at": False,
                "processed_at": fields.Datetime.now(),
                "last_error": _("Blend no longer accepts cashier-origin POS orders."),
            })
            cancelled += 1
        return cancelled

