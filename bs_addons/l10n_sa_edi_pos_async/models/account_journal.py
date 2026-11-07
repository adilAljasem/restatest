from odoo import models


class AccountJournal(models.Model):
    _inherit = 'account.journal'

    def _l10n_sa_get_last_posted_invoice(self):
        """Standard Odoo blocks an invoice when the highest-ICV invoice of the journal is not yet accepted,
        because it signs and submits at the same time. With local signing, later invoices legitimately exist
        before earlier ones are reported, so only block on an earlier *synchronous* invoice that timed out."""
        move_id = self.env.context.get('l10n_sa_async_move_id')
        if not move_id:
            return super()._l10n_sa_get_last_posted_invoice()
        self.ensure_one()
        move = self.env['account.move'].browse(move_id)
        previous_sync = self.env['account.move'].search([
            ('journal_id', '=', self.id),
            ('l10n_sa_chain_index', '!=', 0),
            ('l10n_sa_chain_index', '<', move.l10n_sa_chain_index),
            ('l10n_sa_async_signed', '=', False),
        ], limit=1, order='l10n_sa_chain_index desc')
        if previous_sync and not previous_sync._l10n_sa_is_in_chain():
            return previous_sync
        return move

    def write(self, vals):
        if self.env.context.get('l10n_sa_async_keep_chain_head') and 'l10n_sa_latest_submission_hash' in vals:
            vals = dict(vals)
            vals.pop('l10n_sa_latest_submission_hash')
            if not vals:
                return True
        return super().write(vals)
