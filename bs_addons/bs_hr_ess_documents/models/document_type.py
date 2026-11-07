from odoo import api, fields, models
from odoo.exceptions import ValidationError


class BsHrDocumentType(models.Model):
    _name = 'bs.hr.document.type'
    _description = 'Employee Document Type'
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True)
    code = fields.Char(index=True)
    sequence = fields.Integer(default=10)
    alert_days = fields.Char(
        string='Alert before expiry (days)', default='60,30,15,7', required=True,
        help='Comma separated list of days before the expiry date when alerts are sent.')
    block_attendance = fields.Boolean(
        string='Critical for attendance',
        help='When expired, the self-service check-in is blocked (or the supervisor notified, '
             'depending on the company setting). Typically the health certificate.')
    mandatory_for = fields.Selection([
        ('none', 'Optional'),
        ('all', 'All employees'),
        ('non_saudi', 'Non-Saudi employees'),
        ('saudi', 'Saudi employees'),
    ], default='none', required=True, string='Mandatory for')
    job_ids = fields.Many2many('hr.job', string='Only for job positions',
                               help='Restrict the mandatory rule to these job positions (e.g. drivers).')
    company_id = fields.Many2one('res.company', help='Leave empty to share the type between companies.')
    active = fields.Boolean(default=True)
    note = fields.Text(translate=True)

    _code_company_uniq = models.Constraint('UNIQUE(code, company_id)', 'The document type code must be unique.')

    @api.constrains('alert_days')
    def _check_alert_days(self):
        for rec in self:
            try:
                rec._alert_thresholds()
            except ValueError:
                raise ValidationError(self.env._('Alert days must be a comma separated list of numbers, e.g. 60,30,15,7.'))

    def _alert_thresholds(self):
        """Positive thresholds sorted from the largest to the smallest."""
        self.ensure_one()
        values = set()
        for part in (self.alert_days or '').replace('،', ',').split(','):
            part = part.strip()
            if part:
                value = int(part)
                if value > 0:
                    values.add(value)
        return sorted(values, reverse=True)

    def _is_mandatory_for(self, employee):
        self.ensure_one()
        if self.mandatory_for == 'none':
            return False
        employee = employee.sudo()
        if self.company_id and employee.company_id != self.company_id:
            return False
        if self.job_ids and employee.job_id not in self.job_ids:
            return False
        is_saudi = employee.country_id.code == 'SA'
        if self.mandatory_for == 'saudi':
            return is_saudi
        if self.mandatory_for == 'non_saudi':
            return not is_saudi
        return True
