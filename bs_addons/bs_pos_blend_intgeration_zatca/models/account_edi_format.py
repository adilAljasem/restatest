from odoo import models


class AccountEdiFormat(models.Model):
    _inherit = "account.edi.format"

    def _l10n_sa_post_zatca_edi(self, invoice):
        invoice.ensure_one()
        # Match core POS invoice reads: accounting/cron users may not have POS
        # access. Elevate only this read of orders linked to the current invoice.
        errors = [
            message
            for order in invoice.sudo().pos_order_ids
            if (message := order._blend_total_mismatch_message())
            and not order._blend_zatca_override_applies()
        ]
        if errors:
            # No chain index, signing or network call until totals match or an
            # administrator authorizes this exact mismatch and invoice.
            # Keep the document eligible for the native retry cron, which
            # rechecks the totals; an "error" level would disable those retries.
            return {invoice: {
                "error": "\n".join(errors),
                "blocking_level": "warning",
                "response": None,
            }}
        return super()._l10n_sa_post_zatca_edi(invoice)
