import logging

from odoo import models


_logger = logging.getLogger(__name__)


class PosOrder(models.Model):
    _inherit = 'pos.order'

    def _generate_pos_order_invoice(self):
        if not self.env.context.get('generate_pdf', True) or not self.config_id.defer_invoice_pdf_generation:
            return super()._generate_pos_order_invoice()

        send_cron = self.env.ref('account.ir_cron_account_move_send')
        if not send_cron.sudo().active:
            _logger.warning(
                'Deferred invoice PDF is enabled on POS %s, but the account send cron is inactive; '
                'generating the PDF during payment instead.',
                self.config_id.display_name,
            )
            return super()._generate_pos_order_invoice()

        invoice = super(PosOrder, self.with_context(generate_pdf=False))._generate_pos_order_invoice()
        sudo_invoice = invoice.sudo().with_company(invoice.company_id).with_context(
            skip_invoice_sync=True
        )
        needs_sa_edi = 'sa_edi' in self.env['account.move.send']._get_default_extra_edis(sudo_invoice)

        sudo_invoice.write({
            'pos_deferred_pdf_requested': True,
            'sending_data': {
                'author_user_id': self.env.user.id,
                'author_partner_id': self.env.user.partner_id.id,
            },
        })
        send_cron.sudo()._trigger()

        # The cron trigger is transactional: workers cannot see it until this
        # request commits. Schedule it before the external Saudi EDI call so
        # a scheduling error never follows an accepted ZATCA submission.
        if needs_sa_edi:
            sudo_invoice.action_process_edi_web_services(with_commit=False)
        return invoice
