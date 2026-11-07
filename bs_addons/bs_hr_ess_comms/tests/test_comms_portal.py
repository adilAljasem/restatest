from odoo.tests import HttpCase, new_test_user, tagged


@tagged('post_install', '-at_install', 'bs_hr_ess')
class TestCommsPortal(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_a = new_test_user(cls.env, login='comms_a', password='comms_a_pwd', groups='base.group_portal')
        cls.user_b = new_test_user(cls.env, login='comms_b', password='comms_b_pwd', groups='base.group_portal')
        partner = cls.env['res.partner'].create({'name': 'Comms branch'})
        cls.branch_a = cls.env['hr.work.location'].create({'name': 'Comms A', 'address_id': partner.id})
        cls.branch_b = cls.env['hr.work.location'].create({'name': 'Comms B', 'address_id': partner.id})
        cls.emp_a = cls.env['hr.employee'].create({'name': 'Comms A', 'user_id': cls.user_a.id,
                                                   'work_location_id': cls.branch_a.id})
        cls.emp_b = cls.env['hr.employee'].create({'name': 'Comms B', 'user_id': cls.user_b.id,
                                                   'work_location_id': cls.branch_b.id})
        Announcement = cls.env['bs.hr.announcement']
        cls.ann_a = Announcement.create({
            'name': 'Branch A circular', 'body': '<p>Only for branch A</p>', 'require_ack': True,
            'pinned': True, 'target_all': False, 'work_location_ids': [(6, 0, cls.branch_a.ids)]})
        cls.ann_b = Announcement.create({
            'name': 'Branch B news', 'body': '<p>Only for branch B</p>',
            'target_all': False, 'work_location_ids': [(6, 0, cls.branch_b.ids)]})
        cls.policy = Announcement.create({
            'name': 'Hygiene policy', 'body': '<p>Wash your hands</p>', 'announcement_type': 'policy',
            'target_all': False, 'work_location_ids': [(6, 0, cls.branch_a.ids)]})
        (cls.ann_a | cls.ann_b | cls.policy).action_publish()

    def _csrf(self):
        return self.opener.get(self.base_url() + '/my/hr').text.split('csrf_token: "')[1].split('"')[0]

    def test_pages_render(self):
        self.authenticate('comms_a', 'comms_a_pwd')
        for url in ('/my/hr', '/my/hr/news', '/my/hr/news?filter=unread', '/my/hr/news?filter=to_ack',
                    '/my/hr/policies', '/my/hr/calendar', '/my/hr/news/%s' % self.ann_a.id,
                    '/my/hr/news/%s' % self.policy.id):
            res = self.url_open(url)
            self.assertEqual(res.status_code, 200, url)
        home = self.url_open('/my/hr').text
        self.assertIn('Branch A circular', home)  # pinned on the home page
        feed = self.url_open('/my/hr/news').text
        self.assertIn('Branch A circular', feed)
        self.assertNotIn('Branch B news', feed)

    def test_non_recipient_404(self):
        self.authenticate('comms_a', 'comms_a_pwd')
        res = self.url_open('/my/hr/news/%s' % self.ann_b.id)
        self.assertEqual(res.status_code, 404)
        res = self.url_open('/my/hr/news/%s/ack' % self.ann_b.id, data={'csrf_token': self._csrf()})
        self.assertEqual(res.status_code, 404)
        self.assertFalse(self.env['bs.hr.announcement.ack'].search([('announcement_id', '=', self.ann_b.id)]))
        # drafts are not reachable even by recipients
        draft = self.env['bs.hr.announcement'].create({'name': 'Draft', 'target_all': True})
        res = self.url_open('/my/hr/news/%s' % draft.id)
        self.assertEqual(res.status_code, 404)

    def test_read_and_acknowledge(self):
        self.authenticate('comms_a', 'comms_a_pwd')
        res = self.url_open('/my/hr/news/%s' % self.ann_a.id)
        self.assertEqual(res.status_code, 200)
        self.assertIn('I have read and agree', res.text)
        ack = self.ann_a._ess_ack_for(self.emp_a)
        self.assertTrue(ack.read_date)
        self.assertFalse(ack.ack_date)
        res = self.url_open('/my/hr/news/%s/ack' % self.ann_a.id, data={'csrf_token': self._csrf()})
        self.assertEqual(res.status_code, 200)
        ack.invalidate_recordset()
        self.assertTrue(ack.ack_date)
        self.ann_a.invalidate_recordset()
        self.assertEqual(self.ann_a.ack_rate, 100.0)
