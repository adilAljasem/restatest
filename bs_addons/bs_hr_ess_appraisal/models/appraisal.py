import logging
from collections import defaultdict
from datetime import datetime, time, timedelta

import pytz

from odoo import SUPERUSER_ID, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

_logger = logging.getLogger(__name__)

APPEAL_DAYS = 15
PROBATION_NOTICE_DAYS = 15
DEFAULT_LATE_GRACE = 10

RATINGS = [
    ('exceptional', 'Exceptional'),
    ('exceeds', 'Exceeds expectations'),
    ('meets', 'Meets expectations'),
    ('below', 'Below expectations'),
    ('unsatisfactory', 'Unsatisfactory'),
]
SCORES = [(str(i), str(i)) for i in range(1, 6)]


def rating_for(score):
    if score >= 90:
        return 'exceptional'
    if score >= 75:
        return 'exceeds'
    if score >= 60:
        return 'meets'
    if score >= 40:
        return 'below'
    return 'unsatisfactory'


class BsHrEssAppraisal(models.Model):
    _name = 'bs.hr.ess.appraisal'
    _description = 'Appraisal'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_to desc, id desc'

    name = fields.Char(compute='_compute_name', store=True)
    cycle_id = fields.Many2one('bs.hr.appraisal.cycle', index=True, ondelete='restrict')
    employee_id = fields.Many2one('hr.employee', required=True, index=True, tracking=True)
    manager_id = fields.Many2one('hr.employee', string='Manager', index=True, tracking=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    department_id = fields.Many2one(related='employee_id.department_id', store=True, string='Department')
    work_location_id = fields.Many2one(related='employee_id.work_location_id', store=True, string='Branch')
    template_id = fields.Many2one('bs.hr.appraisal.template', required=True)
    kpi_weight = fields.Float(string='Attendance KPI weight (%)',
                              help='Copied from the template when the appraisal is created.')
    date_from = fields.Date(string='Period start', required=True)
    date_to = fields.Date(string='Period end', required=True)
    deadline = fields.Date(string='Self-review deadline')
    state = fields.Selection([
        ('self_review', 'Self review'),
        ('manager_review', 'Manager review'),
        ('done', 'Done'),
        ('appealed', 'Appealed'),
        ('cancelled', 'Cancelled'),
    ], default='self_review', required=True, tracking=True, index=True)
    line_ids = fields.One2many('bs.hr.ess.appraisal.line', 'appraisal_id', string='Competencies')
    self_comment = fields.Text(string='Employee comment')
    manager_comment = fields.Text(string='Manager comment')
    self_submit_date = fields.Datetime(readonly=True, copy=False)
    done_date = fields.Date(string='Completed on', readonly=True, copy=False)
    employee_ack_date = fields.Datetime(string='Acknowledged on', readonly=True, copy=False)

    # objective KPIs (frozen when the manager review starts or on refresh)
    kpi_date = fields.Datetime(string='KPIs computed on', readonly=True, copy=False)
    expected_days = fields.Integer(readonly=True, copy=False)
    attended_days = fields.Integer(readonly=True, copy=False)
    attendance_rate = fields.Float(string='Attendance rate (%)', readonly=True, copy=False, aggregator='avg')
    late_count = fields.Integer(string='Late arrivals', readonly=True, copy=False)
    disciplinary_count = fields.Integer(string='Disciplinary actions', readonly=True, copy=False)

    competency_score = fields.Float(compute='_compute_scores', store=True, aggregator='avg',
                                    help='Weighted manager scores on a 0-100 scale.')
    self_score = fields.Float(compute='_compute_scores', store=True, aggregator='avg',
                              help='Weighted self scores on a 0-100 scale.')
    final_score = fields.Float(compute='_compute_scores', store=True, aggregator='avg')
    rating = fields.Selection(RATINGS, compute='_compute_scores', store=True)

    # probation
    is_probation = fields.Boolean(string='Probation review', index=True)
    probation_end_date = fields.Date()
    probation_decision = fields.Selection([
        ('confirm', 'Confirm employment'),
        ('extend', 'Extend probation'),
        ('terminate', 'End employment'),
    ], tracking=True)

    # appeal
    appeal_deadline = fields.Date(compute='_compute_appeal')
    can_appeal = fields.Boolean(compute='_compute_appeal')
    appeal_reason = fields.Text(readonly=True, copy=False)
    appeal_date = fields.Datetime(readonly=True, copy=False)
    appeal_res_model = fields.Char(readonly=True, copy=False)
    appeal_res_id = fields.Many2oneReference(model_field='appeal_res_model', readonly=True, copy=False)

    _dates_check = models.Constraint('CHECK(date_from <= date_to)',
                                     'The period start must be before the period end.')

    # ------------------------------------------------------------------
    # computes
    # ------------------------------------------------------------------
    @api.depends('employee_id.name', 'cycle_id.name', 'is_probation', 'date_to')
    def _compute_name(self):
        for rec in self:
            label = rec.cycle_id.name or (self.env._('Probation review') if rec.is_probation
                                           else self.env._('Appraisal'))
            rec.name = '%s - %s' % (rec.employee_id.name or '', label)

    @api.depends('line_ids.weight', 'line_ids.manager_score', 'line_ids.self_score',
                 'kpi_weight', 'attendance_rate')
    def _compute_scores(self):
        for rec in self:
            total = sum(rec.line_ids.mapped('weight'))
            mgr = sum(l.weight * int(l.manager_score or 0) for l in rec.line_ids)
            slf = sum(l.weight * int(l.self_score or 0) for l in rec.line_ids)
            rec.competency_score = round(mgr / (total * 5) * 100, 2) if total else 0.0
            rec.self_score = round(slf / (total * 5) * 100, 2) if total else 0.0
            k = min(max(rec.kpi_weight or 0.0, 0.0), 100.0) / 100.0
            rec.final_score = round(rec.competency_score * (1 - k) + (rec.attendance_rate or 0.0) * k, 2)
            scored = rec.line_ids and all(rec.line_ids.mapped('manager_score'))
            rec.rating = rating_for(rec.final_score) if scored else False

    @api.depends('state', 'done_date')
    def _compute_appeal(self):
        today = fields.Date.context_today(self)
        for rec in self:
            rec.appeal_deadline = rec.done_date + timedelta(days=APPEAL_DAYS) if rec.done_date else False
            rec.can_appeal = bool(rec.state == 'done' and rec.appeal_deadline and today <= rec.appeal_deadline)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        Template = self.env['bs.hr.appraisal.template']
        for vals in vals_list:
            template = Template.browse(vals.get('template_id'))
            if 'kpi_weight' not in vals and template:
                vals['kpi_weight'] = template.kpi_weight
            if not vals.get('line_ids') and template:
                vals['line_ids'] = [fields.Command.create({
                    'competency_id': tl.competency_id.id, 'weight': tl.weight, 'sequence': tl.sequence,
                }) for tl in template.line_ids]
            if vals.get('employee_id') and 'manager_id' not in vals:
                vals['manager_id'] = self.env['hr.employee'].sudo().browse(vals['employee_id']).parent_id.id
            if vals.get('employee_id') and 'company_id' not in vals:
                vals['company_id'] = self.env['hr.employee'].sudo().browse(vals['employee_id']).company_id.id
        return super().create(vals_list)

    _EMPLOYEE_FIELDS = {'self_comment', 'line_ids'}
    _MANAGER_FIELDS = {'manager_comment', 'probation_decision', 'line_ids'}

    def write(self, vals):
        """Internal non-HR users may only edit their part of the review."""
        if not self.env.su and not self.env.user.has_group('bs_hr_ess_base.group_ess_hr_officer'):
            user = self.env.user
            keys = set(vals) - {'message_main_attachment_id', 'activity_ids', 'message_follower_ids'}
            for rec in self:
                if rec.employee_id.user_id == user and rec.state == 'self_review' \
                        and keys <= self._EMPLOYEE_FIELDS:
                    continue
                if rec.manager_id.user_id == user and rec.employee_id.user_id != user \
                        and rec.state == 'manager_review' and keys <= self._MANAGER_FIELDS:
                    continue
                if keys:
                    raise AccessError(self.env._('You cannot modify the appraisal %s.', rec.name))
        return super().write(vals)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _ess_portal_url(self):
        self.ensure_one()
        return '/my/hr/appraisal/%s' % self.id

    def _ess_team_portal_url(self):
        self.ensure_one()
        return '/my/hr/team/appraisal/%s' % self.id

    def _is_hr(self):
        """True for HR officers and for the system itself (cron / OdooBot).

        Portal controllers run ``with_user(user).sudo()``: the identity checks below
        then still apply to the real user.
        """
        return self.env.uid == SUPERUSER_ID or self.env.user.has_group('bs_hr_ess_base.group_ess_hr_officer')

    def _user_is_manager_of(self, user):
        self.ensure_one()
        if self.employee_id.user_id == user:
            return False
        if self.manager_id.user_id == user:
            return True
        manager = self.env['hr.employee']._ess_get_for_user(user)
        return bool(manager) and self.employee_id in manager._ess_team()

    def _ess_notify(self, employee, title, body, url, activity_summary=None, deadline=None):
        """Reach an employee: portal notification and, for internal users, a to-do activity."""
        self.ensure_one()
        employee = employee.sudo()
        if not employee:
            return
        self.env['bs.hr.notification'].sudo().create({
            'employee_id': employee.id, 'title': title, 'body': body, 'url': url,
            'res_model': self._name, 'res_id': self.id,
        })
        user = employee.user_id
        if user and not user.share:
            self.sudo().activity_schedule(
                'mail.mail_activity_data_todo', user_id=user.id,
                summary=activity_summary or title, note=body,
                date_deadline=deadline or fields.Date.context_today(self))
        elif user:
            self.sudo().message_notify(partner_ids=user.partner_id.ids, subject=title, body=body)

    def _ess_notify_self_review(self):
        for rec in self:
            rec._ess_notify(
                rec.employee_id, self.env._('Self review to fill'),
                self.env._('Your appraisal "%s" is open: please fill in your self review.', rec.name),
                rec._ess_portal_url(), deadline=rec.deadline)

    def _ess_close_activities(self, user=None):
        self.sudo().activity_feedback(['mail.mail_activity_data_todo'], user_id=user.id if user else None,
                                      only_automated=False)

    # ------------------------------------------------------------------
    # KPIs
    # ------------------------------------------------------------------
    def _late_grace_minutes(self):
        company = self.company_id or self.env.company
        if 'ess_late_grace_minutes' in company._fields:
            value = company.ess_late_grace_minutes
            return value if value is not None else DEFAULT_LATE_GRACE
        return DEFAULT_LATE_GRACE

    def _kpi_leave_dates(self, employee, date_from, date_to):
        """Dates covered by validated time off (hr_holidays optional)."""
        if 'hr.leave' not in self.env:
            return set()
        leaves = self.env['hr.leave'].sudo().search([
            ('employee_id', '=', employee.id), ('state', '=', 'validate'),
            ('request_date_from', '<=', date_to), ('request_date_to', '>=', date_from),
        ])
        result = set()
        for leave in leaves:
            day = max(leave.request_date_from, date_from)
            while day <= min(leave.request_date_to, date_to):
                result.add(day)
                day += timedelta(days=1)
        return result

    def _compute_kpi_values(self):
        """Return the KPI dict of this appraisal (does not write)."""
        self.ensure_one()
        employee = self.employee_id.sudo()
        calendar = employee.resource_calendar_id or employee.company_id.resource_calendar_id
        today = fields.Date.context_today(self)
        date_from, date_to = self.date_from, min(self.date_to, today)
        values = {'expected_days': 0, 'attended_days': 0, 'attendance_rate': 0.0, 'late_count': 0,
                  'disciplinary_count': self._kpi_disciplinary_count()}
        if not calendar or date_from > date_to:
            return values
        tz = pytz.timezone(employee.tz or calendar.tz or 'UTC')
        start = tz.localize(datetime.combine(date_from, time.min))
        stop = tz.localize(datetime.combine(date_to, time.max))
        intervals = calendar._work_intervals_batch(start, stop, resources=employee.resource_id, tz=tz)
        day_start = {}
        for i_start, _i_stop, _meta in intervals.get(employee.resource_id.id, []):
            day = i_start.astimezone(tz).date()
            if day not in day_start or i_start < day_start[day]:
                day_start[day] = i_start
        for day in self._kpi_leave_dates(employee, date_from, date_to):
            day_start.pop(day, None)
        if not day_start:
            return values
        attendances = self.env['hr.attendance'].sudo().search([
            ('employee_id', '=', employee.id),
            ('check_in', '>=', start.astimezone(pytz.utc).replace(tzinfo=None)),
            ('check_in', '<=', stop.astimezone(pytz.utc).replace(tzinfo=None)),
        ], order='check_in asc')
        first_check_in = defaultdict(lambda: None)
        for att in attendances:
            local = pytz.utc.localize(att.check_in).astimezone(tz)
            day = local.date()
            if first_check_in[day] is None:
                first_check_in[day] = local
        grace = timedelta(minutes=self._late_grace_minutes())
        attended = late = 0
        for day, scheduled in day_start.items():
            check_in = first_check_in.get(day)
            if check_in is None:
                continue
            attended += 1
            if check_in > scheduled + grace:
                late += 1
        values.update({
            'expected_days': len(day_start),
            'attended_days': attended,
            'attendance_rate': round(attended / len(day_start) * 100, 2),
            'late_count': late,
        })
        return values

    def _kpi_disciplinary_count(self):
        if 'bs.hr.disciplinary' not in self.env:
            return 0
        Disciplinary = self.env['bs.hr.disciplinary'].sudo()
        date_field = next((f for f in ('date', 'decision_date', 'date_action') if f in Disciplinary._fields), None)
        domain = [('employee_id', '=', self.employee_id.id)]
        if 'state' in Disciplinary._fields:
            domain.append(('state', 'in', ('confirmed', 'done', 'applied')))
        if date_field:
            domain += [(date_field, '>=', self.date_from), (date_field, '<=', self.date_to)]
        try:
            return Disciplinary.search_count(domain)
        except (ValueError, KeyError):
            _logger.warning('Could not count disciplinary actions for appraisal %s', self.id)
            return 0

    def action_refresh_kpis(self):
        for rec in self:
            if rec.state in ('done', 'appealed', 'cancelled') and not self.env.context.get('force_kpi'):
                raise UserError(self.env._('KPIs of a completed appraisal are frozen.'))
            vals = rec._compute_kpi_values()
            vals['kpi_date'] = fields.Datetime.now()
            rec.sudo().write(vals)
        return True

    # ------------------------------------------------------------------
    # workflow
    # ------------------------------------------------------------------
    def _check_is_employee(self):
        for rec in self:
            if not rec._is_hr() and rec.employee_id.user_id != self.env.user:
                raise AccessError(self.env._('Only the appraised employee can do this.'))

    def _check_is_manager(self):
        for rec in self:
            if rec._is_hr():
                continue
            if not rec._user_is_manager_of(self.env.user):
                raise AccessError(self.env._('Only the manager of the employee can do this.'))

    def action_submit_self_review(self):
        self._check_is_employee()
        for rec in self:
            if rec.state != 'self_review':
                raise UserError(self.env._('The self review of %s is already submitted.', rec.name))
            if not all(rec.line_ids.mapped('self_score')):
                raise ValidationError(self.env._('Please score every competency before submitting.'))
        self.sudo().write({'state': 'manager_review', 'self_submit_date': fields.Datetime.now()})
        for rec in self.sudo():
            rec.with_context(force_kpi=True).action_refresh_kpis()
            rec._ess_close_activities()
            rec._ess_notify(
                rec.manager_id, self.env._('Appraisal to review: %s', rec.employee_id.name),
                self.env._('%s submitted the self review. Please complete the manager review.',
                           rec.employee_id.name),
                rec._ess_team_portal_url(),
                deadline=rec.probation_end_date or fields.Date.context_today(rec) + timedelta(days=7))
        return True

    def action_start_manager_review(self):
        """HR shortcut: skip the self review."""
        if not self._is_hr():
            raise AccessError(self.env._('Only HR can skip the self review.'))
        for rec in self.filtered(lambda r: r.state == 'self_review'):
            rec.write({'state': 'manager_review'})
            rec.action_refresh_kpis()
        return True

    def action_manager_done(self):
        self._check_is_manager()
        for rec in self:
            if rec.state != 'manager_review':
                raise UserError(self.env._('%s is not waiting for the manager review.', rec.name))
            if not rec.line_ids or not all(rec.line_ids.mapped('manager_score')):
                raise ValidationError(self.env._('Please score every competency before completing the review.'))
            if rec.is_probation and not rec.probation_decision:
                raise ValidationError(self.env._('Please take the probation decision.'))
        self.sudo().write({'state': 'done', 'done_date': fields.Date.context_today(self)})
        for rec in self.sudo():
            rec._ess_close_activities()
            rec._ess_notify_employee_result()
        return True

    def _ess_notify_employee_result(self):
        self.ensure_one()
        employee = self.employee_id.sudo()
        rating = dict(self._fields['rating']._description_selection(self.env)).get(self.rating, '')
        body = self.env._('Your appraisal "%(name)s" is complete: %(score)s / 100 (%(rating)s).',
                          name=self.name, score=self.final_score, rating=rating)
        self.env['bs.hr.notification'].sudo().create({
            'employee_id': employee.id, 'title': self.env._('Appraisal result available'), 'body': body,
            'url': self._ess_portal_url(), 'res_model': self._name, 'res_id': self.id,
        })
        if employee.user_id:
            self.sudo().message_notify(partner_ids=employee.user_id.partner_id.ids,
                                       subject=self.env._('Appraisal result available'), body=body)

    def action_acknowledge(self):
        self._check_is_employee()
        for rec in self:
            if rec.state not in ('done', 'appealed'):
                raise UserError(self.env._('Only a completed appraisal can be acknowledged.'))
            if not rec.employee_ack_date:
                rec.sudo().employee_ack_date = fields.Datetime.now()
                rec.sudo().message_post(body=self.env._('Result acknowledged by the employee.'))
        return True

    def action_appeal(self, reason):
        self._check_is_employee()
        if not (reason or '').strip():
            raise ValidationError(self.env._('Please explain why you appeal this appraisal.'))
        for rec in self.sudo():
            if not rec.can_appeal:
                raise UserError(self.env._('The appeal period of this appraisal is over.'))
            rec.write({'state': 'appealed', 'appeal_reason': reason.strip(),
                       'appeal_date': fields.Datetime.now()})
            grievance = rec._ess_create_grievance(reason.strip())
            if grievance:
                rec.write({'appeal_res_model': grievance._name, 'appeal_res_id': grievance.id})
            else:
                rec._ess_appeal_activity_hr()
            rec.message_post(body=self.env._('Appeal filed by the employee: %s', reason.strip()))
        return True

    def _ess_create_grievance(self, reason):
        """Create a grievance when bs_hr_ess_grievance is installed; return it or None."""
        self.ensure_one()
        if 'bs.hr.grievance' not in self.env:
            return None
        Grievance = self.env['bs.hr.grievance'].sudo()
        vals = {
            'grievance_type': 'appeal_appraisal',
            'related_res_model': self._name,
            'related_res_id': self.id,
            'employee_id': self.employee_id.id,
            'subject': self.env._('Appeal on appraisal %s', self.name),
            'description': reason,
        }
        vals = {k: v for k, v in vals.items() if k in Grievance._fields}
        try:
            with self.env.cr.savepoint():
                grievance = Grievance.create(vals)
                if 'action_submit' in dir(grievance) and grievance.state == 'draft':
                    grievance.action_submit()
                return grievance
        except (ValueError, KeyError, UserError, ValidationError) as e:
            _logger.warning('Could not create a grievance for appraisal %s: %s', self.id, e)
            return None

    def _ess_appeal_activity_hr(self):
        self.ensure_one()
        group = self.env.ref('bs_hr_ess_base.group_ess_hr_officer', raise_if_not_found=False)
        users = group.sudo().all_user_ids.filtered(
            lambda u: not u.share and self.company_id in u.company_ids and u.active) if group else False
        summary = self.env._('Appraisal appeal: %s', self.employee_id.name)
        for user in (users or self.env['res.users'])[:5]:
            self.sudo().activity_schedule('mail.mail_activity_data_todo', user_id=user.id,
                                          summary=summary, note=self.appeal_reason,
                                          date_deadline=fields.Date.context_today(self) + timedelta(days=5))

    def action_cancel(self):
        if not self._is_hr():
            raise AccessError(self.env._('Only HR can cancel an appraisal.'))
        self.write({'state': 'cancelled'})
        self._ess_close_activities()

    def action_reset_self_review(self):
        if not self._is_hr():
            raise AccessError(self.env._('Only HR can reopen an appraisal.'))
        self.write({'state': 'self_review', 'done_date': False, 'employee_ack_date': False})

    def action_resolve_appeal(self):
        """HR closes the appeal (after possible score changes)."""
        if not self._is_hr():
            raise AccessError(self.env._('Only HR can close an appeal.'))
        self.filtered(lambda r: r.state == 'appealed').write({'state': 'done'})

    def action_reopen_manager_review(self):
        if not self._is_hr():
            raise AccessError(self.env._('Only HR can reopen an appraisal.'))
        self.write({'state': 'manager_review'})

    # ------------------------------------------------------------------
    # probation
    # ------------------------------------------------------------------
    @api.model
    def _probation_template(self, company):
        Template = self.env['bs.hr.appraisal.template'].sudo()
        return Template.search([('is_probation', '=', True), ('company_id', '=', company.id)], limit=1) \
            or Template.search([('is_probation', '=', True), ('company_id', '=', False)], limit=1)

    @api.model
    def _cron_probation_reviews(self):
        today = fields.Date.context_today(self)
        versions = self.env['hr.version'].sudo().search([
            ('trial_date_end', '>=', today),
            ('trial_date_end', '<=', today + timedelta(days=PROBATION_NOTICE_DAYS)),
            ('employee_id.active', '=', True),
        ])
        created = self.browse()
        for version in versions:
            employee = version.employee_id
            if employee.version_id != version:
                continue
            if self.sudo().search_count([('employee_id', '=', employee.id), ('is_probation', '=', True),
                                         ('state', '!=', 'cancelled')]):
                continue
            template = self._probation_template(employee.company_id)
            if not template:
                _logger.warning('No probation appraisal template: skipped %s', employee.name)
                continue
            end = version.trial_date_end
            start = version.contract_date_start or (end - timedelta(days=90))
            appraisal = self.sudo().create({
                'employee_id': employee.id,
                'manager_id': employee.parent_id.id,
                'company_id': employee.company_id.id,
                'template_id': template.id,
                'is_probation': True,
                'probation_end_date': end,
                'date_from': min(start, end),
                'date_to': end,
                'deadline': end - timedelta(days=5) if end - timedelta(days=5) >= today else end,
            })
            created |= appraisal
            appraisal._ess_notify_self_review()
            manager = employee.parent_id
            if manager:
                appraisal._ess_notify(
                    manager, self.env._('Probation ends for %s', employee.name),
                    self.env._('Decide confirmation before %s', end),
                    appraisal._ess_team_portal_url(),
                    activity_summary=self.env._('Decide confirmation before %s', end), deadline=end)
        return created


class BsHrEssAppraisalLine(models.Model):
    _name = 'bs.hr.ess.appraisal.line'
    _description = 'Appraisal Line'
    _order = 'sequence, id'

    appraisal_id = fields.Many2one('bs.hr.ess.appraisal', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    competency_id = fields.Many2one('bs.hr.appraisal.competency', required=True, ondelete='restrict')
    category = fields.Selection(related='competency_id.category')
    description = fields.Text(related='competency_id.description')
    weight = fields.Float(required=True, default=10.0)
    self_score = fields.Selection(SCORES)
    manager_score = fields.Selection(SCORES)
    comment = fields.Text(string='Manager comment')
    self_comment = fields.Text(string='Employee comment')

    def write(self, vals):
        if not self.env.su and not self.env.user.has_group('bs_hr_ess_base.group_ess_hr_officer'):
            user = self.env.user
            for line in self:
                appraisal = line.appraisal_id
                self_keys = {'self_score', 'self_comment'} & set(vals)
                mgr_keys = {'manager_score', 'comment'} & set(vals)
                other = set(vals) - {'self_score', 'self_comment', 'manager_score', 'comment'}
                if other:
                    raise AccessError(self.env._('Only HR can change the competencies of an appraisal.'))
                if self_keys and (appraisal.state != 'self_review' or appraisal.employee_id.user_id != user):
                    raise AccessError(self.env._('You cannot change the self review.'))
                if mgr_keys and (appraisal.state != 'manager_review' or appraisal.manager_id.user_id != user
                                 or appraisal.employee_id.user_id == user):
                    raise AccessError(self.env._('You cannot change the manager review.'))
        return super().write(vals)
