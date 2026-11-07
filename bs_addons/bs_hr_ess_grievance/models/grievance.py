import hmac as _hmac
import logging
import secrets
from datetime import timedelta

from markupsafe import Markup

from odoo import SUPERUSER_ID, api, fields, models, tools
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)

GRIEVANCE_TYPES = [
    ('complaint_colleague', 'Complaint about a colleague'),
    ('complaint_supervisor', 'Complaint about a supervisor'),
    ('appeal_decision', 'Appeal against a decision'),
    ('whistleblowing', 'Whistleblowing (report a violation)'),
    ('suggestion', 'Suggestion'),
    ('appeal_disciplinary', 'Appeal against a disciplinary action'),
    ('appeal_appraisal', 'Appeal against an appraisal'),
]
# types an employee can choose on the portal "new" form (appeals of a record
# are created from the record itself)
PORTAL_TYPES = ('complaint_colleague', 'complaint_supervisor', 'appeal_decision', 'whistleblowing', 'suggestion')

GRIEVANCE_STATES = [
    ('received', 'Received'),
    ('investigating', 'Under investigation'),
    ('resolved', 'Resolved'),
    ('rejected', 'Rejected'),
    ('appealed', 'Appealed'),
    ('closed', 'Closed'),
]
DECIDED_STATES = ('resolved', 'rejected')
HMAC_SCOPE = 'bs_hr_ess_grievance.owner'


