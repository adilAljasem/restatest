from odoo import models, fields, api,_
from odoo.exceptions import ValidationError

class AccountJournal(models.Model):
    _inherit = 'account.journal'

    petty_cash_checkbox = fields.Boolean(
        string="Permanent Petty Cash"
    )
    temporary_petty_cash = fields.Boolean(
        string="Temporary Petty Cash"
    )
    @api.constrains('petty_cash_checkbox', 'temporary_petty_cash', 'company_id')
    def _check_unique_petty_cash_flags(self):
        for rec in self:

            if rec.petty_cash_checkbox:
                domain = [
                    ('petty_cash_checkbox', '=', True),
                    ('company_id', '=', rec.company_id.id),
                    ('id', '!=', rec.id)
                ]
                if self.search(domain, limit=1):
                    raise ValidationError(
                        _("Only one Permanent Petty Cash journal is allowed per company.")
                    )

            if rec.temporary_petty_cash:
                domain = [
                    ('temporary_petty_cash', '=', True),
                    ('company_id', '=', rec.company_id.id),
                    ('id', '!=', rec.id)
                ]
                if self.search(domain, limit=1):
                    raise ValidationError(
                        _("Only one Temporary Petty Cash journal is allowed per company.")
                    )
