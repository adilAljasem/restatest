"""Run with --test-tags /mrp_bom_consumption_adjustment on an Odoo 19 database."""
from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import Form, tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestConsumptionAdjustment(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.warehouse = cls.env['stock.warehouse'].search([('company_id', '=', cls.company.id)], limit=1)
        Account = cls.env['account.account']
        cls.stock_account = Account.create({'name': 'Recipe stock', 'code': 'BCA100',
            'account_type': 'asset_current', 'company_ids': [Command.set(cls.company.ids)]})
        cls.loss_account = Account.create({'name': 'Recipe differences', 'code': 'BCA200',
            'account_type': 'expense', 'company_ids': [Command.set(cls.company.ids)]})
        cls.journal = cls.env['account.journal'].create({'name': 'Recipe stock journal',
            'code': 'BCA', 'type': 'general', 'company_id': cls.company.id})
        cls.company.account_stock_journal_id = cls.journal
        cls.loss_location = cls.env['stock.location'].create({'name': 'Recipe adjustment',
            'usage': 'inventory', 'company_id': cls.company.id,
            'valuation_account_id': cls.loss_account.id})
        cls.category = cls.env['product.category'].create({'name': 'Recipe components',
            'property_valuation': 'real_time', 'property_cost_method': 'standard',
            'property_stock_valuation_account_id': cls.stock_account.id,
            'recipe_adjustment_location_id': cls.loss_location.id})
        cls.raw = cls.env['product.product'].create({'name': 'Recipe raw', 'is_storable': True,
            'categ_id': cls.category.id, 'standard_price': 10})
        cls.finished = cls.env['product.product'].create({'name': 'Recipe output', 'is_storable': True})
        plan = cls.env['account.analytic.plan'].create({'name': 'Recipe cost centres'})
        cls.analytic = cls.env['account.analytic.account'].create({'name': 'Recipe centre',
            'plan_id': plan.id, 'company_id': cls.company.id})
        cls.bom = cls.env['mrp.bom'].create({'product_tmpl_id': cls.finished.product_tmpl_id.id,
            'product_id': cls.finished.id, 'product_qty': 1, 'type': 'normal',
            'bom_line_ids': [Command.create({'product_id': cls.raw.id, 'product_qty': 0.2,
                                           'product_uom_id': cls.raw.uom_id.id})]})
        cls.env['stock.quant']._update_available_quantity(cls.raw, cls.warehouse.lot_stock_id, 1000)

    def _document(self, quantity=0.25, bom=None):
        today = fields.Date.today()
        document = self.env['mrp.bom.consumption.adjustment'].create({
            'bom_id': (bom or self.bom).id, 'company_id': self.company.id,
            'date': today, 'date_from': today - timedelta(days=2), 'date_to': today,
            'warehouse_id': self.warehouse.id, 'location_id': self.warehouse.lot_stock_id.id,
            'analytic_account_id': self.analytic.id, 'notes': 'Recipe regression test'})
        document.line_ids.write({'apply_change': True, 'new_quantity': quantity})
        return document

    def test_create_from_standard_form(self):
        today = fields.Date.today()
        with Form(self.env['mrp.bom.consumption.adjustment']) as form:
            form.bom_id = self.bom
            form.warehouse_id = self.warehouse
            form.date_from = today
            form.date_to = today
            form.analytic_account_id = self.analytic
            form.notes = 'Standard Odoo form regression'
            with form.line_ids.edit(0) as line:
                line.apply_change = True
                line.new_quantity = 0.25
        document = form.record
        self.assertEqual(document.state, 'draft')
        self.assertEqual(document.bom_quantity, self.bom.product_qty)
        self.assertEqual(document.bom_uom_id, self.bom.product_uom_id)
        self.assertEqual(document.line_ids.product_id, self.raw)
        self.assertEqual(document.line_ids.uom_id, self.raw.uom_id)
        self.assertAlmostEqual(document.line_ids.old_quantity, 0.2)
        self.assertAlmostEqual(document.line_ids.new_quantity, 0.25)

    def test_web_create_empty_generated_fields(self):
        document = self._document()
        values = document.copy_data()[0]
        values.update(account_move_id=False, source_ids=[], detail_ids=[],
                      move_ids=[Command.set([])], review_signature=False,
                      review_warning=False, bom_quantity=0, bom_uom_id=False,
                      line_ids=[Command.create({'bom_line_id': self.bom.bom_line_ids.id,
                                                'apply_change': True, 'new_quantity': 0.25})])
        new_document = self.env[document._name].create(values)
        self.assertEqual(new_document.bom_quantity, 1)
        self.assertAlmostEqual(new_document.line_ids.old_quantity, 0.2)
        self.assertEqual(new_document.line_ids.product_id, self.raw)
        new_document.write({'detail_ids': [], 'account_move_id': False, 'notes': 'Saved again'})
        self.assertEqual(new_document.notes, 'Saved again')
        values['review_signature'] = 'forged review'
        with self.assertRaises(AccessError):
            self.env[document._name].create(values)

    def _production(self, quantity=100):
        production = self.env['mrp.production'].create({'product_id': self.finished.id,
            'product_qty': quantity, 'bom_id': self.bom.id,
            'picking_type_id': self.warehouse.manu_type_id.id})
        production.action_confirm()
        with Form(production) as form:
            form.qty_producing = quantity
        production.move_raw_ids.write({'picked': True})
        production.button_mark_done()
        self.assertEqual(production.state, 'done')
        return production

    def _kit_picking(self, quantity=100):
        self.bom.type = 'phantom'
        picking = self.env['stock.picking'].create({'picking_type_id': self.warehouse.out_type_id.id,
            'location_id': self.warehouse.lot_stock_id.id,
            'location_dest_id': self.env.ref('stock.stock_location_customers').id})
        self.env['stock.move'].create({'product_id': self.finished.id,
            'product_uom': self.finished.uom_id.id, 'product_uom_qty': quantity,
            'picking_id': picking.id, 'location_id': picking.location_id.id,
            'location_dest_id': picking.location_dest_id.id})
        picking.action_confirm()
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        picking.move_ids._action_done()
        return picking

    def _assert_accounting(self, document, expected_quantity, expected_value):
        self.assertEqual(document.state, 'confirmed')
        self.assertEqual(document.move_ids.state, 'done')
        self.assertAlmostEqual(document.detail_ids.adjustment_quantity, expected_quantity)
        self.assertAlmostEqual(document.move_ids.value, expected_value)
        self.assertEqual(document.move_ids.account_move_id, document.account_move_id)
        self.assertEqual(len(document.move_ids.account_move_id), 1)
        self.assertEqual(document.account_move_id.state, 'posted')
        self.assertEqual(document.account_move_id.date, document.date)
        self.assertEqual(document.move_ids.date.date(), document.date)
        self.assertAlmostEqual(sum(document.account_move_id.line_ids.mapped('balance')), 0)
        difference = document.account_move_id.line_ids.filtered(lambda l: l.account_id == self.loss_account)
        stock = document.account_move_id.line_ids.filtered(lambda l: l.account_id == self.stock_account)
        self.assertEqual(difference.analytic_distribution, {str(self.analytic.id): 100})
        self.assertFalse(stock.analytic_distribution)
        self.assertFalse(document.move_ids.analytic_account_line_ids)
        self.assertAlmostEqual(abs(difference.balance), expected_value)

    def test_mo_extra_consumption_and_overlap(self):
        production = self._production()
        old_finished_value = production.move_finished_ids.value
        document = self._document()
        before = self.raw.with_context(location=self.warehouse.lot_stock_id.id).qty_available
        document.action_review()
        self.assertAlmostEqual(document.detail_ids.recorded_quantity, 20)
        self.assertAlmostEqual(document.detail_ids.required_quantity, 25)
        document.action_confirm()
        self._assert_accounting(document, 5, 50)
        self.assertAlmostEqual(self.raw.with_context(location=self.warehouse.lot_stock_id.id).qty_available, before - 5)
        self.assertEqual(production.move_finished_ids.value, old_finished_value)
        self.assertAlmostEqual(self.bom.bom_line_ids.product_qty, 0.25)
        second = self._document()
        second.action_review()
        self.assertAlmostEqual(second.detail_ids.previous_quantity, 5)
        self.assertAlmostEqual(second.detail_ids.recorded_quantity, 25)
        self.assertAlmostEqual(second.detail_ids.adjustment_quantity, 0)
        second.action_confirm()
        self.assertFalse(second.move_ids)
        self.assertFalse(second.account_move_id)
        with self.assertRaises(UserError):
            document.action_confirm()

    def test_mo_stock_return(self):
        self._production()
        document = self._document(0.15)
        document.action_review()
        document.action_confirm()
        self._assert_accounting(document, -5, 50)
        self.assertEqual(document.move_ids.location_id, self.loss_location)
        self.assertEqual(document.move_ids.location_dest_id, self.warehouse.lot_stock_id)

    def test_kit_extra_consumption(self):
        picking = self._kit_picking()
        self.assertTrue(picking.move_ids.recipe_execution_token)
        document = self._document()
        document.action_review()
        self.assertEqual(document.state, 'reviewed')
        self.assertEqual(document.source_ids.quantity, 100)
        document.action_confirm()
        self._assert_accounting(document, 5, 50)
        # The new BOM quantity cannot change the previously frozen kit count.
        second = self._document(0.3)
        second.action_review()
        self.assertEqual(second.source_ids.quantity, 100)
        self.assertAlmostEqual(second.detail_ids.adjustment_quantity, 5)

    def test_kit_stock_return(self):
        self._kit_picking()
        document = self._document(0.15)
        document.action_review()
        document.action_confirm()
        self._assert_accounting(document, -5, 50)

    def test_historical_kit_requires_evidence(self):
        picking = self._kit_picking()
        picking.move_ids.write({'recipe_quantity_per_unit': 0, 'recipe_execution_token': False})
        document = self._document()
        document.action_review()
        self.assertEqual(document.state, 'draft')
        self.assertTrue(document.source_ids.needs_review)
        with self.assertRaises(UserError):
            document.action_confirm()
        document.source_ids.write({'quantity': 100, 'evidence': 'Verified original delivery packing records.'})
        document.source_ids.action_verify()
        document.action_review()
        self.assertEqual(document.state, 'reviewed')

    def test_cost_captured_at_confirmation(self):
        self._production()
        document = self._document()
        document.action_review()
        self.raw.standard_price = 12
        document.action_confirm()
        self.assertEqual(document.detail_ids.unit_cost, 12)
        self._assert_accounting(document, 5, 60)

    def _assert_cost_method(self, method):
        self.category.property_cost_method = method
        # Two real receipt layers at different prices ensure the forced cost
        # differs from the oldest FIFO receipt, rather than testing a uniform cost.
        self.env['stock.quant']._update_available_quantity(self.raw, self.warehouse.lot_stock_id, -1000)
        for cost in (10, 20):
            self.raw.standard_price = cost
            receipt = self.env['stock.move'].create({
                'product_id': self.raw.id, 'product_uom': self.raw.uom_id.id,
                'product_uom_qty': 100,
                'location_id': self.env.ref('stock.stock_location_suppliers').id,
                'location_dest_id': self.warehouse.lot_stock_id.id})
            receipt._action_confirm(merge=False)
            receipt.quantity = 100
            receipt.picked = True
            receipt._action_done()
        self._production()
        document = self._document()
        document.action_review()
        expected = abs(document.detail_ids.adjustment_quantity) * self.raw.standard_price
        value_before = self.raw.total_value
        document.action_confirm()
        self.assertAlmostEqual(document.move_ids.value, self.company.currency_id.round(expected))
        self.assertAlmostEqual(sum(document.account_move_id.line_ids.mapped('debit')), self.company.currency_id.round(expected))
        self.raw.invalidate_recordset(['total_value'])
        self.assertAlmostEqual(self.raw.total_value, value_before - self.company.currency_id.round(expected), places=2)

    def test_fifo_move_valuation(self):
        self._assert_cost_method('fifo')

    def test_average_move_valuation(self):
        self._assert_cost_method('average')

    def test_missing_account_rolls_back(self):
        self._production()
        document = self._document()
        document.action_review()
        self.loss_location.valuation_account_id = False
        with self.assertRaises(UserError):
            document.action_confirm()
        self.assertFalse(document.move_ids)
        self.assertFalse(document.account_move_id)
        self.assertEqual(document.state, 'reviewed')
        self.assertAlmostEqual(self.bom.bom_line_ids.product_qty, 0.2)

    def test_server_side_immutability_and_access(self):
        self._production()
        document = self._document()
        document.action_review()
        document.action_confirm()
        with self.assertRaises(UserError):
            document.write({'notes': 'Forbidden edit'})
        with self.assertRaises(UserError):
            document.line_ids.write({'new_quantity': 99})
        with self.assertRaises(AccessError):
            document.detail_ids.write({'adjustment_quantity': 99})
        with self.assertRaises(UserError):
            document.move_ids.write({'quantity': 99})
        user = new_test_user(self.env, login='recipe_non_manager', groups='mrp.group_mrp_user')
        with self.assertRaises(AccessError):
            document.with_user(user).action_confirm()

    def test_base_quantity_and_unselected_line(self):
        self.bom.product_qty = 10
        self.bom.bom_line_ids.product_qty = 2
        self._production()
        document = self._document(2.5)
        document.action_review()
        self.assertAlmostEqual(document.detail_ids.required_quantity, 25)
        self.assertAlmostEqual(document.detail_ids.adjustment_quantity, 5)

    def test_review_cancel_reset_execute_workflow(self):
        self._production()
        document = self._document()
        quantity_before = self.raw.qty_available
        document.action_cancel()
        self.assertEqual(document.state, 'cancelled')
        for action in (document.action_review, document.action_confirm, document.action_cancel):
            with self.assertRaises(UserError):
                action()
        with self.assertRaises(UserError):
            document.write({'notes': 'Cannot edit cancelled'})
        document.action_draft()
        document.action_review()
        self.assertEqual(document.state, 'reviewed')
        self.assertFalse(document.move_ids)
        self.assertFalse(document.account_move_id)
        self.assertEqual(self.raw.qty_available, quantity_before)
        self.assertAlmostEqual(self.bom.bom_line_ids.product_qty, 0.2)
        document.action_draft()
        self.assertFalse(document.review_signature)
        self.assertFalse(document.detail_ids)
        document.line_ids.new_quantity = 0.3
        with self.assertRaises(UserError):
            document.action_confirm()
        document.action_review()
        document.action_cancel()
        self.assertFalse(document.move_ids)
        self.assertEqual(self.raw.qty_available, quantity_before)
        document.action_draft()
        document.action_review()
        document.action_confirm()
        self.assertEqual(document.state, 'confirmed')
        for action in (document.action_review, document.action_confirm,
                       document.action_cancel, document.action_draft):
            with self.assertRaises(UserError):
                action()
        self.assertEqual(document.action_account_move()['views'],
                         [(self.env.ref('account.view_move_form').id, 'form')])
        self.assertEqual(document.action_account_move()['res_id'], document.account_move_id.id)
        self.assertEqual(document.action_moves()['domain'], [('id', 'in', document.move_ids.ids)])
