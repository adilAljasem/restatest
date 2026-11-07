from markupsafe import Markup

from odoo import SUPERUSER_ID, api, fields, models
from odoo.exceptions import AccessError, UserError

REQUEST_STATES = [
    ('draft', 'Draft'),
    ('submitted', 'Submitted'),
    ('in_approval', 'In Approval'),
    ('approved', 'Approved'),
    ('rejected', 'Rejected'),
    ('cancelled', 'Cancelled'),
    ('done', 'Done'),
]
OPEN_STATES = ('submitted', 'in_approval')


class BsHrRequestMixin(models.AbstractModel):
    """Base for every employee request (leave, loan, custody, ...).

    Concrete models only have to:
    * set ``_ess_sequence_code`` (an ``ir.sequence`` code) - optional,
    * implement the hooks ``_ess_on_submitted`` / ``_ess_on_approved`` /
      ``_ess_on_rejected`` / ``_ess_on_cancelled`` when they need side effects,
    * optionally override ``_ess_portal_summary`` and ``_ess_portal_url``.

    The approval chain itself is driven by ``bs.hr.approval.flow`` records.
    """
    _name = 'bs.hr.request.mixin'
    _description = 'ESS Request'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    _ess_sequence_code = 'bs.hr.request'
    _ess_auto_done = False  # move to "done" right after final approval

    name = fields.Char(string='Reference', default='/', readonly=True, copy=False, index='trigram')
    employee_id = fields.Many2one(
        'hr.employee', string='Employee', required=True, index=True, tracking=True,
        default=lambda self: self.env.user.employee_id, check_company=True)
    company_id = fields.Many2one('res.company', related='employee_id.company_id', store=True, index=True)
    department_id = fields.Many2one('hr.department', related='employee_id.department_id', store=True)
    work_location_id = fields.Many2one('hr.work.location', related='employee_id.work_location_id',
                                       store=True, string='Branch')
    state = fields.Selection(REQUEST_STATES, default='draft', required=True, tracking=True,
                             index=True, copy=False)
    submit_date = fields.Datetime(readonly=True, copy=False)
    decision_date = fields.Datetime(readonly=True, copy=False)
    rejection_reason = fields.Text(readonly=True, copy=False)
    employee_note = fields.Text(string='Notes')
    flow_id = fields.Many2one('bs.hr.approval.flow', readonly=True, copy=False)
    approval_line_ids = fields.One2many(
        'bs.hr.approval.line', 'res_id', string='Approvals',
        domain=lambda self: [('res_model', '=', self._name)], readonly=True, copy=False)
    current_approver_ids = fields.Many2many(
        'res.users', compute='_compute_current_approvers', search='_search_current_approvers',
        string='Waiting for')
    can_ess_approve = fields.Boolean(compute='_compute_current_approvers')
    portal_source = fields.Boolean(readonly=True, copy=False,
                                   help='Request submitted from the employee portal')

    # ------------------------------------------------------------------
    # computes
    # ------------------------------------------------------------------
    @api.depends('approval_line_ids.state', 'approval_line_ids.approver_ids')
    @api.depends_context('uid')
    def _compute_current_approvers(self):
        for rec in self:
            line = rec._ess_current_line()
            rec.current_approver_ids = line.approver_ids
            rec.can_ess_approve = bool(line) and rec._ess_user_can_decide(line, self.env.user)

    def _search_current_approvers(self, operator, value):
        lines = self.env['bs.hr.approval.line'].sudo().search([
            ('res_model', '=', self._name), ('state', '=', 'pending'),
            ('approver_ids', operator, value)])
        return [('id', 'in', lines.mapped('res_id'))]

    # ------------------------------------------------------------------
    # ORM
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            if rec.name == '/':
                seq = self.env['ir.sequence'].sudo().next_by_code(rec._ess_sequence_code) \
                    or self.env['ir.sequence'].sudo().next_by_code('bs.hr.request')
                rec.name = seq or '/'
        return records

    def unlink(self):
        if any(rec.state not in ('draft', 'cancelled') for rec in self):
            raise UserError(self.env._('Only draft or cancelled requests can be deleted.'))
        self.env['bs.hr.approval.line'].sudo().search([
            ('res_model', '=', self._name), ('res_id', 'in', self.ids)]).unlink()
        return super().unlink()

    # ------------------------------------------------------------------
    # hooks for concrete models
    # ------------------------------------------------------------------
    def _ess_check_submit(self):
        """Raise UserError when the request cannot be submitted."""

    def _ess_on_submitted(self):
        pass

    def _ess_on_approved(self):
        pass

    def _ess_on_rejected(self):
        pass

    def _ess_on_cancelled(self):
        pass

    def _ess_portal_summary(self):
        self.ensure_one()
        return self.employee_note or ''

    def _ess_portal_url(self):
        self.ensure_one()
        return '/my/hr/request/%s/%s' % (self._name, self.id)

    def _ess_portal_approval_fields(self):
        """Fields the approver may set from the portal while approving.

        Returns a list of field names (selection / char / text / float / date).
        """
        return []

    def _ess_portal_detail_fields(self):
        """Field names displayed on the generic portal detail page."""
        return []

    # ------------------------------------------------------------------
    # approval engine
    # ------------------------------------------------------------------
    def _ess_get_flow(self):
        self.ensure_one()
        flows = self.env['bs.hr.approval.flow'].sudo().search([
            ('res_model', '=', self._name),
            ('company_id', 'in', [False, self.company_id.id]),
        ], order='company_id desc, sequence, id')
        for flow in flows:
            if self._ess_flow_match(flow) and flow._matches(self):
                return flow
        return flows.browse()

    def _ess_flow_match(self, flow):
        """Extra filter for flows (e.g. generic requests filter on the type)."""
        return True

    def _ess_build_lines(self):
        self.ensure_one()
        Line = self.env['bs.hr.approval.line'].sudo()
        flow = self._ess_get_flow()
        vals_list = []
        seq = 0
        steps = flow.step_ids if flow else []
        for step in steps:
            if not step._applies_to(self):
                continue
            approvers = step._resolve_approvers(self)
            if not approvers:
                continue
            seq += 10
            vals_list.append({'step_id': step.id, 'name': step.name, 'approver_ids': [(6, 0, approvers.ids)],
                              'sequence': seq})
        if not vals_list:
            # Safety net: never auto-approve because nobody was found.
            # No flow -> direct manager then HR; flow whose steps were all skipped -> HR managers.
            if not flow:
                fallback = [
                    (self.env._('Direct manager'), self.employee_id.sudo().parent_id.user_id),
                    (self.env._('Human Resources'),
                     self.env.ref('bs_hr_ess_base.group_ess_hr_officer').sudo().all_user_ids),
                ]
            else:
                fallback = [(self.env._('HR Manager'),
                             self.env.ref('bs_hr_ess_base.group_ess_hr_manager').sudo().all_user_ids)]
            for label, users in fallback:
                users = users.filtered(lambda u: u.active and not u.share and u.id != SUPERUSER_ID
                                       and (not self.company_id or self.company_id in u.company_ids)) \
                    - self.employee_id.sudo().user_id
                if users:
                    seq += 10
                    vals_list.append({'name': label, 'approver_ids': [(6, 0, users.ids)], 'sequence': seq})
        common = {
            'res_model': self._name, 'res_id': self.id, 'request_name': self.display_name,
            'employee_id': self.employee_id.id, 'company_id': self.company_id.id,
            'work_location_id': self.work_location_id.id,
        }
        self.flow_id = flow
        return Line.create([{**common, **vals} for vals in vals_list])

    def _ess_lines(self):
        self.ensure_one()
        return self.env['bs.hr.approval.line'].sudo().search([
            ('res_model', '=', self._name), ('res_id', '=', self.id)])

    def _ess_current_line(self):
        self.ensure_one()
        if not self.id:
            return self.env['bs.hr.approval.line']
        return self._ess_lines().filtered(lambda l: l.state == 'pending')[:1]

    def _ess_has_waiting_line(self, after=None):
        lines = self._ess_lines().filtered(lambda l: l.state == 'waiting')
        if after:
            lines = lines.filtered(lambda l: l.sequence > after.sequence)
        return bool(lines)

    def _ess_user_can_decide(self, line, user):
        if not line:
            return False
        if user in line.approver_ids:
            return True
        delegates = self.env['bs.hr.approval.delegation']._get_delegates(line.approver_ids)
        if user in delegates:
            return True
        own = user == self.employee_id.sudo().user_id or self.employee_id in user.sudo().employee_ids
        return user.has_group('bs_hr_ess_base.group_ess_hr_manager') and not own

    def action_submit(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(self.env._('Only draft requests can be submitted.'))
            rec._ess_check_submit()
            rec._ess_lines().unlink()
            rec.write({'state': 'submitted', 'submit_date': fields.Datetime.now(),
                       'rejection_reason': False})
            rec._ess_build_lines()
            rec._ess_on_submitted()
            rec._ess_activate_next()
        return True

    def _ess_activate_next(self):
        for rec in self:
            waiting = rec._ess_lines().filtered(lambda l: l.state == 'waiting').sorted('sequence')
            if not waiting:
                rec._ess_finalize_approved()
                continue
            line = waiting[0]
            line._activate()
            if rec.state != 'in_approval':
                rec.state = 'in_approval'
            rec._ess_notify_approvers(line)

    def _ess_finalize_approved(self):
        for rec in self:
            rec.write({'state': 'approved', 'decision_date': fields.Datetime.now()})
            rec.activity_unlink(['mail.mail_activity_data_todo'])
            rec._ess_on_approved()
            if rec._ess_auto_done and rec.state == 'approved':
                rec.state = 'done'
            rec._ess_notify_employee(
                rec._ess_lang_env()._('Request approved'),
                rec._ess_lang_env()._('Your request %s has been approved.', rec.name))

    def action_ess_approve(self, comment=None, values=None):
        user = self.env.user
        for rec in self:
            line = rec._ess_current_line()
            if not line:
                raise UserError(self.env._('There is nothing to approve on %s.', rec.name))
            if not rec._ess_user_can_decide(line, user):
                raise AccessError(self.env._('You are not an approver of %s.', rec.name))
            if values:
                allowed = set(rec._ess_portal_approval_fields())
                clean = {k: v for k, v in values.items() if k in allowed}
                if clean:
                    rec.sudo().write(clean)
            line.write({'state': 'approved', 'decided_by_id': user.id,
                        'decided_on': fields.Datetime.now(), 'comment': comment or False})
            rec.sudo().message_post(body=Markup('%s <b>%s</b>%s') % (
                self.env._('Approved:'), line.name, Markup('<br/>') + comment if comment else ''))
            rec.activity_feedback(['mail.mail_activity_data_todo'], user_id=user.id)
            rec._ess_activate_next()
        return True

    def action_ess_reject(self, reason=None):
        user = self.env.user
        for rec in self:
            line = rec._ess_current_line()
            if not line:
                raise UserError(self.env._('There is nothing to reject on %s.', rec.name))
            if not rec._ess_user_can_decide(line, user):
                raise AccessError(self.env._('You are not an approver of %s.', rec.name))
            now = fields.Datetime.now()
            line.write({'state': 'rejected', 'decided_by_id': user.id, 'decided_on': now,
                        'comment': reason or False})
            rec._ess_lines().filtered(lambda l: l.state == 'waiting').write({'state': 'cancelled'})
            rec.write({'state': 'rejected', 'decision_date': now, 'rejection_reason': reason or False})
            rec.activity_unlink(['mail.mail_activity_data_todo'])
            rec._ess_on_rejected()
            lang_env = rec._ess_lang_env()
            rec._ess_notify_employee(
                lang_env._('Request rejected'),
                lang_env._('Your request %(name)s has been rejected. %(reason)s', name=rec.name,
                           reason=reason or ''))
        return True

    def action_cancel(self):
        for rec in self:
            if rec.state not in ('draft',) + OPEN_STATES + ('approved',):
                raise UserError(self.env._('This request cannot be cancelled.'))
            if rec.state == 'approved' and not self.env.user.has_group('bs_hr_ess_base.group_ess_hr_officer'):
                raise UserError(self.env._('Approved requests can only be cancelled by HR.'))
            rec._ess_lines().filtered(lambda l: l.state in ('waiting', 'pending')).write({'state': 'cancelled'})
            rec.state = 'cancelled'
            rec.activity_unlink(['mail.mail_activity_data_todo'])
            rec._ess_on_cancelled()
        return True

    def action_reset_draft(self):
        for rec in self:
            if rec.state not in ('rejected', 'cancelled'):
                raise UserError(self.env._('Only rejected or cancelled requests can be reset.'))
            rec._ess_lines().unlink()
            rec.write({'state': 'draft', 'rejection_reason': False})
        return True

    def action_ess_reject_wizard(self):
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Reject'),
            'res_model': 'bs.hr.ess.decision.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_res_model': self._name, 'default_res_ids': ','.join(map(str, self.ids)),
                        'default_decision': 'reject'},
        }

    # ------------------------------------------------------------------
    # notifications
    # ------------------------------------------------------------------
    def _ess_lang_env(self):
        self.ensure_one()
        lang = self.employee_id.sudo().user_id.lang or self.company_id.partner_id.lang or self.env.lang
        return self.with_context(lang=lang).env

    def _ess_notify_employee(self, title, body):
        for rec in self:
            employee = rec.employee_id.sudo()
            self.env['bs.hr.notification'].sudo().create({
                'employee_id': employee.id, 'title': title, 'body': body,
                'url': rec._ess_portal_url(), 'res_model': rec._name, 'res_id': rec.id,
            })
            partner = employee.user_id.partner_id
            rec.sudo().message_post(
                body=body, subject=title,
                partner_ids=partner.ids if partner else [],
                subtype_xmlid='mail.mt_comment' if partner else 'mail.mt_note')

    def _ess_notify_approvers(self, line, escalation=False):
        self.ensure_one()
        summary = self.env._('Approval needed: %s', self.display_name)
        if escalation:
            summary = self.env._('Escalated approval: %s', self.display_name)
        for user in line.approver_ids:
            if user.share:
                employee = user.sudo().employee_ids[:1]
                if employee:
                    self.env['bs.hr.notification'].sudo().create({
                        'employee_id': employee.id, 'title': summary,
                        'body': self._ess_portal_summary() or self.display_name,
                        'url': '/my/hr/team', 'res_model': self._name, 'res_id': self.id,
                    })
                self.sudo().message_notify(partner_ids=user.partner_id.ids, subject=summary,
                                           body=self._ess_portal_summary() or self.display_name)
            else:
                self.sudo().activity_schedule(
                    'mail.mail_activity_data_todo', user_id=user.id, summary=summary,
                    date_deadline=fields.Date.to_date(line.deadline) if line.deadline else fields.Date.today())
