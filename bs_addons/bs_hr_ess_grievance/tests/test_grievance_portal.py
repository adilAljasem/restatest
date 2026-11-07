from odoo.tests import HttpCase, new_test_user, tagged


@tagged('post_install', '-at_install', 'bs_hr_ess')
class TestGrievancePortal(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_a = new_test_user(cls.env, login='grv_a', password='grv_a_pwd', groups='base.group_portal')
        cls.user_b = new_test_user(cls.env, login='grv_b', password='grv_b_pwd', groups='base.group_portal')
        cls.member = new_test_user(cls.env, login='grv_portal_member',
                                   groups='bs_hr_ess_base.group_ess_grievance_committee')
        cls.emp_a = cls.env['hr.employee'].create({'name': 'Grievance A', 'user_id': cls.user_a.id, 'wage': 3000})
        cls.emp_b = cls.env['hr.employee'].create({'name': 'Grievance B', 'user_id': cls.user_b.id})
        cls.case_b = cls.env['bs.hr.grievance']._ess_create_for_employee(cls.emp_b, {
            'grievance_type': 'whistleblowing', 'subject': 'Expired products', 'description': 'In the cold room',
            'anonymous': True})
        vtype = cls.env.ref('bs_hr_ess_grievance.violation_lateness')
        cls.dis_a = cls.env['bs.hr.disciplinary'].create({
            'employee_id': cls.emp_a.id, 'violation_type_id': vtype.id, 'description': 'Late 40 minutes'})
        cls.dis_a.action_notify_employee()
        cls.dis_b = cls.env['bs.hr.disciplinary'].create({
            'employee_id': cls.emp_b.id, 'violation_type_id': vtype.id, 'description': 'Late'})
        cls.dis_b.action_notify_employee()

    def _csrf(self):
        return self.opener.get(self.base_url() + '/my/hr').text.split('csrf_token: "')[1].split('"')[0]

    def test_pages_render(self):
        self.authenticate('grv_a', 'grv_a_pwd')
        for url in ('/my/hr', '/my/hr/grievances', '/my/hr/grievance/new', '/my/hr/disciplinary'):
            res = self.url_open(url)
            self.assertEqual(res.status_code, 200, url)
        self.assertIn('Late 40 minutes', self.url_open('/my/hr/disciplinary').text)
        self.assertIn('Grievance B', self.url_open('/my/hr/grievance/new').text)  # colleague selectable

    def test_other_employee_case_404(self):
        self.authenticate('grv_a', 'grv_a_pwd')
        res = self.url_open('/my/hr/grievance/%s' % self.case_b.id)
        self.assertEqual(res.status_code, 404)
        csrf = self._csrf()
        res = self.url_open('/my/hr/grievance/%s/reply' % self.case_b.id, data={'csrf_token': csrf, 'body': 'x'})
        self.assertEqual(res.status_code, 404)
        res = self.url_open('/my/hr/disciplinary/%s/appeal' % self.dis_b.id, data={'csrf_token': csrf, 'reason': 'x'})
        self.assertEqual(res.status_code, 404)
        # tracking code of somebody else does not open the case
        res = self.url_open('/my/hr/grievance/track', data={'csrf_token': csrf, 'code': self.case_b.sudo().tracking_code})
        self.assertNotIn('/my/hr/grievance/%s' % self.case_b.id, res.url)
        self.assertFalse(self.case_b.message_ids)

    def test_anonymous_submission_flow(self):
        self.authenticate('grv_a', 'grv_a_pwd')
        res = self.url_open('/my/hr/grievance/new', data={
            'csrf_token': self._csrf(), 'grievance_type': 'complaint_supervisor', 'subject': 'Shouting',
            'description': 'Every evening shift', 'anonymous': '1', 'against_employee_ids': str(self.emp_b.id)})
        self.assertEqual(res.status_code, 200)
        case = self.env['bs.hr.grievance'].search([('subject', '=', 'Shouting')])
        self.assertTrue(case.anonymous)
        self.assertFalse(case.employee_id)
        self.assertNotEqual(case.create_uid, self.user_a)
        self.assertEqual(case.against_employee_ids, self.emp_b)
        self.assertIn(case.sudo().tracking_code, res.text)
        # listed for the author, and the tracking code works for him
        self.assertIn('Shouting', self.url_open('/my/hr/grievances').text)
        res = self.url_open('/my/hr/grievance/track', data={'csrf_token': self._csrf(), 'code': case.sudo().tracking_code})
        self.assertTrue(res.url.endswith('/my/hr/grievance/%s' % case.id))
        self.assertNotIn(case.sudo().tracking_code, res.text)  # shown only once
        # committee reply is displayed as "Committee"
        self.env['bs.hr.grievance.message'].with_user(self.member).create({'grievance_id': case.id, 'body': 'We are on it'})
        page = self.url_open('/my/hr/grievance/%s' % case.id).text
        self.assertIn('We are on it', page)
        self.assertNotIn(self.member.name, page)
        res = self.url_open('/my/hr/grievance/%s/reply' % case.id, data={'csrf_token': self._csrf(), 'body': 'Thanks'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(case.message_ids[-1].author_type, 'employee')

    def test_disciplinary_appeal_from_portal(self):
        self.authenticate('grv_a', 'grv_a_pwd')
        res = self.url_open('/my/hr/disciplinary/%s/ack' % self.dis_a.id, data={'csrf_token': self._csrf()})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(self.dis_a.employee_ack_date)
        res = self.url_open('/my/hr/disciplinary/%s/appeal' % self.dis_a.id,
                            data={'csrf_token': self._csrf(), 'reason': 'Traffic accident'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.dis_a.state, 'appealed')
        grievance = self.dis_a.sudo().grievance_id
        self.assertEqual(grievance.grievance_type, 'appeal_disciplinary')
        self.assertTrue(res.url.endswith('/my/hr/grievance/%s' % grievance.id))
