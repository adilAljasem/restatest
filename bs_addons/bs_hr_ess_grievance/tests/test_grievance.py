from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user, tagged

from odoo.addons.bs_hr_ess_base.tests.common import EssCommon


@tagged('post_install', '-at_install', 'bs_hr_ess')
class TestGrievance(EssCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        committee = 'bs_hr_ess_base.group_ess_grievance_committee'
        cls.member_1 = new_test_user(cls.env, login='grv_member1', groups=committee)
        cls.member_2 = new_test_user(cls.env, login='grv_member2',
                                     groups=committee + ',bs_hr_ess_base.group_ess_hr_manager')
        # the branch manager is also in the committee: he must be excluded when a party
        cls.user_manager.group_ids = [(4, cls.env.ref(committee).id)]
        Grievance = cls.env['bs.hr.grievance']
        cls.anonymous = Grievance._ess_create_for_employee(cls.emp_other, {
            'grievance_type': 'complaint_colleague', 'subject': 'Harassment in the kitchen',
            'description': 'Details', 'anonymous': True,
            'against_employee_ids': [(6, 0, cls.emp_employee.ids)]})
        cls.named = Grievance._ess_create_for_employee(cls.emp_employee, {
            'grievance_type': 'suggestion', 'subject': 'More fans', 'description': 'It is hot'})

    def test_anonymous_stores_no_author(self):
        grv = self.anonymous
        self.assertTrue(grv.anonymous)
        self.assertFalse(grv.employee_id)
        self.assertNotEqual(grv.create_uid, self.user_other)
        self.assertNotEqual(grv.write_uid, self.user_other)
        self.assertTrue(grv.sudo().owner_hash)
        self.assertTrue(grv.sudo().tracking_code)
        # the owner hash is not the plain employee id
        self.assertNotIn(str(self.emp_other.id), grv.sudo().owner_hash[:3])
        # committee member: no way to find the author
        as_member = grv.with_user(self.member_1)
        data = as_member.read(['employee_id', 'author_display', 'create_uid', 'write_uid'])[0]
        self.assertFalse(data['employee_id'])
        self.assertEqual(data['author_display'], 'Anonymous')
        self.assertNotEqual(data['create_uid'][0], self.user_other.id)
        with self.assertRaises(AccessError):
            as_member.read(['owner_hash'])
        with self.assertRaises(AccessError):
            as_member.read(['tracking_code'])
        self.assertFalse(self.env['bs.hr.grievance'].with_user(self.member_1).search(
            [('employee_id', '=', self.emp_other.id)]))
        # no message / follower / activity links the author to the case
        self.assertFalse(self.env['mail.message'].search(
            [('model', '=', grv._name), ('res_id', '=', grv.id)]))
        activities = self.env['mail.activity'].search([('summary', 'ilike', grv.name)])
        self.assertTrue(activities)
        for act in activities:
            self.assertNotIn(self.emp_other.name, act.note or '')
        # the author finds his own case
        owned = self.env['bs.hr.grievance']._ess_search_owned(self.emp_other)
        self.assertIn(grv, owned)
        self.assertTrue(grv._is_owner(self.emp_other))
        self.assertFalse(grv._is_owner(self.emp_employee))
        # the author cannot be changed afterwards
        with self.assertRaises(AccessError):
            grv.with_user(self.member_1).write({'employee_id': self.emp_other.id})

    def test_routing_excludes_parties(self):
        grv = self.anonymous
        # emp_employee (against) -> his manager (user_manager) is also branch manager
        self.assertIn(self.user_manager, grv.excluded_user_ids)
        self.assertIn(self.user_employee, grv.excluded_user_ids)
        self.assertNotIn(self.user_manager, grv.handler_ids)
        self.assertEqual(grv.handler_ids, (self.member_1 | self.member_2) & grv.handler_ids)
        self.assertIn(self.member_1, grv.handler_ids)
        # record rule: the excluded committee member cannot read/search the case
        Grievance = self.env['bs.hr.grievance']
        self.assertNotIn(grv, Grievance.with_user(self.user_manager).search([]))
        with self.assertRaises(AccessError):
            grv.with_user(self.user_manager).read(['subject'])
        self.assertIn(grv, Grievance.with_user(self.member_1).search([]))
        # the excluded member sees the other case (not a party)
        self.assertIn(self.named, Grievance.with_user(self.user_manager).search([]))

    def test_hr_officer_without_committee_has_no_access(self):
        with self.assertRaises(AccessError):
            self.env['bs.hr.grievance'].with_user(self.user_hr).search([])
        with self.assertRaises(AccessError):
            self.named.with_user(self.user_hr).read(['subject'])

    def test_access_log_and_workflow(self):
        grv = self.named.with_user(self.member_1)
        grv.web_read({'subject': {}})
        log = self.env['bs.hr.grievance.access.log'].search([('grievance_id', '=', grv.id)])
        self.assertEqual(log.user_id, self.member_1)
        self.assertEqual(log.action, 'read')
        grv.action_start_investigation()
        self.assertEqual(grv.state, 'investigating')
        with self.assertRaises(UserError):
            grv.action_resolve()  # resolution required
        grv.resolution = 'Fans ordered'
        grv.action_resolve()
        self.assertEqual(grv.state, 'resolved')
        self.assertTrue(grv.decision_date)
        actions = self.env['bs.hr.grievance.access.log'].search([('grievance_id', '=', grv.id)]).mapped('action')
        self.assertIn('investigating', actions)
        self.assertIn('resolved', actions)
        grv.action_close()
        self.assertEqual(grv.state, 'closed')
        # the named author got portal notifications
        self.assertTrue(self.env['bs.hr.notification'].search(
            [('employee_id', '=', self.emp_employee.id), ('res_model', '=', grv._name)]))

    def test_messages_hide_committee_member(self):
        grv = self.anonymous
        grv._ess_post_employee_message('More details')
        msg = self.env['bs.hr.grievance.message'].with_user(self.member_1).create(
            {'grievance_id': grv.id, 'body': 'Thank you', 'author_type': 'employee'})
        self.assertEqual(msg.author_type, 'committee')  # cannot impersonate the employee
        self.assertEqual(msg.author_user_id, self.member_1)
        employee_msg = grv.message_ids.filtered(lambda m: m.author_type == 'employee')
        self.assertFalse(employee_msg.author_user_id)
        self.assertNotEqual(employee_msg.create_uid, self.user_other)

    def test_appeal_within_deadline(self):
        grv = self.anonymous.with_user(self.member_1)
        grv.resolution = 'Not proven'
        grv.action_reject()
        self.assertTrue(grv._can_appeal())
        self.assertEqual(grv.appeal_deadline, fields.Date.today() + timedelta(days=15))
        grv.sudo()._ess_appeal('New witnesses')
        self.assertEqual(grv.state, 'appealed')
        self.assertEqual(grv.level, '2')
        # level 2: HR managers of the committee
        self.assertEqual(grv.handler_ids, self.member_2)
        self.assertFalse(grv._can_appeal())

    def test_appeal_after_deadline(self):
        self.company.ess_appeal_days = 5
        grv = self.named.with_user(self.member_1)
        grv.write({'grievance_type': 'complaint_supervisor', 'resolution': 'Done'})
        grv.action_resolve()
        grv.sudo().decision_date = fields.Datetime.now() - timedelta(days=6)
        self.assertFalse(grv._can_appeal())
        with self.assertRaises(UserError):
            grv.sudo()._ess_appeal('Too late')
