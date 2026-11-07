import base64
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import tagged

from odoo.addons.bs_hr_ess_base.tests.common import EssCommon


@tagged('post_install', '-at_install', 'bs_hr_ess')
class TestEssDocuments(EssCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.health = cls.env.ref('bs_hr_ess_documents.doc_type_health')
        cls.iqama = cls.env.ref('bs_hr_ess_documents.doc_type_iqama')
        cls.today = fields.Date.context_today(cls.env.user)

    def _doc(self, days, type_=None, **vals):
        return self.env['bs.hr.document'].create({
            'employee_id': self.emp_employee.id, 'type_id': (type_ or self.health).id,
            'number': 'N-%s' % days, 'expiry_date': self.today + timedelta(days=days), **vals})

    def test_state_computation(self):
        self.assertEqual(self._doc(200).state, 'valid')
        self.assertEqual(self._doc(60).state, 'expiring')
        self.assertEqual(self._doc(3).state, 'expiring')
        expired = self._doc(-1)
        self.assertEqual(expired.state, 'expired')
        self.assertEqual(expired.days_to_expiry, -1)
        self.assertEqual(self._doc(200, number=False).state, 'missing')
        self.assertTrue(expired.expiry_date_hijri)
        # renewal by write recomputes the state
        expired.expiry_date = self.today + timedelta(days=365)
        self.assertEqual(expired.state, 'valid')

    def test_daily_refresh(self):
        doc = self._doc(61)
        self.assertEqual(doc.state, 'valid')
        later = self.today + timedelta(days=2)
        with patch.object(fields.Date, 'context_today', lambda *a, **k: later):
            self.env['bs.hr.document']._cron_refresh_states()
        self.assertEqual(doc.state, 'expiring')
        self.assertEqual(doc.days_to_expiry, 59)

    def test_alert_days_validation(self):
        with self.assertRaises(ValidationError):
            self.env['bs.hr.document.type'].create({'name': 'Bad', 'alert_days': '30,abc'})
        self.assertEqual(self.health._alert_thresholds(), [60, 30, 15, 7])

    def test_cron_alert_thresholds(self):
        Notification = self.env['bs.hr.notification']
        doc = self._doc(45)
        Document = self.env['bs.hr.document']

        def notif_count():
            return Notification.search_count([('employee_id', '=', self.emp_employee.id),
                                              ('res_model', '=', 'bs.hr.document'), ('res_id', '=', doc.id)])

        Document._cron_expiry_alerts()
        self.assertEqual(doc.last_alert_days, 60)
        self.assertEqual(notif_count(), 1)
        # HR activity on the document, for the HR officers
        activity = doc.activity_ids
        self.assertTrue(activity)
        self.assertIn(self.user_hr, activity.user_id)
        # running again the same day sends nothing
        Document._cron_expiry_alerts()
        self.assertEqual(notif_count(), 1)
        # 20 days later: 25 days left -> 30-days threshold
        with patch.object(fields.Date, 'context_today', lambda *a, **k: self.today + timedelta(days=20)):
            Document._cron_expiry_alerts()
            self.assertEqual(doc.last_alert_days, 30)
            self.assertEqual(notif_count(), 2)
            Document._cron_expiry_alerts()
            self.assertEqual(notif_count(), 2)
        # the activity is reused (one per document)
        self.assertEqual(len(doc.activity_ids), len(activity))
        # after expiry -> expired alert, once
        with patch.object(fields.Date, 'context_today', lambda *a, **k: self.today + timedelta(days=46)):
            Document._cron_expiry_alerts()
            self.assertEqual(doc.state, 'expired')
            self.assertEqual(doc.last_alert_days, -1)
            self.assertEqual(notif_count(), 3)
            Document._cron_expiry_alerts()
            self.assertEqual(notif_count(), 3)
        # supervisor (portal notification of the manager employee)
        self.assertTrue(Notification.search_count([('employee_id', '=', self.emp_manager.id),
                                                   ('res_model', '=', 'bs.hr.document'),
                                                   ('res_id', '=', doc.id)]))

    def test_precheck_blocks_expired_health(self):
        self._doc(-3)
        self.company.ess_block_expired_health = True
        messages = self.emp_employee._ess_attendance_precheck(self.branch)
        self.assertEqual(len(messages), 1)
        self.assertIn(self.health.name, messages[0])
        # other employee is not affected
        self.assertFalse(self.emp_other._ess_attendance_precheck(self.branch))

    def test_precheck_notifies_supervisor_when_not_blocking(self):
        doc = self._doc(-3)
        self.company.ess_block_expired_health = False
        self.assertFalse(self.emp_employee._ess_attendance_precheck(self.branch))
        self.assertEqual(doc.supervisor_alert_date, self.today)
        notif = self.env['bs.hr.notification'].search([('employee_id', '=', self.emp_manager.id),
                                                       ('res_model', '=', 'bs.hr.document')])
        self.assertEqual(len(notif), 1)
        # once a day only
        self.emp_employee._ess_attendance_precheck(self.branch)
        self.assertEqual(self.env['bs.hr.notification'].search_count(
            [('employee_id', '=', self.emp_manager.id), ('res_model', '=', 'bs.hr.document')]), 1)

    def test_precheck_ignores_non_critical(self):
        self._doc(-3, type_=self.iqama)
        self.company.ess_block_expired_health = True
        self.assertFalse(self.emp_employee._ess_attendance_precheck(self.branch))

    def test_renewal_request_updates_document(self):
        doc = self._doc(10)
        renewal = self.env['bs.hr.document.renewal'].create({
            'employee_id': self.emp_employee.id, 'document_id': doc.id, 'new_number': 'NEW-1',
            'new_issue_date': self.today, 'new_expiry_date': self.today + timedelta(days=365)})
        # an attachment is mandatory
        with self.assertRaises(ValidationError):
            renewal.action_submit()
        self.env['ir.attachment'].create({
            'name': 'card.pdf', 'datas': base64.b64encode(b'%PDF-1.4 test'),
            'res_model': renewal._name, 'res_id': renewal.id})
        renewal.action_submit()
        self.assertEqual(renewal.state, 'in_approval')
        self.assertIn(self.user_hr, renewal.current_approver_ids)
        renewal.with_user(self.user_hr).action_ess_approve()
        self.assertEqual(renewal.state, 'done')
        self.assertEqual(doc.number, 'NEW-1')
        self.assertEqual(doc.expiry_date, self.today + timedelta(days=365))
        self.assertEqual(doc.state, 'valid')
        self.assertEqual(doc.last_alert_days, 0)
        self.assertEqual(doc.attachment_ids.mapped('name'), ['card.pdf'])
        self.assertEqual(doc.attachment_ids.res_model, 'bs.hr.document')

    def test_missing_mandatory_types(self):
        self.emp_employee.country_id = self.env.ref('base.eg')
        missing = self.emp_employee._ess_missing_document_types()
        self.assertIn(self.iqama, missing)
        self.assertNotIn(self.env.ref('bs_hr_ess_documents.doc_type_national_id'), missing)
        self._doc(100, type_=self.iqama)
        self.assertNotIn(self.iqama, self.emp_employee._ess_missing_document_types())

    def test_dashboard_card(self):
        self._doc(-2)
        card = [c for c in self.emp_employee._ess_dashboard_cards() if c['key'] == 'documents'][0]
        self.assertEqual(card['value'], 1)
        self.assertEqual(card['tone'], 'danger')