class BsHrGrievance(models.Model):
    """Complaint / grievance / whistleblowing report.

    Confidentiality rules:

    * NOT a ``mail.thread``: a chatter would expose the author (followers,
      ``author_id`` of messages, tracking values).
    * anonymous cases never store ``employee_id``; the author is recognised
      in the portal only through ``owner_hash`` (HMAC of the employee id with
      the database secret), which cannot be reversed by committee members.
    * everything written on behalf of the author (creation, replies, appeal)
      runs as the superuser so that ``create_uid`` / ``write_uid`` never point
      to the author's user.
    * only the grievance committee has an ACL, and a record rule hides the
      case from the committee members who are a party (``excluded_user_ids``).
    """
    _name = 'bs.hr.grievance'
    _description = 'Grievance'
    _order = 'id desc'

    name = fields.Char(string='Reference', default='/', readonly=True, copy=False, index='trigram')
    grievance_type = fields.Selection(GRIEVANCE_TYPES, string='Type', required=True, default='complaint_colleague',
                                      index=True)
    subject = fields.Char(required=True)
    description = fields.Text(required=True)
    anonymous = fields.Boolean(readonly=True, help='The identity of the author is not stored.')
    employee_id = fields.Many2one('hr.employee', string='Author', readonly=True, index=True,
                                  help='Empty for anonymous cases.')
    author_display = fields.Char(compute='_compute_author_display', string='Submitted by')
    owner_hash = fields.Char(readonly=True, index=True, copy=False, groups='base.group_system')
    tracking_code = fields.Char(readonly=True, index=True, copy=False, groups='base.group_system')
    against_employee_ids = fields.Many2many('hr.employee', 'bs_hr_grievance_against_rel', 'grievance_id',
                                            'employee_id', string='Concerned employees')
    work_location_id = fields.Many2one('hr.work.location', string='Branch', index=True,
                                       help='Branch concerned. For anonymous cases it is never the '
                                            "author's branch unless a concerned employee works there.")
    company_id = fields.Many2one('res.company', required=True, index=True, default=lambda self: self.env.company)
    related_res_model = fields.Char(string='Related document model', readonly=True, index=True)
    related_res_id = fields.Many2oneReference(model_field='related_res_model', string='Related document ID',
                                              readonly=True)
    related_ref = fields.Char(compute='_compute_related_ref', string='Related document')
    state = fields.Selection(GRIEVANCE_STATES, default='received', required=True, index=True, readonly=True)
    level = fields.Selection([('1', 'Level 1'), ('2', 'Level 2 (appeal)')], default='1', required=True,
                             readonly=True)
    handler_ids = fields.Many2many('res.users', 'bs_hr_grievance_handler_rel', 'grievance_id', 'user_id',
                                   string='Handled by', domain=[('share', '=', False)])
    excluded_user_ids = fields.Many2many('res.users', 'bs_hr_grievance_excluded_rel', 'grievance_id', 'user_id',
                                         string='Excluded users', readonly=True,
                                         help='Users who are a party (concerned employees, their managers, '
                                              'their branch manager, the decision maker): they can never '
                                              'see the case.')
    resolution = fields.Text()
    decision_date = fields.Datetime(readonly=True, copy=False)
    appeal_deadline = fields.Date(compute='_compute_appeal_deadline', string='Appeal until')
    appeal_reason = fields.Text(readonly=True)
    appeal_date = fields.Datetime(readonly=True, copy=False)
    message_ids = fields.One2many('bs.hr.grievance.message', 'grievance_id', string='Messages')
    access_log_ids = fields.One2many('bs.hr.grievance.access.log', 'grievance_id', string='Access log',
                                     readonly=True)

    # ------------------------------------------------------------------
    # computes
    # ------------------------------------------------------------------
    @api.depends('anonymous', 'employee_id')
    def _compute_author_display(self):
        for rec in self:
            rec.author_display = self.env._('Anonymous') if rec.anonymous else rec.employee_id.name

    @api.depends('related_res_model', 'related_res_id')
    def _compute_related_ref(self):
        for rec in self:
            ref = ''
            if rec.related_res_model in self.env and rec.related_res_id:
                ref = self.env[rec.related_res_model].sudo().browse(rec.related_res_id).exists().display_name or ''
            rec.related_ref = ref

    @api.depends('decision_date', 'company_id.ess_appeal_days')
    def _compute_appeal_deadline(self):
        for rec in self:
            if rec.decision_date:
                days = rec.company_id.ess_appeal_days or 15
                rec.appeal_deadline = rec.decision_date.date() + timedelta(days=days)
            else:
                rec.appeal_deadline = False

    # ------------------------------------------------------------------
    # identity helpers
    # ------------------------------------------------------------------
    @api.model
    def _owner_hash(self, employee):
        return tools.hmac(self.env(su=True), HMAC_SCOPE, employee.id)

    def _is_owner(self, employee):
        self.ensure_one()
        if not employee:
            return False
        stored = self.sudo().owner_hash or ''
        return _hmac.compare_digest(stored, self._owner_hash(employee))

    @api.model
    def _ess_search_owned(self, employee, domain=None):
        """Every case submitted by ``employee`` (anonymous ones included), sudo."""
        return self.sudo().search([('owner_hash', '=', self._owner_hash(employee))] + (domain or []))

    @api.model
    def _new_tracking_code(self):
        raw = secrets.token_hex(5).upper()
        return '%s-%s' % (raw[:5], raw[5:])

    # ------------------------------------------------------------------
    # routing
    # ------------------------------------------------------------------
    def _compute_excluded_users(self):
        """Users that must never see the case: the concerned employees, their
        managers (direct manager, department manager, coach), the branch manager
        of their branch, and the parties declared by the related document."""
        self.ensure_one()
        Users = self.env['res.users']
        excluded = Users
        for emp in self.against_employee_ids.sudo():
            excluded |= emp.user_id | emp.parent_id.user_id | emp.coach_id.user_id \
                | emp.department_id.manager_id.user_id | emp.work_location_id.ess_manager_id
        related = self._get_related_record()
        if related and hasattr(related, '_ess_grievance_parties'):
            excluded |= related._ess_grievance_parties()
        # the author is never excluded from his own (non anonymous) case
        if self.employee_id.user_id:
            excluded -= self.employee_id.sudo().user_id
        return excluded

    def _get_related_record(self):
        self.ensure_one()
        if self.related_res_model and self.related_res_model in self.env and self.related_res_id:
            return self.env[self.related_res_model].sudo().browse(self.related_res_id).exists()
        return None

    def _committee_users(self, managers_only=False):
        group = self.env.ref('bs_hr_ess_base.group_ess_grievance_committee')
        users = group.sudo().all_user_ids.filtered(lambda u: u.active and not u.share)
        users = users.filtered(lambda u: not u.company_ids or self.company_id in u.company_ids)
        if managers_only:
            managers = users.filtered(lambda u: u.has_group('bs_hr_ess_base.group_ess_hr_manager'))
            users = managers or users
        return users

    def _route(self, managers_only=False, summary=None):
        for rec in self.sudo():
            excluded = rec._compute_excluded_users()
            handlers = rec._committee_users(managers_only) - excluded
            if not handlers and managers_only:
                handlers = rec._committee_users() - excluded
            if not handlers:
                _logger.warning('Grievance %s: no committee member available (all are parties).', rec.name)
            rec.write({'excluded_user_ids': [(6, 0, excluded.ids)], 'handler_ids': [(6, 0, handlers.ids)]})
            rec._notify_handlers(handlers, summary=summary)

    def _notify_handlers(self, users, summary=None):
        """Free activity + e-mail to the committee members, never with the name of
        an anonymous author. The activity is not linked to the record on purpose
        (a linked activity would subscribe users as followers of a non-thread model)."""
        self.ensure_one()
        if not users:
            return
        rec = self.sudo()
        type_label = dict(rec._fields['grievance_type']._description_selection(rec.env))[rec.grievance_type]
        summary = summary or self.env._('New grievance %(name)s (%(type)s)', name=rec.name, type=type_label)
        url = '%s/odoo/ess-grievances/%s' % (rec.get_base_url(), rec.id)
        author = self.env._('Anonymous') if rec.anonymous else (rec.employee_id.name or '')
        body = Markup('<p>%s</p><ul><li>%s: %s</li><li>%s: %s</li><li>%s: %s</li></ul><p><a href="%s">%s</a></p>') % (
            summary, self.env._('Type'), type_label, self.env._('Subject'), rec.subject,
            self.env._('Submitted by'), author, url, self.env._('Open the case'))
        activity_type = self.env.ref('mail.mail_activity_data_todo', raise_if_not_found=False)
        Activity = self.env['mail.activity'].with_user(SUPERUSER_ID).with_context(mail_activity_quick_update=True)
        for user in users:
            Activity.create({
                'activity_type_id': activity_type.id if activity_type else False,
                'summary': summary, 'note': body, 'user_id': user.id,
                'date_deadline': fields.Date.context_today(rec) + timedelta(days=2),
            })
        self.env['mail.thread'].with_user(SUPERUSER_ID).message_notify(
            partner_ids=users.partner_id.ids, subject=summary, body=body,
            email_layout_xmlid='mail.mail_notification_light')

    # ------------------------------------------------------------------
    # ORM
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('related_res_model'):
                vals['anonymous'] = False
            if vals.get('anonymous'):
                vals['employee_id'] = False
            if vals.get('name', '/') == '/':
                vals['name'] = self.env['ir.sequence'].sudo().next_by_code('bs.hr.grievance') or '/'
        records = super().create(vals_list)
        for rec in records:
            rec_su = rec.sudo()
            if not rec_su.tracking_code:
                rec_su.tracking_code = self._new_tracking_code()
            if not rec_su.owner_hash and rec.employee_id:
                rec_su.owner_hash = self._owner_hash(rec.employee_id)
            if not rec.work_location_id:
                branch = rec.against_employee_ids.sudo().work_location_id[:1]
                if not branch and not rec.anonymous:
                    branch = rec.employee_id.sudo().work_location_id
                if branch:
                    rec_su.work_location_id = branch
        records._route()
        return records

    @api.model
    def _ess_create_for_employee(self, employee, vals):
        """Create a case on behalf of ``employee`` (portal). Runs as superuser so
        that no technical field links the record to the author's user."""
        env_su = self.with_user(SUPERUSER_ID).with_context(lang=employee.user_id.lang or self.env.lang)
        anonymous = bool(vals.get('anonymous')) and not vals.get('related_res_model')
        rec = env_su.create({
            **vals,
            'anonymous': anonymous,
            'employee_id': False if anonymous else employee.id,
            'company_id': employee.company_id.id,
            'owner_hash': self._owner_hash(employee),
        })
        return rec

    def write(self, vals):
        protected = {'employee_id', 'anonymous', 'owner_hash', 'tracking_code'}
        if protected & set(vals) and not self.env.su:
            raise AccessError(self.env._('The author of a grievance cannot be changed.'))
        return super().write(vals)

    def unlink(self):
        if not self.env.su:
            raise UserError(self.env._('Grievances cannot be deleted; close them instead.'))
        return super().unlink()

    def web_read(self, specification):
        result = super().web_read(specification)
        if len(self) == 1 and isinstance(self.id, int) and not self.env.su:
            self._log_access('read')
        return result
    # opening the form writes the access log: never use a read-only cursor
    web_read._readonly = False

    def _log_access(self, action, user=None):
        user = user or self.env.user
        self.env['bs.hr.grievance.access.log'].sudo().create([{
            'grievance_id': rec.id, 'user_id': user.id, 'action': action,
        } for rec in self])

    # ------------------------------------------------------------------
    # workflow (committee)
    # ------------------------------------------------------------------
    def _check_committee(self):
        if not self.env.su and not self.env.user.has_group('bs_hr_ess_base.group_ess_grievance_committee'):
            raise AccessError(self.env._('Only the grievance committee can process grievances.'))

    def _set_state(self, state, extra=None):
        for rec in self:
            vals = {'state': state, **(extra or {})}
            rec.write(vals)
            rec._log_access(state)

    def action_start_investigation(self):
        self._check_committee()
        if any(rec.state not in ('received', 'appealed') for rec in self):
            raise UserError(self.env._('Only received or appealed cases can be investigated.'))
        self._set_state('investigating')
        self._notify_author(self.env._('Your case is under investigation.'))
        return True

    def action_resolve(self):
        self._check_committee()
        for rec in self:
            if rec.state not in ('received', 'investigating', 'appealed'):
                raise UserError(self.env._('This case cannot be resolved in its current state.'))
            if not (rec.resolution or '').strip():
                raise UserError(self.env._('Please write the resolution before resolving the case.'))
        self._set_state('resolved', {'decision_date': fields.Datetime.now()})
        self._apply_decision_on_related('resolved')
        self._close_activities()
        self._notify_author(self.env._('A decision has been taken on your case.'))
        return True

    def action_reject(self):
        self._check_committee()
        for rec in self:
            if rec.state not in ('received', 'investigating', 'appealed'):
                raise UserError(self.env._('This case cannot be rejected in its current state.'))
            if not (rec.resolution or '').strip():
                raise UserError(self.env._('Please explain the reason of the rejection in the resolution.'))
        self._set_state('rejected', {'decision_date': fields.Datetime.now()})
        self._apply_decision_on_related('rejected')
        self._close_activities()
        self._notify_author(self.env._('A decision has been taken on your case.'))
        return True

    def action_close(self):
        self._check_committee()
        if any(rec.state not in DECIDED_STATES for rec in self):
            raise UserError(self.env._('Only resolved or rejected cases can be closed.'))
        self._set_state('closed')
        self._close_activities()
        return True

    def _apply_decision_on_related(self, decision):
        for rec in self:
            related = rec._get_related_record()
            if related and hasattr(related, '_ess_on_grievance_decision'):
                related._ess_on_grievance_decision(rec, decision)

    def _close_activities(self):
        """Remove the pending committee activities of the cases."""
        Activity = self.env['mail.activity'].sudo()
        for rec in self:
            Activity.search([('res_model', '=', False), ('summary', 'ilike', rec.name)]).unlink()

    # ------------------------------------------------------------------
    # author side (portal)
    # ------------------------------------------------------------------
    def _can_appeal(self, today=None):
        self.ensure_one()
        today = today or fields.Date.context_today(self)
        return self.state in DECIDED_STATES and self.level == '1' \
            and self.grievance_type not in ('appeal_disciplinary', 'suggestion') \
            and bool(self.appeal_deadline) and today <= self.appeal_deadline

    def _ess_appeal(self, reason):
        """Second level appeal by the author (call on the superuser record)."""
        self.ensure_one()
        if not self._can_appeal():
            raise UserError(self.env._('The appeal period is over or this case cannot be appealed.'))
        if not (reason or '').strip():
            raise UserError(self.env._('Please explain why you appeal the decision.'))
        rec = self.with_user(SUPERUSER_ID)
        rec.write({'state': 'appealed', 'level': '2', 'appeal_reason': reason.strip(),
                   'appeal_date': fields.Datetime.now()})
        rec._route(managers_only=True, summary=self.env._('Appeal (level 2) on grievance %s', rec.name))
        rec._log_access('appealed', user=self.env.ref('base.user_root'))
        return rec

    def _ess_post_employee_message(self, body):
        self.ensure_one()
        return self.env['bs.hr.grievance.message'].with_user(SUPERUSER_ID).create({
            'grievance_id': self.id, 'author_type': 'employee', 'body': body.strip()})

    def _notify_author(self, text):
        """Portal notification for non anonymous authors only (an anonymous
        author follows the case in the portal / with the tracking code)."""
        Notification = self.env['bs.hr.notification'].sudo()
        for rec in self.sudo():
            if rec.anonymous or not rec.employee_id:
                continue
            Notification.create({
                'employee_id': rec.employee_id.id, 'title': '%s - %s' % (rec.name, rec.subject),
                'body': text, 'url': '/my/hr/grievance/%s' % rec.id,
                'res_model': rec._name, 'res_id': rec.id,
            })


