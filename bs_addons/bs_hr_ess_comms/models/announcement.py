from datetime import timedelta

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.fields import Domain
from odoo.tools import html2plaintext

ANNOUNCEMENT_TYPES = [
    ('news', 'News'),
    ('circular', 'Circular'),
    ('policy', 'Policy'),
    ('congrats', 'Congratulations'),
    ('event', 'Event'),
]

REMINDER_AFTER_DAYS = 2


class BsHrAnnouncement(models.Model):
    """Company announcement / circular / policy published in the ESS portal.

    ``name`` and ``body`` are translatable: one record carries every language
    and each employee reads it in the language of his user.
    """
    _name = 'bs.hr.announcement'
    _description = 'Announcement'
    _inherit = ['mail.thread']
    _order = 'pinned desc, date_publish desc, id desc'

    name = fields.Char(string='Title', required=True, translate=True, tracking=True)
    summary = fields.Char(translate=True, help='Short text displayed in the feed and in notifications.')
    body = fields.Html(translate=True, sanitize=True)
    announcement_type = fields.Selection(ANNOUNCEMENT_TYPES, string='Type', required=True, default='news',
                                         index=True, tracking=True)
    image = fields.Image(max_width=1280, max_height=1280)
    attachment_ids = fields.Many2many('ir.attachment', 'bs_hr_announcement_attachment_rel',
                                      'announcement_id', 'attachment_id', string='Attachments')
    # targeting
    target_all = fields.Boolean(string='All employees', default=True,
                                help='Every active employee of the selected companies (all companies when empty).')
    company_ids = fields.Many2many('res.company', string='Companies', default=lambda self: self.env.company)
    work_location_ids = fields.Many2many('hr.work.location', string='Branches')
    department_ids = fields.Many2many('hr.department', string='Departments')
    job_ids = fields.Many2many('hr.job', string='Job positions')
    country_ids = fields.Many2many('res.country', string='Nationalities')
    # publication
    date_publish = fields.Datetime(string='Publication date', index=True, copy=False,
                                   help='Leave empty to publish immediately.')
    date_expire = fields.Datetime(string='Expiry date', index=True)
    pinned = fields.Boolean(help='Pinned announcements are shown first and on the portal home page.')
    require_ack = fields.Boolean(string='Requires acknowledgement',
                                 help='Employees must click "I have read and agree". Reminders are sent '
                                      'automatically to those who did not acknowledge.')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('published', 'Published'),
        ('archived', 'Archived'),
    ], default='draft', required=True, index=True, tracking=True, copy=False)
    notified = fields.Boolean(readonly=True, copy=False, help='Recipients have been notified.')
    last_reminder_date = fields.Date(readonly=True, copy=False)
    author_id = fields.Many2one('res.users', default=lambda self: self.env.user, string='Author')
    ack_ids = fields.One2many('bs.hr.announcement.ack', 'announcement_id', string='Reads')
    recipient_count = fields.Integer(compute='_compute_stats', string='Recipients')
    read_count = fields.Integer(compute='_compute_stats', string='Read')
    ack_count = fields.Integer(compute='_compute_stats', string='Acknowledged')
    ack_rate = fields.Float(compute='_compute_stats', string='Acknowledgement rate (%)', digits=(5, 1))
    is_current = fields.Boolean(compute='_compute_is_current', string='Visible now')

    _date_check = models.Constraint(
        'CHECK(date_expire IS NULL OR date_publish IS NULL OR date_expire > date_publish)',
        'The expiry date must be after the publication date.')

    # ------------------------------------------------------------------
    # computes
    # ------------------------------------------------------------------
    @api.depends('ack_ids.read_date', 'ack_ids.ack_date', 'target_all', 'company_ids', 'work_location_ids',
                 'department_ids', 'job_ids', 'country_ids')
    def _compute_stats(self):
        for rec in self:
            recipients = rec._get_recipients() if rec.id else self.env['hr.employee']
            acks = rec.ack_ids.filtered(lambda a: a.employee_id in recipients)
            rec.recipient_count = len(recipients)
            rec.read_count = len(acks.filtered('read_date'))
            rec.ack_count = len(acks.filtered('ack_date'))
            rec.ack_rate = (rec.ack_count / rec.recipient_count * 100.0) if rec.recipient_count else 0.0

    @api.depends('state', 'date_publish', 'date_expire')
    def _compute_is_current(self):
        now = fields.Datetime.now()
        for rec in self:
            rec.is_current = rec.state == 'published' and (not rec.date_publish or rec.date_publish <= now) \
                and (not rec.date_expire or rec.date_expire > now)

    # ------------------------------------------------------------------
    # targeting
    # ------------------------------------------------------------------
    def _recipient_domain(self):
        self.ensure_one()
        domain = Domain('active', '=', True)
        if self.company_ids:
            domain &= Domain('company_id', 'in', self.company_ids.ids)
        if not self.target_all:
            if self.work_location_ids:
                domain &= Domain('work_location_id', 'in', self.work_location_ids.ids)
            if self.department_ids:
                domain &= Domain('department_id', 'child_of', self.department_ids.ids)
            if self.job_ids:
                domain &= Domain('job_id', 'in', self.job_ids.ids)
            if self.country_ids:
                domain &= Domain('country_id', 'in', self.country_ids.ids)
        return domain

    def _get_recipients(self):
        """Active employees targeted by the announcement (sudo recordset)."""
        self.ensure_one()
        return self.env['hr.employee'].sudo().search(self._recipient_domain())

    def _is_recipient(self, employee):
        self.ensure_one()
        if not employee:
            return False
        return bool(self.env['hr.employee'].sudo().search_count(
            self._recipient_domain() & Domain('id', '=', employee.id), limit=1))

    @api.model
    def _ess_visible_for(self, employee, domain=None):
        """Current published announcements targeting ``employee`` (sudo)."""
        now = fields.Datetime.now()
        base = Domain([
            ('state', '=', 'published'),
            '|', ('date_publish', '=', False), ('date_publish', '<=', now),
            '|', ('date_expire', '=', False), ('date_expire', '>', now),
            '|', ('company_ids', '=', False), ('company_ids', 'in', employee.company_id.ids),
        ])
        if domain:
            base &= Domain(domain)
        candidates = self.sudo().search(base)
        return candidates.filtered(lambda a: a._is_recipient(employee))

    # ------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------
    def action_publish(self):
        now = fields.Datetime.now()
        for rec in self:
            if rec.state == 'published':
                continue
            if not rec.target_all and not (rec.work_location_ids or rec.department_ids or rec.job_ids
                                           or rec.country_ids):
                raise UserError(self.env._('Choose at least one target or select "All employees".'))
            rec.write({'state': 'published', 'date_publish': rec.date_publish or now})
        self._notify_due()
        return True

    def action_archive_announcement(self):
        self.write({'state': 'archived', 'pinned': False})
        return True

    def action_reset_draft(self):
        self.write({'state': 'draft', 'notified': False})
        return True

    def action_send_reminder(self):
        for rec in self:
            if rec.state != 'published' or not rec.require_ack:
                raise UserError(self.env._('Reminders are only sent for published announcements '
                                           'that require an acknowledgement.'))
            rec._send_reminder()
        return True

    def action_open_not_acknowledged(self):
        self.ensure_one()
        employees = self._get_not_acknowledged()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Not acknowledged: %s', self.name),
            'res_model': 'hr.employee',
            'view_mode': 'list,form',
            'domain': [('id', 'in', employees.ids)],
            'context': {'create': False},
        }

    def action_open_acks(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Reads and acknowledgements'),
            'res_model': 'bs.hr.announcement.ack',
            'view_mode': 'list',
            'domain': [('announcement_id', '=', self.id)],
            'context': {'create': False},
        }

    def _get_not_acknowledged(self):
        self.ensure_one()
        recipients = self._get_recipients()
        acked = self.ack_ids.filtered('ack_date').employee_id
        return recipients - acked

    # ------------------------------------------------------------------
    # notifications
    # ------------------------------------------------------------------
    def _portal_url(self):
        self.ensure_one()
        return '/my/hr/news/%s' % self.id

    def _notify_employees(self, employees, reminder=False):
        """Portal notification + e-mail, in each employee's own language."""
        self.ensure_one()
        Notification = self.env['bs.hr.notification'].sudo()
        by_lang = {}
        for employee in employees:
            lang = employee.user_id.lang or employee.company_id.partner_id.lang or self.env.lang or 'en_US'
            by_lang.setdefault(lang, self.env['hr.employee'])
            by_lang[lang] |= employee
        for lang, emps in by_lang.items():
            rec = self.with_context(lang=lang).sudo()
            env = rec.env
            if reminder:
                title = env._('Reminder: please acknowledge "%s"', rec.name)
            elif rec.require_ack:
                title = env._('New circular to acknowledge: %s', rec.name)
            else:
                title = env._('New announcement: %s', rec.name)
            text = rec.summary or html2plaintext(rec.body or '')[:200]
            Notification.create([{
                'employee_id': emp.id, 'title': title, 'body': text, 'url': rec._portal_url(),
                'res_model': rec._name, 'res_id': rec.id,
            } for emp in emps])
            partners = emps.user_id.partner_id
            if partners:
                link = Markup('<p><a href="%s">%s</a></p>') % (
                    rec.get_base_url() + rec._portal_url(), env._('Open in the self-service portal'))
                rec.message_notify(
                    partner_ids=partners.ids, subject=title,
                    body=Markup('<p>%s</p>') % text + link,
                    email_layout_xmlid='mail.mail_notification_light',
                )

    def _notify_due(self):
        """Notify recipients of published announcements whose publication date is reached."""
        now = fields.Datetime.now()
        for rec in self.filtered(lambda a: a.state == 'published' and not a.notified
                                 and (not a.date_publish or a.date_publish <= now)):
            rec._notify_employees(rec._get_recipients())
            rec.notified = True

    def _send_reminder(self):
        self.ensure_one()
        employees = self._get_not_acknowledged()
        if employees:
            self._notify_employees(employees, reminder=True)
        self.last_reminder_date = fields.Date.context_today(self)
        self.message_post(body=self.env._('Reminder sent to %s employee(s).', len(employees)))
        return employees

    @api.model
    def _cron_process(self):
        """Hourly: notify scheduled announcements, archive expired ones and
        remind (at most once a day) employees who did not acknowledge after 2 days."""
        now = fields.Datetime.now()
        today = fields.Date.context_today(self)
        self.search([('state', '=', 'published'), ('notified', '=', False),
                     '|', ('date_publish', '=', False), ('date_publish', '<=', now)])._notify_due()
        self.search([('state', '=', 'published'), ('date_expire', '!=', False),
                     ('date_expire', '<=', now)]).action_archive_announcement()
        to_remind = self.search([
            ('state', '=', 'published'), ('require_ack', '=', True), ('notified', '=', True),
            ('date_publish', '<=', now - timedelta(days=REMINDER_AFTER_DAYS)),
            '|', ('last_reminder_date', '=', False), ('last_reminder_date', '<', today),
        ])
        for rec in to_remind:
            rec._send_reminder()
        return True

    # ------------------------------------------------------------------
    # portal helpers
    # ------------------------------------------------------------------
    def _ess_ack_for(self, employee):
        self.ensure_one()
        return self.env['bs.hr.announcement.ack'].sudo().search(
            [('announcement_id', '=', self.id), ('employee_id', '=', employee.id)], limit=1)

    def _ess_mark_read(self, employee):
        self.ensure_one()
        ack = self._ess_ack_for(employee)
        now = fields.Datetime.now()
        if not ack:
            ack = self.env['bs.hr.announcement.ack'].sudo().create(
                {'announcement_id': self.id, 'employee_id': employee.id, 'read_date': now})
        elif not ack.read_date:
            ack.read_date = now
        return ack

    def _ess_acknowledge(self, employee, ip=None):
        self.ensure_one()
        if not self.require_ack:
            raise UserError(self.env._('This announcement does not require an acknowledgement.'))
        ack = self._ess_mark_read(employee)
        if not ack.ack_date:
            ack.write({'ack_date': fields.Datetime.now(), 'ip': ip or False})
        return ack


class BsHrAnnouncementAck(models.Model):
    _name = 'bs.hr.announcement.ack'
    _description = 'Announcement read / acknowledgement'
    _order = 'ack_date desc, read_date desc, id desc'
    _rec_name = 'employee_id'

    announcement_id = fields.Many2one('bs.hr.announcement', required=True, ondelete='cascade', index=True)
    employee_id = fields.Many2one('hr.employee', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='employee_id.company_id', store=True)
    work_location_id = fields.Many2one(related='employee_id.work_location_id', store=True, string='Branch')
    department_id = fields.Many2one(related='employee_id.department_id', store=True)
    read_date = fields.Datetime(readonly=True)
    ack_date = fields.Datetime(string='Acknowledged on', readonly=True)
    ip = fields.Char(string='IP address', readonly=True)

    _employee_uniq = models.Constraint('UNIQUE(announcement_id, employee_id)',
                                       'An employee has a single read record per announcement.')
