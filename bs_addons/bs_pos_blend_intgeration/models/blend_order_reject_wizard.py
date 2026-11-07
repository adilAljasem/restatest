from odoo import fields, models


class BlendOrderRejectWizard(models.TransientModel):
    _name = "blend.order.reject.wizard"
    _description = "Reject an Incoming Blend Order"

    order_id = fields.Many2one("pos.order", required=True, readonly=True, ondelete="cascade")
    reason = fields.Text(string="Rejection Reason", required=True)

    def action_confirm_rejection(self):
        self.ensure_one()
        self.check_access("write")
        # Recheck role, company, source, payment and pending status on confirmation.
        self.order_id._blend_apply_manual_decision("rejected", self.reason)
        return {"type": "ir.actions.act_window_close"}
