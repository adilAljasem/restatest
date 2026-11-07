from datetime import datetime

from odoo import Command, fields
from odoo.addons.stock_account.tests.common import TestStockValuationCommon
from odoo.addons.stock.tests.common import TestStockCommon
from odoo.exceptions import ValidationError
from odoo.tests import new_test_user, tagged


@tagged('post_install', '-at_install')
class TestBackdatedInventory(TestStockCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.inventory = cls.env['stock.inventory'].create({
            'name': 'Backdated inventory test',
            'location_id': cls.stock_location.id,
            'filter': 'partial',
            'force_counting_date': True,
            'counting_date': datetime(2024, 1, 1, 8, 0, 0),
            'state': 'confirm',
        })
        cls.inventory_line = cls.env['stock.inventory.line'].create({
            'stock_inventory_id': cls.inventory.id,
            'product_id': cls.productA.id,
            'real_time_counted_quantity': 10.0,
        })

    def _create_done_move(self, source, destination, quantity, move_date):
        move = self._create_move(
            self.productA,
            source,
            destination,
            product_uom_qty=quantity,
        )
        move._action_confirm()
        move.quantity = quantity
        move.picked = True
        move._action_done()
        move.move_line_ids.date = move_date
        return move

    def test_net_movements_and_recalculation(self):
        movement_date = datetime(2024, 1, 2, 8, 0, 0)
        self._create_done_move(
            self.supplier_location,
            self.shelf_1,
            10.0,
            movement_date,
        )
        self._create_done_move(self.shelf_1, self.shelf_2, 2.0, movement_date)
        self._create_done_move(
            self.shelf_2,
            self.customer_location,
            3.0,
            movement_date,
        )

        action = self.inventory.action_count_with_back_date()
        self.assertEqual(self.inventory_line.qty_after_counting_date, 7.0)
        self.assertEqual(self.inventory_line.inventory_quantity, 17.0)
        self.assertEqual(action['type'], 'ir.actions.act_window_close')
        self.assertTrue(action['infos']['noReload'])

        self.inventory.action_count_with_back_date()
        self.assertEqual(self.inventory_line.qty_after_counting_date, 7.0)
        self.assertEqual(self.inventory_line.inventory_quantity, 17.0)

    def test_formula_is_independent_from_button(self):
        self.inventory_line.write({
            'real_time_counted_quantity': 10.0,
            'qty_after_counting_date': -5.0,
        })
        self.assertEqual(self.inventory_line.inventory_quantity, 5.0)

        self.inventory_line.real_time_counted_quantity = 12.0
        self.assertEqual(self.inventory_line.inventory_quantity, 7.0)

    def test_set_zero_keeps_post_count_movements(self):
        self.inventory_line.make_it_zero = True
        self.inventory_line.qty_after_counting_date = -3.0
        self.inventory_line.onchange_make_zero()

        self.assertEqual(self.inventory_line.real_time_counted_quantity, 0.0)
        self.assertEqual(self.inventory_line.inventory_quantity, -3.0)

    def test_formula_does_not_change_normal_inventory_flow(self):
        normal_inventory = self.env['stock.inventory'].create({
            'name': 'Normal inventory test',
            'location_id': self.stock_location.id,
            'filter': 'partial',
            'state': 'confirm',
        })
        normal_line = self.env['stock.inventory.line'].create({
            'stock_inventory_id': normal_inventory.id,
            'product_id': self.productB.id,
            'inventory_quantity': 4.0,
        })
        normal_line.write({
            'real_time_counted_quantity': 10.0,
            'qty_after_counting_date': -3.0,
        })
        self.assertEqual(normal_line.inventory_quantity, 4.0)

    def test_future_counting_date_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.inventory.counting_date = datetime(2099, 1, 1, 0, 0, 0)

    def test_counting_date_is_required_by_action(self):
        self.inventory.counting_date = False
        with self.assertRaisesRegex(ValidationError, 'Please enter the Counting Date'):
            self.inventory.action_count_with_back_date()

    def test_validation_uses_counting_date_for_inventory_move(self):
        self.inventory.action_done()

        inventory_moves = self.inventory.move_ids
        self.assertTrue(inventory_moves)
        self.assertEqual(inventory_moves.stock_inventory_id, self.inventory)
        self.assertEqual(inventory_moves.date, self.inventory.counting_date)
        self.assertEqual(
            inventory_moves.move_line_ids.date,
            self.inventory.counting_date,
        )

    def test_normal_validation_keeps_execution_date(self):
        normal_inventory = self.env['stock.inventory'].create({
            'name': 'Normal inventory validation date test',
            'location_id': self.stock_location.id,
            'filter': 'partial',
            'state': 'confirm',
        })
        self.env['stock.inventory.line'].create({
            'stock_inventory_id': normal_inventory.id,
            'product_id': self.productB.id,
            'inventory_quantity': 4.0,
        })

        validation_started_at = fields.Datetime.now()
        normal_inventory.action_done()
        validation_finished_at = fields.Datetime.now()

        self.assertTrue(normal_inventory.move_ids)
        for move in normal_inventory.move_ids:
            self.assertGreaterEqual(move.date, validation_started_at)
            self.assertLessEqual(move.date, validation_finished_at)
            self.assertEqual(move.move_line_ids.date, move.date)

    def test_analytic_distribution_targets_adjustment_account_only(self):
        plan = self.env['account.analytic.plan'].create({
            'name': 'Inventory Cost Centers',
        })
        analytic_account = self.env['account.analytic.account'].create({
            'name': 'Warehouse Cost Center',
            'plan_id': plan.id,
        })
        distribution = {str(analytic_account.id): 100.0}
        self.inventory.analytic_distribution = distribution

        self.inventory.action_done()
        inventory_move = self.inventory.move_ids
        self.assertTrue(inventory_move)
        inventory_move.ensure_one()

        adjustment_account = self.env['account.account'].create({
            'name': 'Inventory Adjustment Test',
            'code': 'INV.ADJ.TEST',
            'account_type': 'expense',
        })
        inventory_location = (
            inventory_move.location_id | inventory_move.location_dest_id
        ).filtered(lambda location: location.usage == 'inventory')
        inventory_location.valuation_account_id = adjustment_account
        valuation_account = inventory_move.product_id._get_product_accounts()[
            'stock_valuation'
        ]

        aml_vals = inventory_move._get_account_move_line_vals()
        adjustment_vals = [
            vals for vals in aml_vals
            if vals['account_id'] == adjustment_account.id
        ]
        valuation_vals = [
            vals for vals in aml_vals
            if vals['account_id'] == valuation_account.id
        ]

        self.assertEqual(len(adjustment_vals), 1)
        self.assertEqual(adjustment_vals[0]['analytic_distribution'], distribution)
        self.assertEqual(len(valuation_vals), 1)
        self.assertNotIn('analytic_distribution', valuation_vals[0])


@tagged('post_install', '-at_install')
class TestInventoryJournalBatch(TestStockValuationCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.second_product = cls.env['product.product'].create({
            **cls.product_common_vals,
            'name': 'Second adjustment product',
            'standard_price': 20.0,
            'categ_id': cls.category_standard_auto.id,
        })

    def _inventory(self, *, force_counting_date=False, accounting_date=False):
        line_values = [
            (self.product_standard_auto, 5.0),
            (self.second_product, 3.0),
        ]
        return self.env['stock.inventory'].create({
            'name': 'Multi-product adjustment',
            'location_id': self.stock_location.id,
            'filter': 'partial',
            'state': 'confirm',
            'force_counting_date': force_counting_date,
            'counting_date': datetime(2024, 1, 1, 8) if force_counting_date else False,
            'accounting_date': accounting_date,
            'line_ids': [Command.create({
                'product_id': product.id,
                ('real_time_counted_quantity' if force_counting_date else 'inventory_quantity'): quantity,
            }) for product, quantity in line_values],
        })

    def test_backdated_products_create_one_balanced_entry(self):
        self._use_inventory_location_accounting()
        accounting_date = datetime(2024, 1, 2, 8)
        inventory = self._inventory(force_counting_date=True, accounting_date=accounting_date)

        inventory.action_done()

        self.assertEqual(len(inventory.move_ids), 2)
        account_moves = inventory.move_ids.account_move_id
        self.assertEqual(len(account_moves), 1)
        self.assertEqual(account_moves.state, 'posted')
        self.assertEqual(account_moves.ref, inventory.name)
        self.assertEqual(account_moves.date, accounting_date.date())
        self.assertEqual(inventory.accounting_date, accounting_date)
        self.assertEqual(sum(account_moves.line_ids.mapped('debit')), 110.0)
        self.assertEqual(sum(account_moves.line_ids.mapped('credit')), 110.0)
        self.assertEqual(inventory.move_ids.mapped('date'), [inventory.counting_date] * 2)
        self.assertEqual(inventory.move_ids.move_line_ids.mapped('date'), [inventory.counting_date] * 2)
        self.assertEqual(
            self.env['stock.quant']._get_available_quantity(self.product_standard_auto, self.stock_location),
            5.0,
        )
        self.assertEqual(
            self.env['stock.quant']._get_available_quantity(self.second_product, self.stock_location),
            3.0,
        )

    def test_normal_products_create_one_entry_and_keep_counts(self):
        self._use_inventory_location_accounting()
        accounting_date = datetime(2024, 2, 2, 8)
        inventory = self._inventory(accounting_date=accounting_date)

        inventory.action_done()

        self.assertEqual(len(inventory.move_ids), 2)
        account_moves = inventory.move_ids.account_move_id
        self.assertEqual(len(account_moves), 1)
        self.assertEqual(account_moves.ref, inventory.name)
        self.assertEqual(account_moves.date, accounting_date.date())
        self.assertEqual(sum(account_moves.line_ids.mapped('debit')), 110.0)
        self.assertEqual(sum(account_moves.line_ids.mapped('credit')), 110.0)
        self.assertEqual(inventory.line_ids.mapped('inventory_quantity'), [5.0, 3.0])

    def test_long_inventory_name_is_not_truncated_on_account_move(self):
        self._use_inventory_location_accounting()
        inventory = self._inventory()
        inventory.name = 'Warehouse annual adjustment - ' + 'October 2026 ' * 5

        inventory.action_done()

        self.assertGreater(len(inventory.name), 43)
        self.assertEqual(len(inventory.move_ids.account_move_id), 1)
        self.assertEqual(inventory.move_ids.account_move_id.ref, inventory.name)

    def test_stock_manager_sets_reference_without_accounting_access(self):
        self._use_inventory_location_accounting()
        stock_manager = new_test_user(
            self.env,
            login='inventory_ref_stock_manager',
            groups='stock.group_stock_manager,inventory_stock_adjustments.group_stock_adjustments_manager',
        )
        inventory = self._inventory()

        inventory.with_user(stock_manager).action_done()

        self.assertEqual(len(inventory.move_ids.account_move_id), 1)
        self.assertEqual(inventory.move_ids.account_move_id.ref, inventory.name)

    def test_existing_quants_keep_balances_and_skip_unchanged_product(self):
        self._use_inventory_location_accounting()
        third_product = self.env['product.product'].create({
            **self.product_common_vals,
            'name': 'Unchanged adjustment product',
            'standard_price': 7.0,
            'categ_id': self.category_standard_auto.id,
        })
        quants = self.env['stock.quant']
        quants._update_available_quantity(self.product_standard_auto, self.stock_location, 8.0)
        quants._update_available_quantity(self.second_product, self.stock_location, 1.0)
        quants._update_available_quantity(third_product, self.stock_location, 4.0)
        inventory = self._inventory()
        self.env['stock.inventory.line'].create({
            'stock_inventory_id': inventory.id,
            'product_id': third_product.id,
            'inventory_quantity': 4.0,
        })

        inventory.action_done()

        self.assertEqual(len(inventory.move_ids), 2)
        account_moves = inventory.move_ids.account_move_id
        self.assertEqual(len(account_moves), 1)
        self.assertEqual(sum(account_moves.line_ids.mapped('debit')), 70.0)
        self.assertEqual(sum(account_moves.line_ids.mapped('credit')), 70.0)
        self.assertEqual(set(account_moves.line_ids.product_id.ids), {
            self.product_standard_auto.id, self.second_product.id,
        })
        self.assertEqual(quants._get_available_quantity(self.product_standard_auto, self.stock_location), 5.0)
        self.assertEqual(quants._get_available_quantity(self.second_product, self.stock_location), 3.0)
        self.assertEqual(quants._get_available_quantity(third_product, self.stock_location), 4.0)

    def test_lot_lines_keep_their_own_quantities_in_one_entry(self):
        self._use_inventory_location_accounting()
        product = self.env['product.product'].create({
            **self.product_common_vals,
            'name': 'Lot tracked adjustment product',
            'tracking': 'lot',
            'categ_id': self.category_standard_auto.id,
        })
        lots = self.env['stock.lot'].create([
            {'name': 'ADJ-LOT-1', 'product_id': product.id, 'company_id': self.company.id},
            {'name': 'ADJ-LOT-2', 'product_id': product.id, 'company_id': self.company.id},
        ])
        quants = self.env['stock.quant']
        quants._update_available_quantity(product, self.stock_location, 2.0, lot_id=lots[0])
        quants._update_available_quantity(product, self.stock_location, 4.0, lot_id=lots[1])
        inventory = self.env['stock.inventory'].create({
            'name': 'Lot adjustment',
            'location_id': self.stock_location.id,
            'filter': 'partial',
            'state': 'confirm',
            'line_ids': [Command.create({
                'product_id': product.id,
                'prod_lot_id': lot.id,
                'inventory_quantity': 3.0,
            }) for lot in lots],
        })

        inventory.action_done()

        self.assertEqual(len(inventory.move_ids), 2)
        self.assertEqual(len(inventory.move_ids.account_move_id), 1)
        self.assertEqual(quants._get_available_quantity(product, self.stock_location, lot_id=lots[0]), 3.0)
        self.assertEqual(quants._get_available_quantity(product, self.stock_location, lot_id=lots[1]), 3.0)

    def test_99_product_adjustment_creates_one_balanced_entry(self):
        self._use_inventory_location_accounting()
        products = self.env['product.product'].create([{
            **self.product_common_vals,
            'name': f'Counted product {number}',
            'categ_id': self.category_standard_auto.id,
        } for number in range(99)])
        inventory = self.env['stock.inventory'].create({
            'name': '99-product adjustment',
            'location_id': self.stock_location.id,
            'filter': 'partial',
            'state': 'confirm',
            'line_ids': [Command.create({
                'product_id': product.id,
                'inventory_quantity': 1.0,
            }) for product in products],
        })

        inventory.action_done()

        self.assertEqual(len(inventory.move_ids), 99)
        account_moves = inventory.move_ids.account_move_id
        self.assertEqual(len(account_moves), 1)
        self.assertEqual(sum(account_moves.line_ids.mapped('debit')), 990.0)
        self.assertEqual(sum(account_moves.line_ids.mapped('credit')), 990.0)
        self.assertEqual(sum(inventory.move_ids.mapped('quantity')), 99.0)
