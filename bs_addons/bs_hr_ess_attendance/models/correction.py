from odoo import api, fields, models
from odoo.exceptions import ValidationError


class BsHrAttendanceCorrection(models.Model):
    _name = 'bs.hr.attendance.correction'
    _description = 'Attendance correction'
    _inherit = ['bs.hr.request.mixin']
    _ess_sequence_code = 'bs.hr.attendance.correction'
    _ess_auto_done = True

    correction_type = fields.Selection([
        ('missing_in', 'Forgot to check in'),
        ('missing_out', 'Forgot to check out'),
        ('missing_both', 'Forgot both'),
        ('wrong_time', 'Wrong time recorded'),
    ], required=True, default='missing_out', tracking=True)
    attendance_id = fields.Many2one('hr.attendance', string='Attendance to fix',
                                    domain="[('employee_id', '=', employee_id)]")
    check_in = fields.Datetime(string='Real check-in')
    check_out = fields.Datetime(string='Real check-out')
    reason = fields.Text(required=True)

    @api.constrains('check_in', 'check_out')
    def _check_dates(self):
        for rec in self:
            if rec.check_in and rec.check_out and rec.check_out <= rec.check_in:
                raise ValidationError(self.env._('Check-out must be after check-in.'))
            if (rec.check_in and rec.check_in > fields.Datetime.now()) or \
                    (rec.check_out and rec.check_out > fields.Datetime.now()):
                raise ValidationError(self.env._('A correction cannot be in the future.'))

    def _ess_check_submit(self):
        for rec in self:
            if rec.correction_type in ('missing_in', 'missing_both', 'wrong_time') and not rec.check_in:
                raise ValidationError(self.env._('Give the real check-in time.'))
            if rec.correction_type in ('missing_out', 'missing_both') and not rec.check_out:
                raise ValidationError(self.env._('Give the real check-out time.'))
            if rec.correction_type == 'missing_out' and not rec.attendance_id:
                open_att = self.env['hr.attendance'].sudo().search([
                    ('employee_id', '=', rec.employee_id.id), ('check_out', '=', False),
                    ('check_in', '<', rec.check_out)], order='check_in desc', limit=1)
                if not open_att:
                    raise ValidationError(self.env._('No open attendance found before that check-out time.'))
                rec.attendance_id = open_att

    def _ess_portal_summary(self):
        self.ensure_one()
        label = dict(self._fields['correction_type']._description_selection(self.env)).get(self.correction_type)
        when = self.check_in or self.check_out
        return '%s - %s' % (label, fields.Datetime.context_timestamp(self, when).strftime('%Y-%m-%d %H:%M')
                            if when else '')

    def _ess_portal_detail_fields(self):
        return ['correction_type', 'check_in', 'check_out', 'reason']

    def _ess_on_approved(self):
        Attendance = self.env['hr.attendance'].sudo()
        for rec in self:
            vals_mode = {'in_mode': 'manual', 'ess_method': 'correction'}
            if rec.attendance_id:
                vals = {}
                if rec.check_in:
                    vals['check_in'] = rec.check_in
                if rec.check_out:
                    vals.update(check_out=rec.check_out, out_mode='manual', ess_out_method='correction')
                rec.attendance_id.write(vals)
            else:
                Attendance.create({'employee_id': rec.employee_id.id, 'check_in': rec.check_in,
                                   'check_out': rec.check_out or False,
                                   'ess_location_id': rec.employee_id.work_location_id.id, **vals_mode,
                                   'out_mode': 'manual' if rec.check_out else 'manual'})
