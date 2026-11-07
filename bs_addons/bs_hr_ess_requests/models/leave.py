from datetime import datetime, time, timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Domain

ACTIVE_LEAVE_STATES = ('confirm', 'validate1', 'validate')


class BsHrLeaveBlackout(models.Model):
    _name = 'bs.hr.leave.blackout'
    _description = 'ESS Leave Blackout Period'
    _order = 'date_from desc'

    name = fields.Char(required=True, translate=True)
    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    leave_type_ids = fields.Many2many('hr.leave.type', string='Time off types',
                                      help='Leave empty to block every type of time off.')
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company,
                                 help='Leave empty to apply to all companies.')
    work_location_ids = fields.Many2many('hr.work.location', string='Branches',
                                         help='Leave empty to apply to all branches.')
    active = fields.Boolean(default=True)

    _check_dates = models.Constraint('CHECK(date_to >= date_from)', 'The end date must be after the start date.')

    @api.model
    def _ess_find(self, employee, leave_type, date_from, date_to):
        domain = Domain([
            ('date_from', '<=', date_to), ('date_to', '>=', date_from),
            ('company_id', 'in', [False, employee.company_id.id]),
        ])
        blackouts = self.sudo().search(domain)
        return blackouts.filtered(
            lambda b: (not b.leave_type_ids or leave_type in b.leave_type_ids)
            and (not b.work_location_ids or employee.work_location_id in b.work_location_ids))


