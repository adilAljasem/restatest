from datetime import timedelta

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Domain



class BsHrContractRenewal(models.Model):
    _name = 'bs.hr.contract.renewal'
    _description = 'Contract Renewal'
    _inherit = ['bs.hr.request.mixin']
    _ess_sequence_code = 'bs.hr.contract.renewal'

    version_id = fields.Many2one('hr.version', string='Current contract', readonly=True, copy=False,
                                 ondelete='set null')
    contract_date_start = fields.Date(string='Current contract start', readonly=True)
    contract_date_end = fields.Date(string='Current contract end', readonly=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    current_wage = fields.Monetary(string='Current wage', readonly=True)
    manager_decision = fields.Selection([
        ('renew', 'Renew'),
        ('not_renew', 'Do not renew'),
        ('modify', 'Renew with changes'),
    ], string='Decision', default='renew', tracking=True)
    new_contract_date_end = fields.Date(string='New contract end', tracking=True)
    new_wage = fields.Monetary(string='New wage', tracking=True)
    notes = fields.Text(string='Decision notes')
    new_version_id = fields.Many2one('hr.version', string='New contract', readonly=True, copy=False,
                                     ondelete='set null')
    qiwa_state = fields.Selection([
        ('pending', 'To document'),
        ('documented', 'Documented'),
    ], string='Qiwa', default='pending', tracking=True,
        help='Documentation of the contract on the Qiwa platform (manual for now).')
    qiwa_number = fields.Char(string='Qiwa contract number', tracking=True)
    employee_ack_date = fields.Datetime(string='Acknowledged by employee on', readonly=True, copy=False)

    @api.constrains('new_contract_date_end', 'contract_date_end')
    def _check_new_end(self):
        for rec in self:
            if rec.new_contract_date_end and rec.contract_date_end \
                    and rec.new_contract_date_end <= rec.contract_date_end:
                raise ValidationError(self.env._('The new end date must be after the current contract end.'))

    @api.onchange('employee_id')
    def _onchange_employee_id(self):
        for rec in self:
            rec._ess_load_contract()

    def _ess_load_contract(self):
        for rec in self:
            version = rec.employee_id.sudo().version_id
            rec.version_id = version
            rec.contract_date_start = version.contract_date_start
            rec.contract_date_end = version.contract_date_end
            rec.current_wage = version.wage

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records.filtered(lambda r: not r.version_id or not r.contract_date_start)._ess_load_contract()
        return records

    # ------------------------------------------------------------------
    # security: the employee is the subject, not the requester
    # ------------------------------------------------------------------
    def action_cancel(self):
        if not self.env.user.has_group('bs_hr_ess_base.group_ess_hr_officer'):
            raise UserError(self.env._('Only Human Resources can cancel a contract renewal.'))
        return super().action_cancel()

    def _ess_notify_employee(self, title, body):
        # The generic "request approved / rejected" messages make no sense for the employee: the
        # renewal is an internal workflow. Only the explicit acknowledgement invitation is sent.
        if not self.env.context.get('ess_contract_notify'):
            return
        return super()._ess_notify_employee(title, body)

    def _ess_on_submitted(self):
        for rec in self:
            if not rec._ess_lines():
                raise UserError(self.env._('No approver found for the contract renewal of %s.',
                                           rec.employee_id.name))

    # ------------------------------------------------------------------
    # request hooks
    # ------------------------------------------------------------------
    def _ess_portal_approval_fields(self):
        return ['manager_decision', 'new_contract_date_end', 'new_wage', 'notes']

    def _ess_portal_detail_fields(self):
        # shown to the employee on the generic page and to approvers: no decision before it is final
        return ['contract_date_start', 'contract_date_end']

    def _ess_portal_summary(self):
        self.ensure_one()
        return self.env._('Contract ending %s', self.contract_date_end or '-')

    def _ess_portal_url(self):
        self.ensure_one()
        return '/my/hr/contract'

    def _ess_check_submit(self):
        for rec in self:
            if not rec.version_id:
                raise UserError(self.env._('%s has no current contract.', rec.employee_id.name))

    def _ess_on_approved(self):
        for rec in self:
            if rec.manager_decision in ('renew', 'modify'):
                rec._ess_create_new_version()
            else:
                rec._ess_start_end_of_service()

    def _ess_default_new_end(self):
        """Same duration as the current contract (1 year when unknown)."""
        self.ensure_one()
        if self.contract_date_start and self.contract_date_end:
            duration = self.contract_date_end - self.contract_date_start
            return self.contract_date_end + duration + timedelta(days=1)
        return (self.contract_date_end or fields.Date.context_today(self)) + timedelta(days=365)

    def _ess_create_new_version(self):
        self.ensure_one()
        if not self.contract_date_end:
            raise UserError(self.env._('The current contract has no end date: nothing to renew.'))
        employee = self.employee_id.sudo()
        start = self.contract_date_end + timedelta(days=1)
        new_end = self.new_contract_date_end or self._ess_default_new_end()
        vals = {
            'date_version': start,
            'contract_date_start': start,
            'contract_date_end': new_end,
        }
        if self.new_wage:
            vals['wage'] = self.new_wage
        version = employee.create_version(vals)
        self.sudo().write({'new_version_id': version.id, 'new_contract_date_end': new_end, 'state': 'done'})
        self.sudo().message_post(body=Markup(self.env._('New contract created from %(start)s to %(end)s.')) % {
            'start': start, 'end': new_end}, subtype_xmlid='mail.mt_note')
        lang_env = self._ess_lang_env()
        self.with_context(ess_contract_notify=True)._ess_notify_employee(
            lang_env._('New contract to acknowledge'),
            lang_env._('Your contract was renewed until %s. Please open it and acknowledge it.', new_end))
        return version

    def _ess_start_end_of_service(self):
        self.ensure_one()
        hr_users = self.env.ref('bs_hr_ess_base.group_ess_hr_officer').sudo().all_user_ids.filtered(
            lambda u: u.active and not u.share and self.company_id in u.company_ids)
        responsible = self.employee_id.sudo().version_id.hr_responsible_id
        users = responsible.filtered(lambda u: u.active and not u.share) or hr_users[:1]
        summary = self.env._('End of service / clearance: %s', self.employee_id.name)
        note = self.env._('The contract will not be renewed. Send the notice (article 75 of the Labor Law: '
                          'at least 60 days), prepare the end of service award and the clearance.')
        for user in users:
            self.sudo().activity_schedule(
                'bs_hr_ess_documents.mail_activity_end_of_service',
                date_deadline=self.contract_date_end or fields.Date.context_today(self),
                summary=summary, note=note, user_id=user.id)
        self.sudo().message_post(body=note, subtype_xmlid='mail.mt_note')
        self.sudo().state = 'done'

    def action_ess_acknowledge(self):
        for rec in self:
            if rec.state != 'done' or not rec.new_version_id:
                raise UserError(self.env._('There is no new contract to acknowledge.'))
            if rec.employee_ack_date:
                continue
            rec.sudo().employee_ack_date = fields.Datetime.now()
            rec.sudo().message_post(
                body=self.env._('The employee acknowledged the new contract.'), subtype_xmlid='mail.mt_note')
        return True

    def action_qiwa_documented(self):
        for rec in self:
            if not rec.qiwa_number:
                raise UserError(self.env._('Enter the Qiwa contract number first.'))
            rec.qiwa_state = 'documented'
        return True

    def action_open_new_version(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'res_model': 'hr.version',
            'res_id': self.new_version_id.id, 'view_mode': 'form',
        }

    # ------------------------------------------------------------------
    # cron
    # ------------------------------------------------------------------
    @api.model
    def _ess_open_states(self):
        return ('draft', 'submitted', 'in_approval', 'approved')

    @api.model
    def _cron_open_renewals(self):
        today = fields.Date.context_today(self)
        Version = self.env['hr.version'].sudo()
        created = self.browse()
        for company in self.env['res.company'].sudo().search([]):
            days = company.ess_contract_alert_days or 60
            versions = Version.search(Domain([
                ('company_id', '=', company.id),
                ('employee_id', '!=', False),
                ('employee_id.active', '=', True),
                ('contract_date_end', '!=', False),
                ('contract_date_end', '>=', today),
                ('contract_date_end', '<=', today + timedelta(days=days)),
            ]))
            for version in versions:
                employee = version.employee_id
                # only the current contract, and only when no later contract already exists
                if employee.version_id != version:
                    continue
                if Version.search_count([('employee_id', '=', employee.id),
                                         ('contract_date_start', '>', version.contract_date_end)]):
                    continue
                if self.sudo().search_count([
                        ('employee_id', '=', employee.id),
                        ('contract_date_end', '=', version.contract_date_end),
                        ('state', 'not in', ('cancelled', 'rejected'))]):
                    continue
                renewal = self.sudo().create({'employee_id': employee.id, 'version_id': version.id})
                renewal._ess_load_contract()
                try:
                    with self.env.cr.savepoint():
                        renewal.action_submit()
                except (UserError, ValidationError):
                    pass
                created |= renewal
        return created
