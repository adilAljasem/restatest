from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.bs_hr_ess_base.tests.common import EssCommon


@tagged('post_install', '-at_install', 'bs_hr_ess')
class TestEssContractRenewal(EssCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.today = fields.Date.context_today(cls.env.user)
        cls.end = cls.today + timedelta(days=30)
        cls.emp_employee.sudo().write({'contract_date_start': '2020-01-01', 'contract_date_end': cls.end})
        cls.user_hr_manager = cls.env['res.users'].create({
            'name': 'ESS HR Manager', 'login': 'ess_hr_mgr',
            'group_ids': [(6, 0, cls.env.ref('bs_hr_ess_base.group_ess_hr_manager').ids)]})
        # keep the default flow deterministic: manager -> HR officer -> HR manager (test user only)
        step = cls.env.ref('bs_hr_ess_documents.flow_contract_renewal_step_management')
        step.write({'approver_type': 'users', 'user_ids': [(6, 0, cls.user_hr_manager.ids)]})
        cls.env.ref('bs_hr_ess_documents.flow_contract_renewal_step_hr').write(
            {'approver_type': 'users', 'user_ids': [(6, 0, cls.user_hr.ids)]})

    def _cron(self):
        return self.env['bs.hr.contract.renewal']._cron_open_renewals().filtered(
            lambda r: r.employee_id == self.emp_employee)

    def test_cron_opens_and_submits_once(self):
        renewal = self._cron()
        self.assertEqual(len(renewal), 1)
        self.assertEqual(renewal.state, 'in_approval')
        self.assertEqual(renewal.contract_date_end, self.end)
        self.assertEqual(renewal.version_id, self.emp_employee.version_id)
        self.assertEqual(renewal.current_approver_ids, self.user_manager)
        self.assertFalse(self._cron(), 'no second renewal for the same contract')
        # contract far away: nothing
        self.emp_other.sudo().write({'contract_date_start': '2020-01-01',
                                     'contract_date_end': self.today + timedelta(days=200)})
        self.assertFalse(self.env['bs.hr.contract.renewal']._cron_open_renewals().filtered(
            lambda r: r.employee_id == self.emp_other))

    def test_renew_creates_new_version(self):
        renewal = self._cron()
        new_end = self.end + timedelta(days=365)
        # manager decides from the portal with the approval fields
        renewal.with_user(self.user_manager).sudo().action_ess_approve(
            'ok', values={'manager_decision': 'modify', 'new_contract_date_end': new_end,
                          'new_wage': 7000, 'state': 'done'})
        self.assertEqual(renewal.state, 'in_approval', 'state is not an approval field')
        self.assertEqual(renewal.manager_decision, 'modify')
        renewal.with_user(self.user_hr).action_ess_approve()
        renewal.with_user(self.user_hr_manager).action_ess_approve()
        self.assertEqual(renewal.state, 'done')
        version = renewal.new_version_id
        self.assertTrue(version)
        self.assertEqual(version.employee_id, self.emp_employee)
        self.assertEqual(version.date_version, self.end + timedelta(days=1))
        self.assertEqual(version.contract_date_start, self.end + timedelta(days=1))
        self.assertEqual(version.contract_date_end, new_end)
        self.assertEqual(version.wage, 7000)
        # the old version keeps its dates and wage
        self.assertEqual(renewal.version_id.contract_date_end, self.end)
        self.assertEqual(renewal.version_id.wage, 6000)
        # the employee is invited to acknowledge, then acknowledges
        self.assertTrue(self.env['bs.hr.notification'].search_count([
            ('employee_id', '=', self.emp_employee.id), ('res_model', '=', renewal._name)]))
        renewal.action_ess_acknowledge()
        self.assertTrue(renewal.employee_ack_date)
        # no renewal is opened anymore: a later contract exists
        self.assertFalse(self._cron())

    def test_renew_default_duration(self):
        renewal = self._cron()
        renewal.with_user(self.user_manager).sudo().action_ess_approve()
        renewal.with_user(self.user_hr).action_ess_approve()
        renewal.with_user(self.user_hr_manager).action_ess_approve()
        self.assertEqual(renewal.new_version_id.wage, 6000)
        self.assertGreater(renewal.new_version_id.contract_date_end, self.end)

    def test_not_renew_creates_activity(self):
        renewal = self._cron()
        renewal.with_user(self.user_manager).sudo().action_ess_approve(
            values={'manager_decision': 'not_renew'})
        renewal.with_user(self.user_hr).action_ess_approve()
        renewal.with_user(self.user_hr_manager).action_ess_approve()
        self.assertEqual(renewal.state, 'done')
        self.assertFalse(renewal.new_version_id)
        act_type = self.env.ref('bs_hr_ess_documents.mail_activity_end_of_service')
        self.assertTrue(renewal.activity_ids.filtered(lambda a: a.activity_type_id == act_type))
        with self.assertRaises(UserError):
            renewal.action_ess_acknowledge()
