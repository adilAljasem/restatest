import re

from odoo import api, fields, models
from odoo.exceptions import ValidationError

IBAN_SA = re.compile(r'^SA\d{22}$')


class BsHrProfileChange(models.Model):
    _name = 'bs.hr.profile.change'
    _description = 'ESS Profile Update Request'
    _inherit = ['bs.hr.request.mixin']
    _ess_sequence_code = 'bs.hr.profile.change'
    _ess_auto_done = True

    new_mobile = fields.Char(string='New mobile')
    new_private_email = fields.Char(string='New private email')
    new_private_street = fields.Char(string='New address')
    new_private_city = fields.Char(string='New city')
    new_emergency_contact = fields.Char(string='New emergency contact')
    new_emergency_phone = fields.Char(string='New emergency phone')
    new_iban = fields.Char(string='New IBAN')
    new_bank_name = fields.Char(string='Bank name')

    @api.constrains('new_iban')
    def _check_iban(self):
        for rec in self:
            if rec.new_iban:
                iban = rec.new_iban.replace(' ', '').upper()
                if not IBAN_SA.match(iban):
                    raise ValidationError(self.env._('A Saudi IBAN is SA followed by 22 digits.'))

    def _ess_check_submit(self):
        fields_ = ['new_mobile', 'new_private_email', 'new_private_street', 'new_private_city',
                   'new_emergency_contact', 'new_emergency_phone', 'new_iban']
        for rec in self:
            if not any(rec[f] for f in fields_):
                raise ValidationError(self.env._('Fill at least one field to update.'))

    def _ess_portal_summary(self):
        self.ensure_one()
        labels = []
        for fname in ('new_mobile', 'new_private_email', 'new_private_street', 'new_private_city',
                      'new_emergency_contact', 'new_emergency_phone', 'new_iban'):
            if self[fname]:
                labels.append(self._fields[fname].get_description(self.env)['string'])
        return ', '.join(labels)

    def _ess_portal_detail_fields(self):
        return ['new_mobile', 'new_private_email', 'new_private_street', 'new_private_city',
                'new_emergency_contact', 'new_emergency_phone', 'new_iban', 'new_bank_name']

    def _ess_on_approved(self):
        for rec in self:
            employee = rec.employee_id.sudo()
            vals = {}
            mapping = {
                'new_mobile': 'private_phone', 'new_private_email': 'private_email',
                'new_private_street': 'private_street', 'new_private_city': 'private_city',
                'new_emergency_contact': 'emergency_contact', 'new_emergency_phone': 'emergency_phone',
            }
            for src, dst in mapping.items():
                if rec[src]:
                    vals[dst] = rec[src]
            if vals:
                employee.write(vals)
            if rec.new_iban:
                iban = rec.new_iban.replace(' ', '').upper()
                partner = employee.work_contact_id
                bank_vals = {'acc_number': iban, 'partner_id': partner.id}
                if rec.new_bank_name:
                    bank = self.env['res.bank'].sudo().search([('name', '=ilike', rec.new_bank_name)], limit=1) \
                        or self.env['res.bank'].sudo().create({'name': rec.new_bank_name})
                    bank_vals['bank_id'] = bank.id
                account = self.env['res.partner.bank'].sudo().search(
                    [('acc_number', '=', iban), ('partner_id', '=', partner.id)], limit=1) \
                    or self.env['res.partner.bank'].sudo().create(bank_vals)
                employee.bank_account_ids = [(4, account.id)]
