import json

from odoo.tests import HttpCase, new_test_user, tagged


@tagged('post_install', '-at_install', 'bs_hr_ess')
class TestEssAttendancePortal(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, login='ess_att_portal', password='ess_att_portal',
                                 groups='base.group_portal')
        partner = cls.env['res.partner'].create({'name': 'Kiosk branch'})
        cls.branch = cls.env['hr.work.location'].create({
            'name': 'Kiosk branch', 'address_id': partner.id, 'ess_use_qr': True, 'ess_use_geo': False,
            'ess_latitude': 24.69, 'ess_longitude': 46.68})
        cls.employee = cls.env['hr.employee'].create({
            'name': 'Portal punch', 'user_id': cls.user.id, 'work_location_id': cls.branch.id})

    def jsonrpc(self, url, params):
        res = self.url_open(url, data=json.dumps({'jsonrpc': '2.0', 'method': 'call', 'params': params}),
                            headers={'Content-Type': 'application/json'})
        return res.json().get('result')

    def test_pages(self):
        self.authenticate('ess_att_portal', 'ess_att_portal')
        for url in ('/my/hr/attendance', '/my/hr/attendance/correction', '/my/hr/attendance/face',
                    '/my/hr/attendance/qr?c=%s' % self.branch._ess_qr_code(), '/my/hr'):
            res = self.url_open(url)
            self.assertEqual(res.status_code, 200, url)

    def test_punch_rpc(self):
        self.authenticate('ess_att_portal', 'ess_att_portal')
        result = self.jsonrpc('/my/hr/attendance/punch', {
            'qr_code': self.branch._ess_qr_code(), 'device_token': 'z' * 32})
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.employee.attendance_state, 'checked_in')

    def test_kiosk(self):
        url = '/hr/kiosk/%s' % self.branch.ess_kiosk_token
        res = self.url_open(url)
        self.assertEqual(res.status_code, 200)
        self.assertIn('activation key', res.text)
        # code endpoint refused before activation
        self.assertFalse(self.jsonrpc(url + '/code', {})['ok'])
        page = self.url_open(url).text
        csrf = page.split('name="csrf_token" value="')[1].split('"')[0]
        res = self.url_open(url, data={'key': self.branch.sudo().ess_kiosk_key, 'csrf_token': csrf})
        self.assertEqual(res.status_code, 200)
        result = self.jsonrpc(url + '/code', {})
        self.assertTrue(result['ok'])
        self.assertIn('/my/hr/attendance/qr?c=', result['value'])
        self.assertEqual(self.url_open('/hr/kiosk/doesnotexist0000').status_code, 404)

    def test_browser_js(self):
        """Interactions really run in Chrome (no JS error, clock ticking, kiosk QR loaded)."""
        code = """
            const wait = () => {
                const clock = document.querySelector('[data-ess-clock]');
                if (clock && clock.textContent !== '--:--') { console.log('test successful'); }
                else { setTimeout(wait, 200); }
            };
            wait();
        """
        self.browser_js('/my/hr/attendance', code, login='ess_att_portal', timeout=60)

    def test_browser_kiosk(self):
        url = '/hr/kiosk/%s' % self.branch.ess_kiosk_token
        cookie = self.branch._ess_kiosk_cookie_value()
        code = """
            const wait = () => {
                const img = document.querySelector('[data-ess-qr]');
                if (img && img.src.startsWith('data:image') && img.dataset.value.includes('attendance/qr')) { console.log('test successful'); }
                else { setTimeout(wait, 200); }
            };
            wait();
        """
        self.opener.cookies.set('bs_ess_kiosk_%s' % self.branch.id, cookie)
        self.browser_js(url, code, cookies={'bs_ess_kiosk_%s' % self.branch.id: cookie}, timeout=60)
