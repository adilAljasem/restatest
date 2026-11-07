from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.bs_hr_ess_base.tests.common import EssCommon


@tagged('post_install', '-at_install', 'bs_hr_ess')
class TestAnnouncement(EssCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.dept_kitchen = cls.env['hr.department'].create({'name': 'ESS Kitchen'})
        cls.dept_service = cls.env['hr.department'].create({'name': 'ESS Service'})
        partner = cls.env['res.partner'].create({'name': 'ESS Other Branch'})
        cls.branch_2 = cls.env['hr.work.location'].create({'name': 'ESS Branch 2', 'address_id': partner.id})
        cls.country_in = cls.env.ref('base.in')
        cls.country_sa = cls.env.ref('base.sa')
        cls.emp_employee.write({'department_id': cls.dept_kitchen.id, 'country_id': cls.country_in.id})
        cls.emp_other.write({'department_id': cls.dept_service.id, 'country_id': cls.country_sa.id,
                             'work_location_id': cls.branch_2.id})
        cls.emp_manager.write({'department_id': cls.dept_service.id, 'country_id': cls.country_sa.id})

    def _announcement(self, **vals):
        return self.env['bs.hr.announcement'].create({
            'name': 'Test', 'body': '<p>Body</p>', 'company_ids': [(6, 0, self.company.ids)], **vals})

    # ------------------------------------------------------------------
    def test_targeting(self):
        test_emps = self.emp_employee | self.emp_other | self.emp_manager
        everyone = self._announcement(target_all=True)
        self.assertEqual(everyone._get_recipients() & test_emps, test_emps)

        by_branch = self._announcement(target_all=False, work_location_ids=[(6, 0, self.branch.ids)])
        self.assertEqual(by_branch._get_recipients(), self.emp_employee | self.emp_manager)

        by_dept = self._announcement(target_all=False, department_ids=[(6, 0, self.dept_service.ids)])
        self.assertEqual(by_dept._get_recipients(), self.emp_other | self.emp_manager)

        by_country = self._announcement(target_all=False, country_ids=[(6, 0, self.country_in.ids)])
        self.assertIn(self.emp_employee, by_country._get_recipients())
        self.assertNotIn(self.emp_other, by_country._get_recipients())

        combined = self._announcement(target_all=False, work_location_ids=[(6, 0, self.branch.ids)],
                                      department_ids=[(6, 0, self.dept_service.ids)])
        self.assertEqual(combined._get_recipients(), self.emp_manager)
        self.assertTrue(combined._is_recipient(self.emp_manager))
        self.assertFalse(combined._is_recipient(self.emp_employee))

        # archived employees are never targeted
        self.emp_manager.active = False
        self.assertFalse(combined._get_recipients())

    def test_publish_requires_target(self):
        ann = self._announcement(target_all=False)
        with self.assertRaises(UserError):
            ann.action_publish()

    def test_publish_creates_notifications(self):
        ann = self._announcement(target_all=False, work_location_ids=[(6, 0, self.branch.ids)],
                                 require_ack=True)
        ann.action_publish()
        self.assertEqual(ann.state, 'published')
        self.assertTrue(ann.notified)
        notifs = self.env['bs.hr.notification'].search([('res_model', '=', ann._name), ('res_id', '=', ann.id)])
        self.assertEqual(notifs.employee_id, self.emp_employee | self.emp_manager)
        self.assertEqual(set(notifs.mapped('url')), {'/my/hr/news/%s' % ann.id})
        # an e-mail notification is sent to the users
        message = self.env['mail.message'].search([
            ('model', '=', ann._name), ('res_id', '=', ann.id), ('message_type', '=', 'user_notification')])
        self.assertTrue(message)
        self.assertIn(self.user_employee.partner_id, message.partner_ids)
        # publishing again does not notify twice
        ann.action_publish()
        self.assertEqual(self.env['bs.hr.notification'].search_count(
            [('res_model', '=', ann._name), ('res_id', '=', ann.id)]), 2)

    def test_scheduled_publication(self):
        ann = self._announcement(date_publish=fields.Datetime.now() + timedelta(days=1),
                                 target_all=False, work_location_ids=[(6, 0, self.branch.ids)])
        ann.action_publish()
        self.assertFalse(ann.notified)
        self.assertFalse(ann.is_current)
        self.assertNotIn(ann, self.env['bs.hr.announcement']._ess_visible_for(self.emp_employee))
        ann.date_publish = fields.Datetime.now() - timedelta(minutes=1)
        self.env['bs.hr.announcement']._cron_process()
        self.assertTrue(ann.notified)
        self.assertIn(ann, self.env['bs.hr.announcement']._ess_visible_for(self.emp_employee))

    def test_ack_flow_and_rate(self):
        to_ack_before = self.emp_employee._ess_comms_counters()[1]
        ann = self._announcement(target_all=False, work_location_ids=[(6, 0, self.branch.ids)],
                                 require_ack=True)
        ann.action_publish()
        self.assertEqual(ann.recipient_count, 2)
        self.assertEqual(ann.ack_rate, 0.0)
        ann._ess_mark_read(self.emp_employee)
        ann.invalidate_recordset()
        self.assertEqual(ann.read_count, 1)
        self.assertEqual(ann.ack_count, 0)
        ann._ess_acknowledge(self.emp_employee, ip='10.0.0.1')
        ann.invalidate_recordset()
        self.assertEqual(ann.ack_count, 1)
        self.assertAlmostEqual(ann.ack_rate, 50.0)
        ack = ann._ess_ack_for(self.emp_employee)
        self.assertTrue(ack.ack_date)
        self.assertEqual(ack.ip, '10.0.0.1')
        self.assertEqual(ann._get_not_acknowledged(), self.emp_manager)
        action = ann.action_open_not_acknowledged()
        self.assertEqual(action['domain'], [('id', 'in', self.emp_manager.ids)])
        # acknowledging twice keeps the first date
        first = ack.ack_date
        ann._ess_acknowledge(self.emp_employee)
        self.assertEqual(ack.ack_date, first)
        self.assertEqual(self.emp_employee._ess_comms_counters()[1], to_ack_before)
        self.assertEqual(self.emp_manager._ess_comms_counters()[1], to_ack_before + 1)

    def test_reminder_cron(self):
        ann = self._announcement(target_all=False, work_location_ids=[(6, 0, self.branch.ids)],
                                 require_ack=True)
        ann.action_publish()
        ann._ess_acknowledge(self.emp_employee)
        Notification = self.env['bs.hr.notification']
        domain = [('res_model', '=', ann._name), ('res_id', '=', ann.id)]
        before = Notification.search_count(domain)
        # too early: no reminder
        self.env['bs.hr.announcement']._cron_process()
        self.assertEqual(Notification.search_count(domain), before)
        # 3 days later: only the manager (not acknowledged) is reminded
        ann.date_publish = fields.Datetime.now() - timedelta(days=3)
        self.env['bs.hr.announcement']._cron_process()
        new = Notification.search(domain).filtered(lambda n: n.title.startswith('Reminder'))
        self.assertEqual(new.employee_id, self.emp_manager)
        self.assertEqual(ann.last_reminder_date, fields.Date.context_today(ann))
        # at most once a day
        self.env['bs.hr.announcement']._cron_process()
        self.assertEqual(len(Notification.search(domain).filtered(lambda n: n.title.startswith('Reminder'))), 1)
        # manual reminder button
        ann.action_send_reminder()
        self.assertEqual(len(Notification.search(domain).filtered(lambda n: n.title.startswith('Reminder'))), 2)

    def test_expiry_archives(self):
        ann = self._announcement()
        ann.action_publish()
        ann.date_expire = fields.Datetime.now() - timedelta(minutes=1)
        ann.date_publish = fields.Datetime.now() - timedelta(days=1)
        self.env['bs.hr.announcement']._cron_process()
        self.assertEqual(ann.state, 'archived')

    def test_translated_content(self):
        self.env['res.lang']._activate_lang('ar_001')
        ann = self._announcement(name='Uniform policy')
        ann.with_context(lang='ar_001').name = 'سياسة الزي'
        self.assertEqual(ann.with_context(lang='en_US').name, 'Uniform policy')
        self.assertEqual(ann.with_context(lang='ar_001').name, 'سياسة الزي')

    def test_celebrations_privacy(self):
        self.company.ess_show_birthdays = True
        today = fields.Date.today()
        self.emp_manager.birthday = today.replace(year=1990)
        self.emp_other.birthday = today.replace(year=1991)  # other branch
        result = self.emp_employee._ess_celebrations(today)
        names = [r['name'] for r in result if r['kind'] == 'birthday']
        self.assertIn(self.emp_manager.name, names)
        self.assertNotIn(self.emp_other.name, names)
        for row in result:
            self.assertFalse(set(row) - {'day', 'name', 'kind', 'years'})
        self.company.ess_show_birthdays = False
        self.assertEqual(self.emp_employee._ess_celebrations(today), [])

    def test_public_holidays(self):
        calendar = self.emp_employee.resource_calendar_id or self.company.resource_calendar_id
        start = fields.Datetime.now() + timedelta(days=10)
        holiday = self.env['resource.calendar.leaves'].create({
            'name': 'ESS National Day', 'calendar_id': calendar.id,
            'date_from': start, 'date_to': start + timedelta(days=1)})
        far = self.env['resource.calendar.leaves'].create({
            'name': 'ESS Far', 'calendar_id': calendar.id,
            'date_from': start + timedelta(days=200), 'date_to': start + timedelta(days=201)})
        personal = self.env['resource.calendar.leaves'].create({
            'name': 'ESS Personal', 'calendar_id': calendar.id, 'resource_id': self.emp_other.resource_id.id,
            'date_from': start, 'date_to': start + timedelta(days=1)})
        holidays = self.emp_employee._ess_public_holidays()
        self.assertIn(holiday, holidays)
        self.assertNotIn(far, holidays)
        self.assertNotIn(personal, holidays)
