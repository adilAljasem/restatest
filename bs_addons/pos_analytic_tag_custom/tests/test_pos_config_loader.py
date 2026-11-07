from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPosConfigLoader(TransactionCase):

    def test_pos_config_loader_keeps_all_installed_fields(self):
        config = self.env['pos.config'].search([], limit=1)
        if not config:
            self.skipTest('No POS config in database')

        # In Odoo 19 an empty list intentionally asks ORM read() to return all
        # accessible fields. Replacing it with a partial whitelist drops fields
        # added by other installed modules, such as pos_restaurant.floor_ids.
        self.assertEqual(self.env['pos.config']._load_pos_data_fields(config), [])

        payload = self.env['pos.config']._load_pos_data_search_read({}, config)
        self.assertTrue(payload)
        self.assertIn('analytic_account_id', payload[0])
        if 'floor_ids' in self.env['pos.config']._fields:
            self.assertIn('floor_ids', payload[0])
