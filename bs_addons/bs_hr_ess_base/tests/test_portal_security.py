from odoo.tests import HttpCase, new_test_user, tagged


@tagged('post_install', '-at_install', 'bs_hr_ess')
class TestEssPortalSecurity(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_a = new_test_user(cls.env, login='ess_portal_a', password='ess_portal_a',
                                   groups='base.group_portal')
        cls.user_b = new_test_user(cls.env, login='ess_portal_b', password='ess_portal_b',
                                   groups='base.group_portal')
        cls.emp_a = cls.env['hr.employee'].create({'name': 'Portal A', 'user_id': cls.user_a.id})
        cls.emp_b = cls.env['hr.employee'].create({'name': 'Portal B', 'user_id': cls.user_b.id})
        cls.req_b = cls.env['bs.hr.profile.change'].create({
            'employee_id': cls.emp_b.id, 'new_mobile': '0500000001'})

    def test_pages_render(self):
        self.authenticate('ess_portal_a', 'ess_portal_a')
        for url in ('/my/hr', '/my/hr/requests', '/my/hr/profile', '/my/hr/profile/change',
                    '/my/hr/notifications', '/my/hr/new', '/my/hr/eos', '/my/hr/manifest.webmanifest'):
            res = self.url_open(url)
            self.assertEqual(res.status_code, 200, url)

    def test_cannot_open_other_employee_request(self):
        self.authenticate('ess_portal_a', 'ess_portal_a')
        res = self.url_open('/my/hr/request/bs.hr.profile.change/%s' % self.req_b.id)
        self.assertEqual(res.status_code, 404)
        # arbitrary model names are refused too
        res = self.url_open('/my/hr/request/res.users/%s' % self.user_b.id)
        self.assertEqual(res.status_code, 404)
        res = self.url_open('/my/hr/avatar/%s' % self.emp_b.id)
        self.assertEqual(res.status_code, 404)

    def test_own_request_and_submit(self):
        self.authenticate('ess_portal_a', 'ess_portal_a')
        res = self.url_open('/my/hr/profile/change', data={
            'new_mobile': '0555555555', 'csrf_token': self.csrf_token()})
        self.assertEqual(res.status_code, 200)
        req = self.env['bs.hr.profile.change'].search([('employee_id', '=', self.emp_a.id)])
        self.assertEqual(len(req), 1)
        self.assertIn(req.state, ('in_approval', 'approved', 'done'))
        res = self.url_open(req._ess_portal_url())
        self.assertEqual(res.status_code, 200)

    def csrf_token(self):
        return self.opener.get(self.base_url() + '/my/hr').text.split('csrf_token: "')[1].split('"')[0]


@tagged('post_install', '-at_install', 'bs_hr_ess')
class TestEssMobileAppLanding(HttpCase):
    """The Odoo iOS / Android app opens /odoo (or /web) after login."""

    APP_UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) OdooMobile (iOS)'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, login='ess_app', password='ess_app_pwd', groups='base.group_portal')
        cls.env['hr.employee'].create({'name': 'App employee', 'user_id': cls.user.id})
        cls.customer = new_test_user(cls.env, login='ess_customer', password='ess_customer', groups='base.group_portal')

    def test_employee_lands_on_ess(self):
        self.authenticate('ess_app', 'ess_app_pwd')
        for url in ('/odoo', '/web', '/my', '/'):
            res = self.url_open(url, headers={'User-Agent': self.APP_UA}, allow_redirects=False)
            self.assertIn(res.status_code, (301, 302, 303), url)
            self.assertTrue(res.headers['Location'].endswith('/my/hr'), (url, res.headers['Location']))
        res = self.url_open('/my?all=1', allow_redirects=False)
        self.assertEqual(res.status_code, 200)

    def test_non_employee_portal_unchanged(self):
        self.authenticate('ess_customer', 'ess_customer')
        res = self.url_open('/odoo', allow_redirects=False)
        self.assertTrue(res.headers['Location'].endswith('/my'), res.headers.get('Location'))

    def test_setting_off(self):
        self.env.company.ess_portal_landing = False
        self.authenticate('ess_app', 'ess_app_pwd')
        res = self.url_open('/my', allow_redirects=False)
        self.assertEqual(res.status_code, 200)
