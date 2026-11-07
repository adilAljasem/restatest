from datetime import timedelta

from markupsafe import Markup

from odoo import api, fields, models
from odoo.fields import Domain

from odoo.addons.bs_hr_ess_base.tools import to_hijri_str

DOC_STATES = [
    ('missing', 'Missing number'),
    ('valid', 'Valid'),
    ('expiring', 'Expiring soon'),
    ('expired', 'Expired'),
]
# stage stored in last_alert_days once the "expired" alert has been sent
EXPIRED_STAGE = -1


class BsHrDocument(models.Model):
    _name = 'bs.hr.document'
    _description = 'Employee Document'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'expiry_date, id'
    _check_company_auto = True

    employee_id = fields.Many2one('hr.employee', required=True, index=True, tracking=True,
                                  ondelete='cascade', check_company=True)
    company_id = fields.Many2one('res.company', related='employee_id.company_id', store=True, index=True)
    department_id = fields.Many2one('hr.department', related='employee_id.department_id', store=True)
    work_location_id = fields.Many2one('hr.work.location', related='employee_id.work_location_id',
                                       store=True, string='Branch')
    type_id = fields.Many2one('bs.hr.document.type', required=True, index=True, tracking=True,
                              string='Document type')
    block_attendance = fields.Boolean(related='type_id.block_attendance')
    number = fields.Char(tracking=True, copy=False)
    issue_date = fields.Date(tracking=True)
    expiry_date = fields.Date(tracking=True, index=True)
    issue_date_hijri = fields.Char(compute='_compute_hijri', string='Issue date (Hijri)')
    expiry_date_hijri = fields.Char(compute='_compute_hijri', string='Expiry date (Hijri)')
    attachment_ids = fields.Many2many('ir.attachment', 'bs_hr_document_attachment_rel', 'document_id',
                                      'attachment_id', string='Files', copy=False)
    state = fields.Selection(DOC_STATES, compute='_compute_state', store=True, index=True, tracking=True)
    days_to_expiry = fields.Integer(compute='_compute_state', store=True, string='Days to expiry')
    last_alert_days = fields.Integer(readonly=True, copy=False,
                                     help='Last alert threshold already sent (-1 = expiry alert sent).')
    currency_id = fields.Many2one('res.currency', related='company_id.currency_id')
    renewal_cost = fields.Monetary(string='Renewal cost', tracking=True,
                                   help='Government fees / insurance premium paid for the last renewal.')
    notes = fields.Text()
    supervisor_alert_date = fields.Date(readonly=True, copy=False,
                                        help='Last day the supervisor was told about a check-in with this '
                                             'expired document.')
    active = fields.Boolean(default=True)
    renewal_ids = fields.One2many('bs.hr.document.renewal', 'document_id', string='Renewals')

    @api.depends('type_id', 'number', 'employee_id')
    def _compute_display_name(self):
        for doc in self:
            label = doc.type_id.name or ''
            if doc.number:
                label = '%s %s' % (label, doc.number)
            doc.display_name = label.strip() or self.env._('Document')

    @api.depends('issue_date', 'expiry_date')
    @api.depends_context('lang')
    def _compute_hijri(self):
        lang = self.env.lang or 'en_US'
        for doc in self:
            doc.issue_date_hijri = to_hijri_str(doc.issue_date, lang) if doc.issue_date else False
            doc.expiry_date_hijri = to_hijri_str(doc.expiry_date, lang) if doc.expiry_date else False

    @api.depends('number', 'expiry_date', 'type_id.alert_days')
    def _compute_state(self):
        today = fields.Date.context_today(self)
        for doc in self:
            days = (doc.expiry_date - today).days if doc.expiry_date else 0
            doc.days_to_expiry = days
            if doc.expiry_date and days < 0:
                doc.state = 'expired'
            elif not doc.number:
                doc.state = 'missing'
            elif doc.expiry_date and doc.type_id and doc.type_id._alert_thresholds() \
                    and days <= doc.type_id._alert_thresholds()[0]:
                doc.state = 'expiring'
            else:
                doc.state = 'valid'

    # ------------------------------------------------------------------
    # ORM
    # ------------------------------------------------------------------
    def write(self, vals):
        if 'expiry_date' in vals:
            vals = dict(vals, last_alert_days=0)
            # a renewed document no longer needs the expiry follow-up
            res = super().write(vals)
            self._ess_close_expiry_activities()
            return res
        return super().write(vals)

    # ------------------------------------------------------------------
    # cron
    # ------------------------------------------------------------------
    @api.model
    def _cron_refresh_states(self):
        """The state depends on today's date: recompute it every day."""
        docs = self.search([('expiry_date', '!=', False)])
        for fname in ('state', 'days_to_expiry'):
            self.env.add_to_compute(self._fields[fname], docs)
        docs._recompute_recordset(['state', 'days_to_expiry'])
        return docs

    @api.model
    def _cron_expiry_alerts(self):
        self._cron_refresh_states()
        docs = self.search([('state', 'in', ('expiring', 'expired')), ('employee_id.active', '=', True)])
        for doc in docs:
            stage = doc._ess_alert_stage()
            if stage is None:
                continue
            if doc.last_alert_days and stage >= doc.last_alert_days:
                continue  # this threshold (or an earlier one) was already sent
            doc._ess_send_alert(stage)
            doc.last_alert_days = stage
        return True

    def _ess_alert_stage(self):
        """Threshold reached today: EXPIRED_STAGE, the smallest threshold >= days, or None."""
        self.ensure_one()
        if not self.expiry_date:
            return None
        if self.days_to_expiry < 0:
            return EXPIRED_STAGE
        reached = [t for t in self.type_id._alert_thresholds() if self.days_to_expiry <= t]
        return min(reached) if reached else None

    # ------------------------------------------------------------------
    # alerts
    # ------------------------------------------------------------------
    def _ess_hr_users(self):
        """HR responsible of the employee (contract), else the company HR officers."""
        self.ensure_one()
        employee = self.employee_id.sudo()
        users = employee.version_id.hr_responsible_id.filtered(lambda u: u.active and not u.share)
        if not users:
            users = self.env.ref('bs_hr_ess_base.group_ess_hr_officer').sudo().all_user_ids.filtered(
                lambda u: u.active and not u.share and employee.company_id in u.company_ids)
        return users

    def _ess_alert_texts(self, stage, lang_env):
        self.ensure_one()
        if stage == EXPIRED_STAGE:
            title = lang_env._('Document expired: %s', self.type_id.with_env(lang_env).name)
            body = lang_env._('Your %(doc)s expired on %(date)s. Please renew it and upload the new copy.',
                              doc=self.type_id.with_env(lang_env).name, date=self.expiry_date)
        else:
            title = lang_env._('Document expiring: %s', self.type_id.with_env(lang_env).name)
            body = lang_env._('Your %(doc)s expires on %(date)s (in %(days)s days). Please start the renewal.',
                              doc=self.type_id.with_env(lang_env).name, date=self.expiry_date,
                              days=self.days_to_expiry)
        return title, body

    def _ess_send_alert(self, stage):
        self.ensure_one()
        doc = self.sudo()
        employee = doc.employee_id
        user = employee.user_id
        lang_env = doc.with_context(lang=user.lang or employee.company_id.partner_id.lang or self.env.lang).env
        title, body = doc._ess_alert_texts(stage, lang_env)
        # 1. employee: portal notification + message
        self.env['bs.hr.notification'].sudo().create({
            'employee_id': employee.id, 'title': title, 'body': body, 'url': '/my/hr/documents',
            'res_model': doc._name, 'res_id': doc.id,
        })
        partner_ids = user.partner_id.ids if user else []
        doc.message_post(body=body, subject=title, partner_ids=partner_ids,
                         subtype_xmlid='mail.mt_comment' if partner_ids else 'mail.mt_note')
        # 2. supervisor
        summary = self.env._('%(doc)s of %(employee)s', doc=doc.display_name, employee=employee.name)
        manager_user = employee.parent_id.user_id or employee.work_location_id.ess_manager_id
        if manager_user and manager_user != user:
            doc._ess_notify_user(manager_user, title, summary)
        # 3. HR: one open activity per document, deadline moved to the expiry date
        doc._ess_schedule_hr_activity(summary)

    def _ess_notify_user(self, user, title, body):
        """Notify an internal or portal user (portal notification when he is an employee)."""
        self.ensure_one()
        manager_employee = user.sudo().employee_ids[:1]
        if manager_employee:
            self.env['bs.hr.notification'].sudo().create({
                'employee_id': manager_employee.id, 'title': title, 'body': body,
                'url': '/my/hr/team', 'res_model': self._name, 'res_id': self.id,
            })
        self.sudo().message_notify(partner_ids=user.partner_id.ids, subject=title, body=body)

    def _ess_schedule_hr_activity(self, summary):
        self.ensure_one()
        act_type = self.env.ref('bs_hr_ess_documents.mail_activity_document_expiry', raise_if_not_found=False)
        deadline = max(self.expiry_date or fields.Date.context_today(self), fields.Date.context_today(self))
        existing = self.activity_ids.filtered(lambda a: act_type and a.activity_type_id == act_type)
        if existing:
            existing.write({'date_deadline': deadline, 'summary': summary})
            return existing
        activities = self.env['mail.activity']
        for hr_user in self._ess_hr_users():
            activities |= self.sudo().activity_schedule(
                'bs_hr_ess_documents.mail_activity_document_expiry', date_deadline=deadline,
                summary=summary, user_id=hr_user.id)
        return activities

    def _ess_close_expiry_activities(self):
        act_type = self.env.ref('bs_hr_ess_documents.mail_activity_document_expiry', raise_if_not_found=False)
        if not act_type:
            return
        for doc in self.sudo():
            if doc.expiry_date and doc.expiry_date > fields.Date.context_today(doc):
                doc.activity_ids.filtered(lambda a: a.activity_type_id == act_type).action_feedback(
                    feedback=self.env._('Document renewed.'))

    # ------------------------------------------------------------------
    # attendance
    # ------------------------------------------------------------------
    @api.model
    def _ess_blocking_documents(self, employee, date=None):
        """Expired documents of ``employee`` whose type is critical for attendance."""
        date = date or fields.Date.context_today(self)
        return self.sudo().search(Domain([
            ('employee_id', '=', employee.id),
            ('type_id.block_attendance', '=', True),
            ('expiry_date', '!=', False),
            ('expiry_date', '<', date),
        ]))

    def _ess_notify_supervisor_expired(self):
        """Non-blocking mode: tell the supervisor (at most once a day per document)."""
        today = fields.Date.context_today(self)
        for doc in self.sudo():
            if doc.supervisor_alert_date == today:
                continue
            employee = doc.employee_id
            manager_user = employee.parent_id.user_id or employee.work_location_id.ess_manager_id
            if manager_user and manager_user != employee.user_id:
                title = self.env._('Expired %(doc)s: %(employee)s',
                                   doc=doc.type_id.name, employee=employee.name)
                body = self.env._('%(employee)s is checking in with an expired %(doc)s (expired on %(date)s).',
                                  employee=employee.name, doc=doc.type_id.name, date=doc.expiry_date)
                doc._ess_notify_user(manager_user, title, body)
            doc.supervisor_alert_date = today

    # ------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------
    def action_open_renewals(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Renewals'),
            'res_model': 'bs.hr.document.renewal',
            'view_mode': 'list,form',
            'domain': [('document_id', '=', self.id)],
            'context': {'default_document_id': self.id, 'default_employee_id': self.employee_id.id},
        }

    def _ess_post_files(self, attachments, body):
        """Copy ``attachments`` on the document, link them and post them in the chatter."""
        self.ensure_one()
        copies = self.env['ir.attachment'].sudo()
        for att in attachments.sudo():
            copies |= att.copy({'res_model': self._name, 'res_id': self.id})
        if copies:
            self.sudo().attachment_ids = [(4, a.id) for a in copies]
        self.sudo().message_post(body=Markup('%s') % body, attachment_ids=copies.ids,
                                 subtype_xmlid='mail.mt_note')
        return copies
