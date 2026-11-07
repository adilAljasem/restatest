import math

from odoo import _, api, fields, models
from odoo.tools.misc import format_amount

from ..services.totals import blend_payload_total


class PosOrder(models.Model):
    _inherit = "pos.order"

    blend_total_matches = fields.Boolean(
        string="Blend Total Matches", compute="_compute_blend_total_match",
        help="Whether the order total matches the original Blend payload at currency precision. "
             "Orders not received from Blend are not subject to this check.",
    )
    blend_expected_total = fields.Monetary(
        string="Blend Original Total", compute="_compute_blend_total_match",
        currency_field="currency_id",
    )
    blend_total_difference = fields.Monetary(
        string="Difference from Blend", compute="_compute_blend_total_match",
        currency_field="currency_id",
    )
    blend_total_warning = fields.Text(
        string="Blend Total Warning", compute="_compute_blend_total_match",
    )

    @api.depends("blend_is_inbound", "blend_partner_reference", "blend_payload", "amount_total",
                 "currency_id", "currency_id.rounding")
    @api.depends_context("lang")
    def _compute_blend_total_match(self):
        for order in self:
            expected = blend_payload_total(order.blend_payload) if order.blend_is_inbound else None
            warning = order._blend_total_mismatch_message()
            order.blend_expected_total = expected if expected is not None else 0
            order.blend_total_difference = order.amount_total - expected if expected is not None else 0
            order.blend_total_matches = not warning
            order.blend_total_warning = warning

    def _blend_total_mismatch_message(self):
        """Recheck source values at submission time, independent of any UI flag."""
        self.ensure_one()
        if not self.blend_is_inbound:
            return False
        expected = blend_payload_total(self.blend_payload)
        if expected is None or not self.currency_id or not math.isfinite(self.amount_total):
            return _(
                "ZATCA submission is blocked for Blend order %(reference)s. "
                "The original Blend total or the Odoo total/currency is missing or invalid. "
                "Review and correct the order before submission.",
                reference=self.blend_partner_reference,
            )
        difference = self.amount_total - expected
        if self.currency_id.is_zero(difference):
            return False
        return _(
            "Blend order %(reference)s has different totals. "
            "Odoo total: %(actual)s. Blend total: %(expected)s. Difference (Odoo - Blend): %(difference)s. "
            "Review the difference before ZATCA submission.",
            reference=self.blend_partner_reference,
            actual=format_amount(self.env, self.amount_total, self.currency_id),
            expected=format_amount(self.env, expected, self.currency_id),
            difference=format_amount(self.env, difference, self.currency_id),
        )
