from datetime import datetime, time, timedelta

import pytz

from odoo import api, fields, models


class HrAttendance(models.Model):
    _inherit = 'hr.attendance'

    in_mode = fields.Selection(selection_add=[('ess_portal', 'Self-Service')],
                               ondelete={'ess_portal': 'set default'})
    out_mode = fields.Selection(selection_add=[('ess_portal', 'Self-Service')],
                                ondelete={'ess_portal': 'set default'})
    ess_location_id = fields.Many2one('hr.work.location', string='Branch', index=True)
    ess_method = fields.Char(string='Check-in method', readonly=True)
    ess_out_method = fields.Char(string='Check-out method', readonly=True)
    ess_distance = fields.Float(string='Distance (m)', readonly=True, aggregator='avg')
    ess_accuracy = fields.Float(string='GPS accuracy (m)', readonly=True, aggregator='avg')
    ess_face_distance = fields.Float(string='Face distance', digits=(4, 3), readonly=True, aggregator='avg')
    ess_liveness = fields.Boolean(string='Liveness passed', readonly=True)
    ess_device_id = fields.Many2one('bs.hr.ess.device', string='Device', readonly=True)
    ess_photo = fields.Image(string='Check-in photo', max_width=320, max_height=320, attachment=True)
    ess_suspicious = fields.Boolean(string='Suspicious', index=True)
    ess_suspicious_reason = fields.Char(string='Why suspicious')
    ess_late_minutes = fields.Integer(string='Late (min)', compute='_compute_ess_lateness', store=True,
                                      aggregator='sum')
    ess_early_minutes = fields.Integer(string='Left early (min)', compute='_compute_ess_lateness', store=True,
                                       aggregator='sum')

    # ------------------------------------------------------------------
    # lateness
    # ------------------------------------------------------------------
    def _ess_day_intervals(self):
        """Scheduled (start, stop) UTC-naive for the day of check-in."""
        self.ensure_one()
        employee = self.employee_id
        calendar = employee.resource_calendar_id or employee.company_id.resource_calendar_id
        if not calendar or not self.check_in:
            return []
        tz = pytz.timezone(employee.tz or calendar.tz or 'UTC')
        local_day = pytz.utc.localize(self.check_in).astimezone(tz).date()
        start = tz.localize(datetime.combine(local_day, time.min))
        stop = tz.localize(datetime.combine(local_day, time.max))
        intervals = calendar._work_intervals_batch(start, stop, resources=employee.resource_id)
        result = []
        for i_start, i_stop, _meta in intervals.get(employee.resource_id.id, []):
            result.append((i_start.astimezone(pytz.utc).replace(tzinfo=None),
                           i_stop.astimezone(pytz.utc).replace(tzinfo=None)))
        return sorted(result)

    @api.depends('check_in', 'check_out', 'employee_id')
    def _compute_ess_lateness(self):
        for att in self:
            late = early = 0
            intervals = att._ess_day_intervals() if att.check_in else []
            if intervals:
                grace = att.employee_id.company_id.ess_late_grace_minutes or 0
                first_start = intervals[0][0]
                last_stop = intervals[-1][1]
                # only the first attendance of the day can be late
                earlier = self.search_count([
                    ('employee_id', '=', att.employee_id.id), ('id', '!=', att.id or 0),
                    ('check_in', '>=', first_start - timedelta(hours=4)), ('check_in', '<', att.check_in),
                ]) if att.id else 0
                if not earlier and att.check_in > first_start + timedelta(minutes=grace):
                    late = int((att.check_in - first_start).total_seconds() // 60)
                if att.check_out and att.check_out < last_stop:
                    later = self.search_count([
                        ('employee_id', '=', att.employee_id.id), ('id', '!=', att.id or 0),
                        ('check_in', '>', att.check_out), ('check_in', '<', last_stop),
                    ]) if att.id else 0
                    if not later:
                        early = int((last_stop - att.check_out).total_seconds() // 60)
            att.ess_late_minutes = late
            att.ess_early_minutes = early

    @api.model
    def _cron_ess_clean_photos(self):
        for company in self.env['res.company'].search([]):
            days = company.ess_face_photo_days or 30
            limit = fields.Datetime.now() - timedelta(days=days)
            old = self.sudo().search([('employee_id.company_id', '=', company.id),
                                      ('check_in', '<', limit), ('ess_photo', '!=', False)])
            old.write({'ess_photo': False})


class BsHrAttendanceAttempt(models.Model):
    """Audit log of every self-service punch attempt (also used for rate limiting)."""
    _name = 'bs.hr.attendance.attempt'
    _description = 'ESS attendance attempt'
    _order = 'id desc'

    employee_id = fields.Many2one('hr.employee', required=True, index=True, ondelete='cascade')
    company_id = fields.Many2one(related='employee_id.company_id', store=True)
    location_id = fields.Many2one('hr.work.location', string='Branch')
    success = fields.Boolean(index=True)
    action = fields.Selection([('check_in', 'Check in'), ('check_out', 'Check out')])
    methods = fields.Char()
    reason = fields.Char()
    latitude = fields.Float(digits=(10, 7))
    longitude = fields.Float(digits=(10, 7))
    accuracy = fields.Float()
    distance = fields.Float()
    face_distance = fields.Float(digits=(4, 3))
    ip_address = fields.Char()
    user_agent = fields.Char()
    device_id = fields.Many2one('bs.hr.ess.device')
    attendance_id = fields.Many2one('hr.attendance', ondelete='set null')

    @api.autovacuum
    def _gc_old_attempts(self):
        self.sudo().search([('create_date', '<', fields.Datetime.now() - timedelta(days=180))]).unlink()


class BsHrAttendanceQrUse(models.Model):
    """Prevents the same employee from replaying the same QR code."""
    _name = 'bs.hr.attendance.qr.use'
    _description = 'ESS QR code use'

    employee_id = fields.Many2one('hr.employee', required=True, ondelete='cascade')
    location_id = fields.Many2one('hr.work.location', required=True, ondelete='cascade')
    step = fields.Integer(required=True)

    _uniq_use = models.Constraint('UNIQUE(employee_id, location_id, step)', 'This QR code was already used.')

    @api.autovacuum
    def _gc_old_uses(self):
        self.sudo().search([('create_date', '<', fields.Datetime.now() - timedelta(days=2))]).unlink()
