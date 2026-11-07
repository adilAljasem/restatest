from datetime import datetime, time

from odoo import api, fields, models
from odoo.exceptions import ValidationError

DAILY_LIMIT = 5

BADGES = [
    ('thanks', 'Thank you'),
    ('teamwork', 'Teamwork'),
    ('customer_hero', 'Customer hero'),
    ('clean_kitchen', 'Clean kitchen'),
    ('fast_service', 'Fast service'),
]
BADGE_ICONS = {
    'thanks': 'fa-heart',
    'teamwork': 'fa-users',
    'customer_hero': 'fa-star',
    'clean_kitchen': 'fa-shower',
    'fast_service': 'fa-bolt',
}


class BsHrKudos(models.Model):
    _name = 'bs.hr.kudos'
    _description = 'Kudos'
    _order = 'date desc, id desc'

    from_employee_id = fields.Many2one('hr.employee', string='From', required=True, index=True)
    to_employee_id = fields.Many2one('hr.employee', string='To', required=True, index=True)
    message = fields.Text(required=True)
    badge = fields.Selection(BADGES, required=True, default='thanks')
    date = fields.Datetime(required=True, default=fields.Datetime.now, index=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    work_location_id = fields.Many2one(related='to_employee_id.work_location_id', store=True, string='Branch')

    @api.constrains('from_employee_id', 'to_employee_id', 'company_id')
    def _check_kudos(self):
        for kudos in self:
            if kudos.from_employee_id == kudos.to_employee_id:
                raise ValidationError(self.env._('You cannot send kudos to yourself.'))
            if kudos.from_employee_id.sudo().company_id != kudos.to_employee_id.sudo().company_id:
                raise ValidationError(self.env._('Kudos can only be sent to a colleague of the same company.'))

    @api.constrains('from_employee_id', 'date')
    def _check_daily_limit(self):
        for kudos in self:
            day = kudos.date.date()
            count = self.sudo().search_count([
                ('from_employee_id', '=', kudos.from_employee_id.id),
                ('date', '>=', datetime.combine(day, time.min)),
                ('date', '<=', datetime.combine(day, time.max)),
            ])
            if count > DAILY_LIMIT:
                raise ValidationError(self.env._('You can send at most %s kudos per day.', DAILY_LIMIT))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('to_employee_id') and 'company_id' not in vals:
                vals['company_id'] = self.env['hr.employee'].sudo().browse(vals['to_employee_id']).company_id.id
        records = super().create(vals_list)
        records._notify_receiver()
        return records

    def _badge_label(self):
        self.ensure_one()
        return dict(self._fields['badge']._description_selection(self.env)).get(self.badge, '')

    def _badge_icon(self):
        return BADGE_ICONS.get(self.badge, 'fa-heart')

    def _notify_receiver(self):
        for kudos in self.sudo():
            receiver = kudos.to_employee_id
            title = self.env._('%(name)s sent you kudos: %(badge)s',
                               name=kudos.from_employee_id.name, badge=kudos._badge_label())
            self.env['bs.hr.notification'].sudo().create({
                'employee_id': receiver.id, 'title': title, 'body': kudos.message,
                'url': '/my/hr/kudos', 'res_model': kudos._name, 'res_id': kudos.id,
            })
            if receiver.user_id:
                receiver.message_notify(partner_ids=receiver.user_id.partner_id.ids, subject=title,
                                        body=kudos.message)
