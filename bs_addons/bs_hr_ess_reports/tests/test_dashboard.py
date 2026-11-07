from odoo import fields
from odoo.tests import HttpCase, tagged

from odoo.addons.bs_hr_ess_base.tests.common import EssCommon


@tagged('post_install', '-at_install', 'bs_hr_ess')
class TestEssDashboard(EssCommon):

    def test_dashboard_data(self):
        self.env['hr.attendance'].create({'employee_id': self.emp_employee.id, 'check_in': fields.Datetime.now()})
        data = self.env['bs.hr.ess.dashboard'].with_user(self.user_hr).get_data()
        keys = {t['key'] for t in data['tiles']}
        for key in ('headcount', 'saudization', 'present', 'pending', 'loans', 'doc_expired', 'grievances'):
            self.assertIn(key, keys)
        present = next(t for t in data['tiles'] if t['key'] == 'present')
        self.assertGreaterEqual(present['value'], 1)
        branch = self.env['bs.hr.ess.dashboard'].with_user(self.user_hr).get_data(location_id=self.branch.id)
        self.assertEqual([b['id'] for b in branch['branches']], [self.branch.id])
        # non HR users get nothing
        self.assertEqual(self.env['bs.hr.ess.dashboard'].with_user(self.user_manager).get_data(), {})

    def test_workforce_view_and_report(self):
        self.env.flush_all()
        rows = self.env['bs.hr.ess.workforce.report'].search([('employee_id', '=', self.emp_employee.id)])
        self.assertEqual(len(rows), 1)
        html = self.env['ir.actions.report']._render_qweb_html(
            'bs_hr_ess_reports.action_report_monthly_hr', self.env.company.ids)[0]
        self.assertIn(b'Monthly HR report', html)


@tagged('post_install', '-at_install', 'bs_hr_ess')
class TestEssDashboardUi(HttpCase):

    def test_dashboard_client_action(self):
        action = self.env.ref('bs_hr_ess_reports.action_ess_hr_dashboard')
        code = """
            const wait = () => {
                if (document.querySelector('.o_error_dialog')) { console.error('dashboard crashed'); }
                else if (document.querySelector('.o_ess_tile') && document.querySelectorAll('.o_ess_dashboard select option').length > 1) {
                    console.log('test successful');
                }
                else { setTimeout(wait, 200); }
            };
            wait();
        """
        self.browser_js('/odoo/action-%s' % action.id, code, login='admin', timeout=90)
