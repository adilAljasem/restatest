from odoo import models


class PosOrder(models.Model):
    _inherit = 'pos.order'

    def _generate_pos_order_invoice(self):
        # The POS UI of l10n_sa_edi_pos sends generate_pdf=False; in that case l10n_sa_edi_pos calls the
        # ZATCA API synchronously. With the option enabled we flag the call so the EDI documents are only
        # signed locally (see account_edi_document.py).
        if (
            not self.env.context.get('generate_pdf', True)
            and self
            and all(o.company_id.country_id.code == 'SA' and o.config_id.l10n_sa_zatca_async for o in self)
        ):
            return super(PosOrder, self.with_context(l10n_sa_pos_async=True))._generate_pos_order_invoice()
        return super()._generate_pos_order_invoice()
