from odoo import _, api, fields, models


class PosOrder(models.Model):
    _inherit = "pos.order"

    bs_kitchen_resend_count = fields.Integer(
        string="Kitchen Resends", default=0, copy=False, readonly=True,
        help="How many times this order was re-sent to the kitchen after payment.",
    )
    bs_kitchen_last_resend = fields.Datetime(
        string="Last Kitchen Resend", copy=False, readonly=True,
    )

    @api.model
    def bs_register_kitchen_resend(self, order_id, channel):
        """Called from the POS before a resend. Returns the new resend number."""
        order = self.browse(order_id).exists()
        if not order:
            return 0
        count = order.bs_kitchen_resend_count + 1
        order.sudo().write({
            "bs_kitchen_resend_count": count,
            "bs_kitchen_last_resend": fields.Datetime.now(),
        })
        label = {
            "print": _("Kitchen printer"),
            "display": _("Kitchen display"),
        }.get(channel, channel)
        order.sudo().message_post(
            body=_("Kitchen resend #%(count)s → %(target)s by %(user)s",
                   count=count, target=label, user=self.env.user.name),
        )
        return count
