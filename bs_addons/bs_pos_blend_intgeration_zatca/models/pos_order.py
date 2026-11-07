import math

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.bs_pos_blend_intgeration.services.totals import blend_payload_total

OVERRIDE_FIELDS = {
    "blend_zatca_override_by", "blend_zatca_override_at",
    "blend_zatca_override_odoo_total", "blend_zatca_override_blend_total",
    "blend_zatca_override_invoice_id",
}


class PosOrder(models.Model):
    _inherit = "pos.order"

    blend_zatca_override_by = fields.Many2one(
        "res.users", string="ZATCA Override Approved By", readonly=True,
        copy=False, groups="base.group_system",
    )
    blend_zatca_override_at = fields.Datetime(
        string="ZATCA Override Approved At", readonly=True,
        copy=False, groups="base.group_system",
    )
    blend_zatca_override_odoo_total = fields.Monetary(
        string="Approved Odoo Total", currency_field="currency_id",
        readonly=True, copy=False, groups="base.group_system",
    )
    blend_zatca_override_blend_total = fields.Monetary(
        string="Approved Blend Total", currency_field="currency_id",
        readonly=True, copy=False, groups="base.group_system",
    )
    blend_zatca_override_invoice_id = fields.Many2one(
        "account.move", string="Approved ZATCA Invoice", readonly=True,
        copy=False, groups="base.group_system",
    )

    # @api.model_create_multi
    # def create(self, vals_list):
    #     if not self.env.su and (
    #         any(OVERRIDE_FIELDS.intersection(vals) for vals in vals_list)
    #         or any("default_" + name in self.env.context for name in OVERRIDE_FIELDS)
    #     ):
    #         raise AccessError(_("ZATCA override fields are managed by the administrator action."))
    #     return super().create(vals_list)
    #
    # def write(self, vals):
    #     if not self.env.su and OVERRIDE_FIELDS.intersection(vals):
    #         raise AccessError(_("ZATCA override fields are managed by the administrator action."))
    #     return super().write(vals)

    def _blend_zatca_override_applies(self):
        self.ensure_one()
        expected = blend_payload_total(self.blend_payload)
        return bool(
            self.blend_is_inbound and self.blend_zatca_override_by
            and self.blend_zatca_override_at and self.currency_id
            and self.account_move == self.blend_zatca_override_invoice_id
            and expected is not None
            and math.isfinite(self.amount_total)
            and self.currency_id.is_zero(self.amount_total - self.blend_zatca_override_odoo_total)
            and self.currency_id.is_zero(expected - self.blend_zatca_override_blend_total),
        )

    def action_blend_send_zatca_with_total_override(self):
        self.ensure_one()
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Only administrators can override a Blend total mismatch for ZATCA."))
        self.check_access("write")
        if not self.blend_is_inbound or not self.currency_id:
            raise UserError(_("This action is only available for an incoming Blend order."))
        expected = blend_payload_total(self.blend_payload)
        if expected is None or not math.isfinite(self.amount_total):
            raise UserError(_("The original Blend total is missing or invalid."))
        if self.currency_id.is_zero(self.amount_total - expected):
            raise UserError(_("The Odoo and Blend totals already match."))
        # The order is access-checked above; the linked invoice and its EDI
        # document are processed with the same elevated access as the EDI cron.
        invoice = self.account_move.sudo()
        if (not invoice or invoice.state != "posted" or invoice.move_type != "out_invoice"
                or invoice.company_id != self.company_id):
            raise UserError(_("A posted customer invoice in the order company is required."))
        document = invoice.edi_document_ids.filtered(
            lambda doc: doc.edi_format_id.code == "sa_zatca" and doc.state == "to_send",
        )
        if len(document) != 1 or document.blocking_level == "error":
            raise UserError(_("A retryable ZATCA document awaiting submission is required."))

        self.sudo().write({
            "blend_zatca_override_by": self.env.user.id,
            "blend_zatca_override_at": fields.Datetime.now(),
            "blend_zatca_override_odoo_total": self.amount_total,
            "blend_zatca_override_blend_total": expected,
            "blend_zatca_override_invoice_id": invoice.id,
        })
        self.message_post(body=_(
            "An administrator authorized ZATCA submission despite the Blend total mismatch. "
            "The approved Odoo and Blend totals are recorded on this order.",
        ))
        document._process_documents_web_services(with_commit=False)
        sent = document.state == "sent"
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("ZATCA submission"),
                "message": _("The invoice was sent to ZATCA.") if sent else _(
                    "ZATCA did not accept the invoice yet. The document remains available for retry.",
                ),
                "type": "success" if sent else "warning",
                "sticky": not sent,
            },
        }
