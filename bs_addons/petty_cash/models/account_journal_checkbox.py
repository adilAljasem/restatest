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
class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    # NOTE (upgrade review): this stub was already present in the Odoo 17
    # version and is kept as-is so the upgrade does not change behaviour.
    # `has_abnormal_deferred_dates` does not exist anywhere in Odoo 19
    # Community - it is an Enterprise (account_accountant) field used by the
    # Deferred Entries feature. Because petty_cash depends on
    # account_accountant, this definition loads last and overrides the
    # Enterprise compute with a constant False. If Deferred Entries are used on
    # the target database, delete this class: nothing else in this module
    # references the field.
    has_abnormal_deferred_dates = fields.Boolean(
        string='Has Abnormal Deferred Dates',
        compute='_compute_has_abnormal_deferred_dates',
        store=False,
    )

    def _compute_has_abnormal_deferred_dates(self):
        for line in self:
            line.has_abnormal_deferred_dates = False