class BsHrLeaveRequest(models.Model):
    _name = 'bs.hr.leave.request'
    _description = 'Time Off Request'
    _inherit = ['bs.hr.request.mixin']
    _ess_sequence_code = 'bs.hr.leave.request'

    leave_type_id = fields.Many2one('hr.leave.type', string='Time off type', required=True, tracking=True)
    date_from = fields.Date(string='From', required=True, tracking=True)
    date_to = fields.Date(string='To', required=True, tracking=True)
    number_of_days = fields.Float(string='Days', readonly=True, copy=False)
    need_ticket = fields.Boolean(string='Travel ticket needed')
    need_exit_reentry = fields.Boolean(string='Exit / re-entry visa needed')
    advance_salary = fields.Boolean(string='Salary in advance')
    leave_id = fields.Many2one('hr.leave', string='Time off', readonly=True, copy=False)
    leave_state = fields.Selection(related='leave_id.state', string='Time off status')
    return_state = fields.Selection([
        ('none', 'Not checked'),
        ('returned', 'Returned'),
        ('late', 'Late return'),
    ], default='none', required=True, copy=False, tracking=True, string='Return to work')
    conflict_warning = fields.Text(readonly=True, copy=False)
    support_document = fields.Boolean(related='leave_type_id.support_document')

    _check_dates = models.Constraint('CHECK(date_to >= date_from)', 'The end date must be after the start date.')

    # ------------------------------------------------------------------
    # request hooks
    # ------------------------------------------------------------------
    def _ess_attachments(self):
        self.ensure_one()
        return self.env['ir.attachment'].sudo().search([('res_model', '=', self._name), ('res_id', '=', self.id)])

    def _ess_check_submit(self):
        for rec in self:
            employee = rec.employee_id.sudo()
            if rec.leave_type_id.support_document and not rec._ess_attachments():
                raise ValidationError(self.env._(
                    'A supporting document is required for %s.', rec.leave_type_id.name))
            blackouts = self.env['bs.hr.leave.blackout']._ess_find(
                employee, rec.leave_type_id, rec.date_from, rec.date_to)
            if blackouts:
                raise ValidationError(self.env._(
                    'Time off is not allowed during this period: %s.', ', '.join(blackouts.mapped('name'))))
            rec.conflict_warning = rec._ess_conflict_warning()
            rec._ess_create_leave()

    def _ess_conflict_warning(self):
        self.ensure_one()
        employee = self.employee_id.sudo()
        limit = employee.company_id.ess_leave_max_concurrent
        if not limit or not employee.work_location_id:
            return False
        start = datetime.combine(self.date_from, time.min)
        stop = datetime.combine(self.date_to, time.max)
        domain = [
            ('employee_id', '!=', employee.id),
            ('employee_id.work_location_id', '=', employee.work_location_id.id),
            ('state', 'in', ACTIVE_LEAVE_STATES),
            ('date_from', '<=', stop), ('date_to', '>=', start),
        ]
        if employee.job_id:
            domain.append(('employee_id.job_id', '=', employee.job_id.id))
        colleagues = self.env['hr.leave'].sudo().search(domain).employee_id
        if len(colleagues) >= limit:
            return self.env._(
                '%(count)s colleague(s) of the same branch and job are already on leave during this period: '
                '%(names)s', count=len(colleagues), names=', '.join(colleagues.mapped('name')))
        return False

    def _ess_create_leave(self):
        """Create the hr.leave right away so Odoo checks balance and overlaps."""
        self.ensure_one()
        if self.leave_id and self.leave_id.state in ACTIVE_LEAVE_STATES:
            return self.leave_id
        # leave_fast_create: the ESS engine owns the approval, so skip Odoo's native auto-approval
        # (no_validation types) and approval activities. Balance (_check_validity) and overlap
        # (_check_date) checks still run.
        Leave = self.env['hr.leave'].sudo().with_context(
            leave_fast_create=True, mail_activity_automation_skip=True, mail_create_nosubscribe=True)
        try:
            leave = Leave.create({
                'employee_id': self.employee_id.id,
                'holiday_status_id': self.leave_type_id.id,
                'request_date_from': self.date_from,
                'request_date_to': self.date_to,
                'private_name': self.employee_note or self.name,
            })
        except UserError as e:
            raise ValidationError(e.args[0]) from e
        for attachment in self._ess_attachments():
            attachment.copy({'res_model': 'hr.leave', 'res_id': leave.id})
        self.write({'leave_id': leave.id, 'number_of_days': leave.number_of_days})
        return leave

    def _ess_on_approved(self):
        for rec in self:
            leave = rec.leave_id.sudo()
            if not leave:
                leave = rec._ess_create_leave().sudo()
            if leave.state in ('confirm', 'validate1'):
                try:
                    leave._action_validate(check_state=False)
                except UserError as e:
                    raise ValidationError(e.args[0]) from e
            rec.number_of_days = leave.number_of_days

    def _ess_on_rejected(self):
        for rec in self:
            leave = rec.leave_id.sudo()
            if leave and leave.state in ACTIVE_LEAVE_STATES:
                leave.action_refuse()

    def _ess_on_cancelled(self):
        for rec in self:
            leave = rec.leave_id.sudo()
            if leave and leave.state in ACTIVE_LEAVE_STATES:
                leave._force_cancel(rec.rejection_reason or self.env._('Request %s cancelled', rec.name),
                                    'mail.mt_note', notify_responsibles=False)

    def _ess_portal_summary(self):
        self.ensure_one()
        from_txt = fields.Date.to_string(self.date_from) if self.date_from else ''
        to_txt = fields.Date.to_string(self.date_to) if self.date_to else ''
        days = (' · %g %s' % (self.number_of_days, self.env._('days'))) if self.number_of_days else ''
        return '%s · %s → %s%s' % (self.leave_type_id.name or '', from_txt, to_txt, days)

    def _ess_portal_detail_fields(self):
        return ['leave_type_id', 'date_from', 'date_to', 'number_of_days', 'need_ticket',
                'need_exit_reentry', 'advance_salary', 'conflict_warning', 'return_state']

    # ------------------------------------------------------------------
    # return from leave
    # ------------------------------------------------------------------
    @api.model
    def _cron_check_return(self, grace_days=1):
        """Check that employees came back after their leave.

        Runs daily: requests whose expected return day (date_to + 1) is over by
        ``grace_days`` are marked returned when an attendance exists after the leave,
        late otherwise (manager notified).
        """
        today = fields.Date.context_today(self)
        limit = today - timedelta(days=grace_days + 1)
        requests = self.sudo().search([
            ('state', 'in', ('approved', 'done')), ('return_state', '=', 'none'),
            ('date_to', '<=', limit), ('date_to', '>=', limit - timedelta(days=30)),
        ])
        if not requests or 'hr.attendance' not in self.env:
            return
        Attendance = self.env['hr.attendance'].sudo()
        for rec in requests:
            after = datetime.combine(rec.date_to + timedelta(days=1), time.min)
            returned = Attendance.search_count([('employee_id', '=', rec.employee_id.id),
                                                ('check_in', '>=', after)], limit=1)
            if returned:
                rec.return_state = 'returned'
                continue
            rec.return_state = 'late'
            rec._ess_notify_late_return()

    def _ess_notify_late_return(self):
        for rec in self:
            employee = rec.employee_id.sudo()
            manager_user = employee.parent_id.user_id or employee.work_location_id.ess_manager_id
            summary = self.env._('%(employee)s did not return from leave (%(ref)s)',
                                 employee=employee.name, ref=rec.name)
            if manager_user and not manager_user.share:
                rec.sudo().activity_schedule('mail.mail_activity_data_todo', user_id=manager_user.id,
                                             summary=summary)
            elif manager_user:
                rec.sudo().message_notify(partner_ids=manager_user.partner_id.ids, subject=summary, body=summary)
            else:
                rec.sudo().message_post(body=summary, subtype_xmlid='mail.mt_note')

    def action_open_leave(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'hr.leave', 'res_id': self.leave_id.id,
                'view_mode': 'form'}
