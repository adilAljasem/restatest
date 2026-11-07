from odoo import _, api, models
from odoo.exceptions import UserError


class AccountPayment(models.Model):
    _inherit = "account.payment"

    @api.depends("journal_id", "pos_session_id")
    def _compute_currency_id(self):
        super()._compute_currency_id()
        for payment in self.filtered(lambda pay: pay.pos_session_id.bs_clearing_journal_id):
            payment.currency_id = payment.pos_session_id.currency_id

    def _generate_move_vals(self, write_off_line_vals=None, force_balance=None, line_ids=None):
        vals = super()._generate_move_vals(write_off_line_vals, force_balance, line_ids)
        session = self.pos_session_id
        if session.bs_clearing_journal_id:
            session._bs_check_clearing_configuration()
            vals.update(
                journal_id=session.bs_clearing_journal_id.id,
                bs_pos_clearing_session_id=session.id,
                bs_pos_payment_journal_id=self.journal_id.id,
                bs_pos_outstanding_account_id=self.outstanding_account_id.id,
            )
        return vals

    def write(self, vals):
        if "pos_session_id" in vals and any(
            payment.move_id.bs_pos_clearing_session_id and payment.pos_session_id.id != vals["pos_session_id"]
            for payment in self
        ):
            raise UserError(_("The session cannot be changed on a generated POS clearing payment."))
        if "journal_id" in vals and any(
            payment.move_id.bs_pos_clearing_session_id and payment.journal_id.id != vals["journal_id"]
            for payment in self
        ):
            raise UserError(_("The source journal cannot be changed on a generated POS clearing payment."))
        return super().write(vals)
