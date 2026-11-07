from datetime import date, datetime, timedelta

from freezegun import freeze_time

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged

from odoo.addons.bs_hr_ess_base.tests.common import EssCommon


class AppraisalCommon(EssCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.calendar = cls.env['resource.calendar'].create({
            'name': 'Test 40h', 'tz': 'UTC', 'company_id': cls.company.id,
            'attendance_ids': [
                (0, 0, {'name': 'D%s %s' % (d, p), 'dayofweek': str(d), 'hour_from': f, 'hour_to': t,
                        'day_period': p})
                for d in range(5) for (f, t, p) in ((8, 12, 'morning'), (13, 17, 'afternoon'))
            ],
        })
        (cls.emp_employee | cls.emp_manager | cls.emp_other).write({
            'resource_calendar_id': cls.calendar.id, 'tz': 'UTC'})
        cls.template = cls.env.ref('bs_hr_ess_appraisal.template_restaurant')
        cls.probation_template = cls.env.ref('bs_hr_ess_appraisal.template_probation')
        cls.Appraisal = cls.env['bs.hr.ess.appraisal']

    def _appraisal(self, employee=None, **vals):
        return self.Appraisal.create({
            'employee_id': (employee or self.emp_employee).id, 'template_id': self.template.id,
            'date_from': '2026-03-02', 'date_to': '2026-03-06', **vals})


@tagged('post_install', '-at_install', 'bs_hr_ess')
class TestAppraisal(AppraisalCommon):

    def test_template_weights(self):
        self.assertEqual(self.template.total_weight, 100)
        self.assertEqual(self.probation_template.total_weight, 100)
        with self.assertRaises(ValidationError):
            self.env['bs.hr.appraisal.template'].create({
                'name': 'Bad', 'line_ids': [(0, 0, {
                    'competency_id': self.env.ref('bs_hr_ess_appraisal.competency_speed').id, 'weight': 50})]})

    def test_cycle_generation(self):
        dept = self.env['hr.department'].create({'name': 'Kitchen'})
        self.emp_employee.department_id = dept
        cycle = self.env['bs.hr.appraisal.cycle'].create({
            'name': 'H1 2026', 'cycle_type': 'semi_annual', 'date_from': '2026-01-01', 'date_to': '2026-06-30',
            'template_id': self.template.id, 'selection_mode': 'branches',
            'work_location_ids': [(6, 0, self.branch.ids)],
        })
        cycle.action_generate_appraisals()
        self.assertEqual(cycle.state, 'open')
        apprs = cycle.appraisal_ids
        self.assertEqual(apprs.employee_id, self.emp_manager | self.emp_employee | self.emp_other)
        emp_appr = apprs.filtered(lambda a: a.employee_id == self.emp_employee)
        self.assertEqual(emp_appr.manager_id, self.emp_manager)
        self.assertEqual(len(emp_appr.line_ids), 6)
        self.assertEqual(emp_appr.state, 'self_review')
        self.assertEqual(emp_appr.kpi_weight, self.template.kpi_weight)
        self.assertTrue(self.env['bs.hr.notification'].search([
            ('employee_id', '=', self.emp_employee.id), ('res_model', '=', emp_appr._name)]))
        # idempotent
        cycle.action_generate_appraisals()
        self.assertEqual(len(cycle.appraisal_ids), 3)
        # department selection
        cycle2 = self.env['bs.hr.appraisal.cycle'].create({
            'name': 'Kitchen 2026', 'date_from': '2026-01-01', 'date_to': '2026-12-31',
            'template_id': self.template.id, 'selection_mode': 'departments',
            'department_ids': [(6, 0, dept.ids)],
        })
        cycle2.action_generate_appraisals()
        self.assertEqual(cycle2.appraisal_ids.employee_id, self.emp_employee)

    def test_weighted_score_and_rating(self):
        appr = self._appraisal(kpi_weight=0)
        lines = appr.line_ids
        lines.write({'manager_score': '5'})
        self.assertEqual(appr.final_score, 100)
        self.assertEqual(appr.rating, 'exceptional')
        lines.write({'manager_score': '3'})
        self.assertEqual(appr.final_score, 60)
        self.assertEqual(appr.rating, 'meets')
        # weights: hygiene (25) at 5, the others at 1 -> (25*5 + 75*1) / 500 = 40 %
        lines.write({'manager_score': '1'})
        lines.filtered(lambda l: l.competency_id == self.env.ref('bs_hr_ess_appraisal.competency_hygiene')) \
            .manager_score = '5'
        self.assertEqual(appr.final_score, 40)
        self.assertEqual(appr.rating, 'below')
        lines.write({'manager_score': '4'})
        self.assertEqual(appr.rating, 'exceeds')
        # KPI weight: 80 % competencies (80) + 20 % attendance (50) = 74
        appr.write({'kpi_weight': 20, 'attendance_rate': 50})
        self.assertEqual(appr.final_score, 74)
        self.assertEqual(appr.rating, 'meets')
        lines.write({'manager_score': '1'})
        appr.kpi_weight = 0
        self.assertEqual(appr.rating, 'unsatisfactory')

    @freeze_time('2026-03-10')
    def test_kpi_attendance_rate_and_late(self):
        Att = self.env['hr.attendance']
        emp = self.emp_employee
        # Mon 2 -> Fri 6 March 2026 : 5 expected days; attended 4, two late (> 8:10)
        Att.create([
            {'employee_id': emp.id, 'check_in': datetime(2026, 3, 2, 8, 0), 'check_out': datetime(2026, 3, 2, 17, 0)},
            {'employee_id': emp.id, 'check_in': datetime(2026, 3, 3, 8, 25), 'check_out': datetime(2026, 3, 3, 17, 0)},
            {'employee_id': emp.id, 'check_in': datetime(2026, 3, 4, 8, 9), 'check_out': datetime(2026, 3, 4, 17, 0)},
            {'employee_id': emp.id, 'check_in': datetime(2026, 3, 5, 9, 30), 'check_out': datetime(2026, 3, 5, 12, 0)},
            # second attendance of the same day does not count as late
            {'employee_id': emp.id, 'check_in': datetime(2026, 3, 5, 13, 30), 'check_out': datetime(2026, 3, 5, 17, 0)},
            # weekend attendance is ignored
            {'employee_id': emp.id, 'check_in': datetime(2026, 3, 7, 8, 0), 'check_out': datetime(2026, 3, 7, 12, 0)},
        ])
        appr = self._appraisal()
        appr.action_refresh_kpis()
        self.assertEqual(appr.expected_days, 5)
        self.assertEqual(appr.attended_days, 4)
        self.assertEqual(appr.attendance_rate, 80)
        self.assertEqual(appr.late_count, 2)
        self.assertEqual(appr.disciplinary_count, 0)
        self.assertTrue(appr.kpi_date)

    @freeze_time('2026-03-10')
    def test_workflow_and_appeal(self):
        appr = self._appraisal()
        emp_env = appr.with_user(self.user_employee).sudo()
        with self.assertRaises(ValidationError):
            emp_env.action_submit_self_review()
        appr.line_ids.write({'self_score': '4'})
        emp_env.action_submit_self_review()
        self.assertEqual(appr.state, 'manager_review')
        self.assertTrue(appr.kpi_date, 'KPIs are frozen when the manager review starts')
        self.assertTrue(appr.activity_ids.filtered(lambda a: a.user_id == self.user_manager))
        # the employee cannot complete his own review
        with self.assertRaises(AccessError):
            appr.with_user(self.user_other).sudo().action_manager_done()
        # manager (internal) scores through ACL + record rules
        mgr_appr = appr.with_user(self.user_manager)
        mgr_appr.line_ids.write({'manager_score': '4'})
        with self.assertRaises(AccessError):
            mgr_appr.line_ids[:1].write({'self_score': '1'})
        mgr_appr.action_manager_done()
        self.assertEqual(appr.state, 'done')
        self.assertEqual(appr.done_date, date(2026, 3, 10))
        self.assertTrue(appr.can_appeal)
        emp_env.action_acknowledge()
        self.assertTrue(appr.employee_ack_date)
        with freeze_time('2026-03-30'):
            appr.invalidate_recordset(['can_appeal'])
            self.assertFalse(appr.can_appeal)
        appr.invalidate_recordset(['can_appeal'])
        emp_env.action_appeal('The attendance KPI ignores my approved overtime.')
        self.assertEqual(appr.state, 'appealed')
        if 'bs.hr.grievance' not in self.env:
            self.assertTrue(appr.activity_ids.filtered(lambda a: a.user_id.has_group(
                'bs_hr_ess_base.group_ess_hr_officer')))

    def test_record_rules(self):
        appr = self._appraisal()
        other = self._appraisal(employee=self.emp_other)
        visible = self.Appraisal.with_user(self.user_manager).search([])
        self.assertIn(appr, visible)
        self.assertNotIn(other, visible)
        self.assertEqual(self.Appraisal.with_user(self.user_hr).search_count([('id', 'in', (appr | other).ids)]), 2)

    def test_probation_cron(self):
        today = date.today()
        self.emp_employee.sudo().write({'trial_date_end': today + timedelta(days=10)})
        self.emp_other.sudo().write({'trial_date_end': today + timedelta(days=40)})
        created = self.Appraisal._cron_probation_reviews()
        self.assertEqual(created.employee_id, self.emp_employee)
        self.assertTrue(created.is_probation)
        self.assertEqual(created.template_id, self.probation_template)
        self.assertEqual(created.probation_end_date, today + timedelta(days=10))
        activity = created.activity_ids.filtered(lambda a: a.user_id == self.user_manager)
        self.assertTrue(activity)
        self.assertIn('Decide confirmation before', activity.summary)
        # not twice
        self.assertFalse(self.Appraisal._cron_probation_reviews())
        # probation decision required
        created.write({'state': 'manager_review'})
        created.line_ids.write({'manager_score': '3'})
        with self.assertRaises(ValidationError):
            created.action_manager_done()
        created.probation_decision = 'confirm'
        created.action_manager_done()
        self.assertEqual(created.state, 'done')

    def test_kudos_daily_limit(self):
        Kudos = self.env['bs.hr.kudos']
        for i in range(5):
            Kudos.create({'from_employee_id': self.emp_employee.id, 'to_employee_id': self.emp_other.id,
                          'message': 'Thanks %s' % i, 'badge': 'teamwork'})
        self.assertEqual(self.env['bs.hr.notification'].search_count([
            ('employee_id', '=', self.emp_other.id), ('res_model', '=', 'bs.hr.kudos')]), 5)
        with self.assertRaises(ValidationError):
            Kudos.create({'from_employee_id': self.emp_employee.id, 'to_employee_id': self.emp_manager.id,
                          'message': 'One too many'})
        with self.assertRaises(ValidationError):
            Kudos.create({'from_employee_id': self.emp_other.id, 'to_employee_id': self.emp_other.id,
                          'message': 'Me'})
        other_company = self.env['res.company'].create({'name': 'Other Co'})
        stranger = self.env['hr.employee'].create({'name': 'Stranger', 'company_id': other_company.id})
        with self.assertRaises(ValidationError):
            Kudos.create({'from_employee_id': self.emp_other.id, 'to_employee_id': stranger.id,
                          'message': 'Hi'})

    def test_goal_progress(self):
        goal = self.env['bs.hr.goal'].create({'employee_id': self.emp_employee.id, 'name': 'Upsell desserts'})
        self.assertEqual(goal.manager_id, self.emp_manager)
        goal._ess_update_progress(150, 'done')
        self.assertEqual(goal.progress, 100)
        goal.action_achieved()
        with self.assertRaises(ValidationError):
            goal._ess_update_progress(50)
