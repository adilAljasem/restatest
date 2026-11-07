from odoo.tests import HttpCase, tagged

from .test_appraisal import AppraisalCommon


@tagged('post_install', '-at_install', 'bs_hr_ess')
class TestAppraisalPortal(AppraisalCommon, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_employee.password = 'ess_emp_pw'
        cls.user_manager.password = 'ess_mgr_pw'
        # another manager of another team
        cls.user_mgr2 = cls.env['res.users'].create({
            'name': 'Mgr 2', 'login': 'ess_mgr2', 'password': 'ess_mgr2_pw',
            'group_ids': [(6, 0, cls.env.ref('bs_hr_ess_base.group_ess_branch_supervisor').ids)]})
        cls.emp_mgr2 = cls.env['hr.employee'].create({'name': 'Manager 2', 'user_id': cls.user_mgr2.id})
        cls.emp_sub2 = cls.env['hr.employee'].create({'name': 'Sub 2', 'parent_id': cls.emp_mgr2.id})
        cls.appr = cls._appraisal_cls(cls.emp_employee)
        cls.appr_other = cls._appraisal_cls(cls.emp_other)
        cls.goal = cls.env['bs.hr.goal'].create({'employee_id': cls.emp_employee.id, 'name': 'Upsell'})

    @classmethod
    def _appraisal_cls(cls, employee):
        return cls.env['bs.hr.ess.appraisal'].create({
            'employee_id': employee.id, 'template_id': cls.template.id,
            'date_from': '2026-03-02', 'date_to': '2026-03-06'})

    def _csrf(self):
        return self.url_open('/my/hr').text.split('csrf_token: "')[1].split('"')[0]

    def test_pages_render(self):
        self.authenticate('ess_emp', 'ess_emp_pw')
        for url in ('/my/hr', '/my/hr/appraisals', '/my/hr/appraisal/%s' % self.appr.id, '/my/hr/goals',
                    '/my/hr/kudos', '/my/hr/kudos?tab=sent', '/my/hr/kudos?tab=send'):
            res = self.url_open(url)
            self.assertEqual(res.status_code, 200, url)
        self.assertIn('/my/hr/appraisals', self.url_open('/my/hr').text)

    def test_employee_cannot_open_other_appraisal(self):
        self.authenticate('ess_emp', 'ess_emp_pw')
        res = self.url_open('/my/hr/appraisal/%s' % self.appr_other.id)
        self.assertEqual(res.status_code, 404)
        # nor review his own appraisal as a "manager"
        res = self.url_open('/my/hr/team/appraisal/%s' % self.appr.id)
        self.assertEqual(res.status_code, 404)

    def test_self_review_then_manager_review(self):
        self.authenticate('ess_emp', 'ess_emp_pw')
        data = {'csrf_token': self._csrf(), 'action': 'submit', 'self_comment': 'Good month'}
        for line in self.appr.line_ids:
            data['score_%s' % line.id] = '4'
        res = self.url_open('/my/hr/appraisal/%s' % self.appr.id, data=data)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.appr.state, 'manager_review')
        self.assertEqual(set(self.appr.line_ids.mapped('self_score')), {'4'})
        # goal progress and kudos from the portal
        self.url_open('/my/hr/goals', data={'csrf_token': self._csrf(), 'goal_id': self.goal.id,
                                            'progress': '40', 'note': 'half way'})
        self.assertEqual(self.goal.progress, 40)
        self.url_open('/my/hr/kudos', data={'csrf_token': self._csrf(), 'to_employee_id': self.emp_other.id,
                                            'badge': 'clean_kitchen', 'message': 'Spotless station!'})
        self.assertEqual(self.env['bs.hr.kudos'].search_count([('to_employee_id', '=', self.emp_other.id)]), 1)

        # manager of another team -> 404
        self.authenticate('ess_mgr2', 'ess_mgr2_pw')
        self.assertEqual(self.url_open('/my/hr/team/appraisal/%s' % self.appr.id).status_code, 404)
        res = self.url_open('/my/hr/team/appraisal/%s' % self.appr.id,
                            data={'csrf_token': self._csrf(), 'action': 'done'})
        self.assertEqual(res.status_code, 404)

        # the real manager completes the review
        self.authenticate('ess_mgr', 'ess_mgr_pw')
        for url in ('/my/hr/team/appraisals', '/my/hr/team/appraisals?state=all',
                    '/my/hr/team/appraisal/%s' % self.appr.id):
            self.assertEqual(self.url_open(url).status_code, 200, url)
        data = {'csrf_token': self._csrf(), 'action': 'done', 'manager_comment': 'Keep it up'}
        for line in self.appr.line_ids:
            data['score_%s' % line.id] = '5'
        self.url_open('/my/hr/team/appraisal/%s' % self.appr.id, data=data)
        self.assertEqual(self.appr.state, 'done')
        self.assertEqual(self.appr.manager_comment, 'Keep it up')

        # employee sees the result, acknowledges and appeals
        self.authenticate('ess_emp', 'ess_emp_pw')
        res = self.url_open('/my/hr/appraisal/%s' % self.appr.id)
        self.assertEqual(res.status_code, 200)
        self.assertIn('Final score', res.text)
        self.url_open('/my/hr/appraisal/%s' % self.appr.id, data={'csrf_token': self._csrf(), 'action': 'acknowledge'})
        self.assertTrue(self.appr.employee_ack_date)
        self.url_open('/my/hr/appraisal/%s' % self.appr.id,
                      data={'csrf_token': self._csrf(), 'action': 'appeal', 'appeal_reason': 'Unfair'})
        self.assertEqual(self.appr.state, 'appealed')
