from lxml import etree
from psycopg2 import IntegrityError

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import Form, TransactionCase, new_test_user, tagged
from odoo.tools import mute_logger


@tagged('post_install', '-at_install')
class TestInventoryTemplate(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.location = cls.env['stock.location'].create({
            'name': 'Template count location',
            'usage': 'internal',
            'company_id': cls.env.company.id,
        })
        cls.products = cls.env['product.product'].create([
            {'name': name, 'type': 'consu', 'is_storable': True}
            for name in ('Template product A', 'Template product B', 'Other product')
        ])
        cls.template = cls.env['stock.inventory.template'].create({
            'name': 'Inventory template test',
            'code': 'IT-001',
            'line_ids': [Command.create({'product_id': product.id})
                         for product in cls.products[:2]],
        })
        cls.stock_user = new_test_user(
            cls.env, login='inventory_template_user',
            groups='stock.group_stock_user,inventory_stock_adjustments.group_stock_adjustments_user',
        )
        cls.stock_manager = new_test_user(
            cls.env, login='inventory_template_manager',
            groups='stock.group_stock_manager,stock.group_production_lot,stock.group_stock_multi_locations',
        )

    def _inventory(self, **values):
        return self.env['stock.inventory'].create({
            'name': 'Template count',
            'location_id': self.location.id,
            'filter': 'template',
            'template_id': self.template.id,
            **values,
        })

    def test_load_on_start_including_zero_stock(self):
        self.env['stock.quant']._update_available_quantity(self.products[0], self.location, 7)
        self.env['stock.quant']._update_available_quantity(self.products[2], self.location, 99)
        inventory = self._inventory()
        self.assertFalse(inventory.add_onhand_zero)
        self.assertFalse(inventory.line_ids)
        inventory.with_user(self.stock_user).prepare_inventory_stock_counting()
        self.assertEqual(inventory.state, 'confirm')
        self.assertEqual(inventory.line_ids.product_id, self.products[:2])
        quantities = {line.product_id: line.quantity for line in inventory.line_ids}
        self.assertEqual(quantities, {self.products[0]: 7, self.products[1]: 0})
        self.assertEqual(inventory.line_ids.location_id, self.location)

    def test_only_selected_location_is_counted(self):
        other_location = self.location.copy({'name': 'Other location'})
        self.env['stock.quant']._update_available_quantity(self.products[0], other_location, 19)
        inventory = self._inventory()
        inventory.prepare_inventory_stock_counting()
        self.assertEqual(len(inventory.line_ids), 2)
        self.assertFalse(any(inventory.line_ids.mapped('location_quantity')))
        self.assertFalse(any(inventory.line_ids.mapped('quantity')))

    def test_hidden_lots_are_preserved(self):
        self.products[0].tracking = 'lot'
        lots = self.env['stock.lot'].create([
            {'name': name, 'product_id': self.products[0].id, 'company_id': self.env.company.id}
            for name in ('Template lot A', 'Template lot B')
        ])
        for lot in lots:
            self.env['stock.quant']._update_available_quantity(
                self.products[0], self.location, 3, lot_id=lot,
            )
        inventory = self._inventory()
        inventory.prepare_inventory_stock_counting()
        self.assertEqual(inventory.line_ids.prod_lot_id, lots)
        self.assertEqual(len(inventory.line_ids), 3)
        self.assertEqual(sum(inventory.line_ids.mapped('location_quantity')), 6)
        self.assertEqual(inventory.line_ids.filtered('prod_lot_id').mapped('quantity'), [3, 3])

    def test_template_cannot_be_missing(self):
        with self.assertRaisesRegex(ValidationError, 'select an inventory template'), self.cr.savepoint():
            self._inventory(template_id=False)

    def test_empty_template_does_not_start_counting(self):
        self.template.line_ids.unlink()
        inventory = self._inventory()
        with self.assertRaisesRegex(UserError, 'at least one product'):
            inventory.prepare_inventory_stock_counting()
        self.assertEqual(inventory.state, 'draft')

    def test_inactive_templates_are_hidden_and_rejected(self):
        inventory = self._inventory()
        self.template.is_active = False
        self.assertFalse(self.env['stock.inventory.template'].search([('id', '=', self.template.id)]))
        with self.assertRaisesRegex(UserError, 'active inventory template'):
            inventory.prepare_inventory_stock_counting()
        self.assertEqual(
            self.env['stock.inventory.template'].search([
                ('id', '=', self.template.id), ('active', '=', False),
            ]), self.template,
        )

    def test_repeat_start_does_not_duplicate_lines(self):
        inventory = self._inventory()
        inventory.prepare_inventory_stock_counting()
        original_lines = inventory.line_ids
        with self.assertRaisesRegex(UserError, 'draft inventory'):
            inventory.prepare_inventory_stock_counting()
        self.assertEqual(inventory.line_ids, original_lines)

    def test_existing_lines_are_not_silently_overwritten(self):
        inventory = self._inventory(line_ids=[Command.create({'product_id': self.products[2].id})])
        with self.assertRaisesRegex(UserError, 'Remove existing inventory lines'):
            inventory.prepare_inventory_stock_counting()
        self.assertEqual(inventory.line_ids.product_id, self.products[2])

    def test_changing_template_before_start_uses_new_products(self):
        inventory = self._inventory()
        replacement = self.template.copy({
            'line_ids': [Command.create({'product_id': self.products[2].id})],
        })
        inventory.template_id = replacement
        inventory.prepare_inventory_stock_counting()
        self.assertEqual(inventory.line_ids.product_id, self.products[2])
        replacement.line_ids.unlink()
        self.assertEqual(inventory.line_ids.product_id, self.products[2])

    def test_filter_onchange_clears_template(self):
        inventory = self.env['stock.inventory'].new({
            'filter': 'partial', 'template_id': self.template.id,
        })
        inventory.onchange_filter()
        self.assertFalse(inventory.template_id)

    def test_non_storable_products_are_rejected(self):
        service = self.env['product.product'].create({'name': 'Template service', 'type': 'service'})
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.template.write({'line_ids': [Command.create({'product_id': service.id})]})

    def test_archived_product_is_rejected_at_start(self):
        self.products[0].active = False
        inventory = self._inventory()
        with self.assertRaisesRegex(UserError, 'active storable products'):
            inventory.prepare_inventory_stock_counting()

    @mute_logger('odoo.sql_db')
    def test_duplicate_products_are_rejected(self):
        with self.assertRaises(IntegrityError), self.cr.savepoint():
            self.template.write({'line_ids': [Command.create({'product_id': self.products[0].id})]})

    def test_users_read_templates_managers_maintain_them(self):
        template = self.template.with_user(self.stock_user)
        self.assertTrue(template.read(['name', 'line_ids']))
        self.assertTrue(template.line_ids.read(['product_id']))
        with self.assertRaises(AccessError):
            template.write({'name': 'Not allowed'})
        with self.assertRaises(AccessError):
            template.line_ids[0].unlink()
        manager_template = self.env['stock.inventory.template'].with_user(self.stock_manager).create({
            'name': 'Manager template',
            'line_ids': [Command.create({'product_id': self.products[0].id})],
        })
        manager_template.write({'code': 'EDITED'})
        manager_template.unlink()

    def test_company_boundaries(self):
        other_company = self.env['res.company'].create({'name': 'Other template company'})
        other_template = self.env['stock.inventory.template'].with_company(other_company).create({
            'name': 'Other company template',
        })
        with self.assertRaises(UserError), self.cr.savepoint():
            self._inventory(template_id=other_template.id)
        with self.assertRaises(AccessError):
            other_template.with_user(self.stock_user).read(['name'])
        other_product = self.env['product.product'].with_company(other_company).create({
            'name': 'Other company product', 'type': 'consu', 'is_storable': True,
            'company_id': other_company.id,
        })
        with self.assertRaises(UserError), self.cr.savepoint():
            self.template.write({'line_ids': [Command.create({'product_id': other_product.id})]})

    def test_columns_hidden_and_menu_location(self):
        view = self.env['stock.inventory'].with_user(self.stock_manager).get_view(
            self.env.ref('inventory_stock_adjustments.inventory_adjustments_form_view').id,
            'form',
        )
        arch = etree.fromstring(view['arch'])
        for name in ('location_id', 'lot_id', 'lot_serial', 'prod_lot_id', 'currency_id'):
            field = arch.xpath(f"//field[@name='line_ids']/list/field[@name='{name}']")
            self.assertEqual(len(field), 1)
            self.assertEqual(field[0].get('column_invisible'), 'True')
        menu = self.env.ref('inventory_stock_adjustments.menu_stock_inventory_template')
        self.assertEqual(menu.parent_id, self.env.ref('stock.menu_stock_config_settings'))

    def test_inventory_detail_columns_are_optional_and_shown_by_default(self):
        view = self.env['stock.inventory'].with_user(self.stock_manager).get_view(
            self.env.ref('inventory_stock_adjustments.inventory_adjustments_form_view').id,
            'form',
        )
        arch = etree.fromstring(view['arch'])
        for name in (
            'product_id', 'product_uom_id', 'location_quantity',
            'reserved_quantity', 'available_quantity', 'inventory_quantity',
            'different', 'price_value', 'state',
        ):
            field = arch.xpath(f"//field[@name='line_ids']/list/field[@name='{name}']")
            self.assertEqual(len(field), 1, name)
            self.assertEqual(field[0].get('optional'), 'show', name)
            self.assertNotEqual(field[0].get('column_invisible'), 'True', name)
        for name in ('real_time_counted_quantity', 'qty_after_counting_date'):
            field = arch.xpath(f"//field[@name='line_ids']/list/field[@name='{name}']")
            self.assertEqual(len(field), 1, name)
            self.assertEqual(field[0].get('optional'), 'show', name)
            self.assertEqual(field[0].get('column_invisible'), 'not parent.force_counting_date', name)

    def test_template_form_and_inventory_form(self):
        with Form(self.env['stock.inventory.template'].with_user(self.stock_manager)) as form:
            form.name = 'Template created from form'
            form.code = 'FORM-001'
            self.assertTrue(form.is_active)
            with form.line_ids.new() as line:
                line.product_id = self.products[0]
        template = form.record
        with Form(self.env['stock.inventory'].with_user(self.stock_user)) as form:
            form.name = 'Template inventory from form'
            form.location_id = self.location
            form.filter = 'template'
            form.template_id = template
        inventory = form.record
        self.assertFalse(inventory.line_ids)
        inventory.prepare_inventory_stock_counting()
        self.assertEqual(inventory.line_ids.product_id, self.products[0])

    def test_serial_lot_survives_hidden_column(self):
        self.products[0].tracking = 'serial'
        lot = self.env['stock.lot'].create({
            'name': 'Template serial', 'product_id': self.products[0].id,
            'company_id': self.env.company.id,
        })
        self.env['stock.quant']._update_available_quantity(
            self.products[0], self.location, 1, lot_id=lot,
        )
        inventory = self._inventory()
        inventory.prepare_inventory_stock_counting()
        line = inventory.line_ids.filtered(lambda item: item.product_id == self.products[0])
        self.assertTrue(line.lot_serial)
        self.assertEqual(line.prod_lot_id, lot)
        self.assertEqual(line.quantity, 1)