class BsHrGrievanceMessage(models.Model):
    _name = 'bs.hr.grievance.message'
    _description = 'Grievance message'
    _order = 'date, id'

    grievance_id = fields.Many2one('bs.hr.grievance', required=True, ondelete='cascade', index=True)
    author_type = fields.Selection([('employee', 'Employee'), ('committee', 'Committee')], required=True,
                                   default='committee')
    author_user_id = fields.Many2one('res.users', string='Committee member', readonly=True,
                                     help='Only set for committee messages; never shown to the employee.')
    body = fields.Text(required=True)
    date = fields.Datetime(default=fields.Datetime.now, required=True, readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not self.env.su:
                vals['author_type'] = 'committee'
            if vals.get('author_type', 'committee') == 'committee':
                vals['author_user_id'] = self.env.uid
            else:
                vals['author_user_id'] = False
        messages = super().create(vals_list)
        for msg in messages.filtered(lambda m: m.author_type == 'committee'):
            msg.grievance_id._notify_author(self.env._('The committee replied to your case.'))
            msg.grievance_id._log_access('message')
        return messages

    def write(self, vals):
        if not self.env.su:
            raise UserError(self.env._('Messages cannot be modified.'))
        return super().write(vals)


class BsHrGrievanceAccessLog(models.Model):
    _name = 'bs.hr.grievance.access.log'
    _description = 'Grievance access log'
    _order = 'date desc, id desc'

    grievance_id = fields.Many2one('bs.hr.grievance', required=True, ondelete='cascade', index=True)
    user_id = fields.Many2one('res.users', required=True, readonly=True)
    date = fields.Datetime(default=fields.Datetime.now, required=True, readonly=True)
    action = fields.Selection([
        ('read', 'Opened'),
        ('message', 'Message'),
        ('investigating', 'Investigation started'),
        ('resolved', 'Resolved'),
        ('rejected', 'Rejected'),
        ('appealed', 'Appealed'),
        ('closed', 'Closed'),
    ], required=True, readonly=True)
