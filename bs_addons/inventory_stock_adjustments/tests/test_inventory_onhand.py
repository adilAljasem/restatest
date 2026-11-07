from datetime import timedelta

from lxml import etree

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestInventoryOnhand(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product = cls.env['product.product'].create({
            'name': 'Live inventory stock',
            'type': 'consu',
            'is_storable': True,
            'standard_price': 10.0,
        })
        cls.w02 = cls.env['stock.location'].create({
            'name': 'W02 Location',
            'usage': 'internal',
            'company_id': cls.env.company.id,
        })
        cls.w03 = cls.env['stock.location'].create({
            'name': 'W03 Location',
            'usage': 'internal',
            'company_id': cls.env.company.id,
        })
        cls.quant = cls.env['stock.quant']
        # Product distribution: W02 = 56, W03 = 99 (Total = 155)
        cls.quant._update_available_quantity(cls.product, cls.w02, 56)
        cls.quant._update_available_quantity(cls.product, cls.w03, 99)

    def _inventory(self, location=None, counted=56, **values):
        loc = location or self.w02
        return self.env['stock.inventory'].create({
            'name': 'Live quantity test',
            'location_id': loc.id,
            'filter': 'partial',
            'state': 'confirm',
            'line_ids': [Command.create({
                'product_id': self.product.id,
                'inventory_quantity': counted,
            })],
            **values,
        })

    def test_acceptance_criteria_1_and_2_on_hand_selected_location_only(self):
        """AC 1 & 2: When creating a Stock Adjustment for W02, the On Hand quantity
        must be 56, not 155 (total across all warehouses).
        """
        inventory = self._inventory(location=self.w02, counted=56)
        line = inventory.line_ids
        self.assertEqual(line.location_quantity, 56, "On Hand must display actual quantity in W02 only (56, not 155).")
        self.assertEqual(line.quantity, 56, "Quantity must also be restricted to W02 only.")

    def test_acceptance_criteria_3_reserved_and_available_selected_location_only(self):
        """AC 3 & 13: Reserved and Available must be calculated based only on selected location."""
        # Reserve 10 units in W02
        self.quant._update_reserved_quantity(self.product, self.w02, 10)
        # Reserve 20 units in W03
        self.quant._update_reserved_quantity(self.product, self.w03, 20)

        inventory = self._inventory(location=self.w02, counted=56)
        line = inventory.line_ids
        self.assertEqual(line.location_quantity, 56)
        self.assertEqual(line.reserved_quantity, 10, "Reserved quantity must only be for W02 (10, not 30).")
        self.assertEqual(line.available_quantity, 46, "Available quantity must be 56 - 10 = 46 in W02.")

    def test_acceptance_criteria_4_field_order_in_inventory_details(self):
        """AC 4: Fields in Inventory Details must be displayed in the specified order."""
        view = self.env['stock.inventory'].get_view(
            self.env.ref('inventory_stock_adjustments.inventory_adjustments_form_view').id,
            'form',
        )
        arch = etree.fromstring(view['arch'])
        field_nodes = arch.xpath("//field[@name='line_ids']/list/field")
        # Filter visible/meaningful columns
        field_names = [f.get('name') for f in field_nodes]

        expected_order = [
            'product_id',
            'product_uom_id',
            'location_quantity',
            'reserved_quantity',
            'available_quantity',
            'inventory_quantity',
            'different',
            'price_value',
        ]
        # Verify that these fields appear in the exact relative order
        indices = [field_names.index(f) for f in expected_order]
        self.assertEqual(indices, sorted(indices), "Fields must appear in the exact order specified in AC 4.")

    def test_acceptance_criteria_5_set_zero_hidden(self):
        """AC 5: Set Zero field / option must be hidden from Inventory Details."""
        view = self.env['stock.inventory'].get_view(
            self.env.ref('inventory_stock_adjustments.inventory_adjustments_form_view').id,
            'form',
        )
        arch = etree.fromstring(view['arch'])
        make_it_zero_node = arch.xpath("//field[@name='line_ids']/list/field[@name='make_it_zero']")
        self.assertTrue(make_it_zero_node, "make_it_zero field exists in list.")
        self.assertEqual(make_it_zero_node[0].get('column_invisible'), 'True', "make_it_zero must be hidden.")

    def test_acceptance_criteria_6_different_formula(self):
        """AC 6: Different = Counted Quantity - On Hand."""
        inventory = self._inventory(location=self.w02, counted=60)
        line = inventory.line_ids
        # On Hand = 56, Counted = 60 -> Different = 60 - 56 = 4
        self.assertEqual(line.different, 4.0)

        line.inventory_quantity = 50
        # On Hand = 56, Counted = 50 -> Different = 50 - 56 = -6
        self.assertEqual(line.different, -6.0)

    def test_acceptance_criteria_7_to_12_dynamic_stock_movement_behavior(self):
        """AC 7-12: Dynamic behavior when stock moves occur after inventory creation:
        Stock Movement -> On Hand changes -> Counted Quantity remains unchanged
        -> Different recalculates automatically -> Diff. Valuation recalculates automatically.
        """
        inventory = self._inventory(location=self.w02, counted=56)
        line = inventory.line_ids
        self.assertEqual(line.location_quantity, 56.0)
        self.assertEqual(line.inventory_quantity, 56.0)
        self.assertEqual(line.different, 0.0)
        self.assertEqual(line.price_value, 0.0)

        # 1. Stock movement in ANOTHER location (W03) must NOT affect W02 inventory
        self.quant._update_available_quantity(self.product, self.w03, -20)
        self.assertEqual(line.location_quantity, 56.0, "Stock change in W03 must not affect W02.")
        self.assertEqual(line.different, 0.0)

        # 2. Stock movement in the SAME location (W02): e.g. receipt of 14 units
        self.quant._update_available_quantity(self.product, self.w02, 14)
        self.env.invalidate_all()

        # Check AC 8: On Hand reflects current actual stock quantity (56 + 14 = 70)
        self.assertEqual(line.location_quantity, 70.0, "On Hand must reflect updated stock quantity in W02.")

        # Check AC 7 & 9: Counted Quantity remains unchanged at 56
        self.assertEqual(line.inventory_quantity, 56.0, "Counted Quantity must remain unchanged.")

        # Check AC 10: Different recalculates automatically: 56 - 70 = -14
        self.assertEqual(line.different, -14.0, "Different must be automatically recalculated (56 - 70 = -14).")

        # Check AC 11: Diff. Valuation recalculates: -14 * 10.0 = -140.0
        self.assertEqual(line.price_value, -140.0, "Diff. Valuation must be automatically recalculated (-14 * 10 = -140).")

    def test_stock_movement_after_creation_does_not_block_validation(self):
        """A real stock move updates the live difference without changing the count."""
        inventory = self._inventory(location=self.w02, counted=56)
        inventory.sent_for_approval_date = fields.Datetime.now() - timedelta(days=1)

        move = self.env['stock.move'].create({
            'product_id': self.product.id,
            'product_uom_qty': 14.0,
            'product_uom': self.product.uom_id.id,
            'location_id': self.env.ref('stock.stock_location_suppliers').id,
            'location_dest_id': self.w02.id,
        })
        move._action_confirm()
        move.move_line_ids.quantity = 14.0
        move.picked = True
        move._action_done()

        line = inventory.line_ids
        self.assertEqual(line.location_quantity, 70.0)
        self.assertEqual(line.inventory_quantity, 56.0)
        self.assertEqual(line.different, -14.0)
        self.assertEqual(line.price_value, -140.0)

        inventory.action_done()
        self.assertEqual(inventory.state, 'done')
        self.assertEqual(line.inventory_quantity, 56.0)
        self.assertEqual(line.location_quantity, 70.0)
        self.assertEqual(line.different, -14.0)

    def test_validate_freezes_onhand_snapshot(self):
        """When validated, on-hand quantity is frozen."""
        inventory = self._inventory(location=self.w02, counted=50)
        line = inventory.line_ids
        inventory.action_done()
        self.assertEqual(inventory.state, 'done')
        self.assertTrue(line.quantity_frozen)
        self.assertEqual(line.location_quantity, 56.0)
        self.assertEqual(line.different, -6.0)
        # Verify quant was updated to 50
        self.assertEqual(self.quant._get_available_quantity(self.product, self.w02), 50.0)

    def test_snapshot_and_done_state_cannot_be_overridden_by_user(self):
        user = new_test_user(self.env, login='onhand_stock_user', groups='stock.group_stock_user')
        inventory = self._inventory(location=self.w02, counted=56)
        inventory.action_done()
        inventory = inventory.with_user(user)
        for values in ({'quantity_frozen': False}, {'validated_quantity': 999}):
            with self.assertRaises(UserError):
                inventory.line_ids.write(values)
        with self.assertRaises(UserError):
            inventory.write({'state': 'confirm'})
        with self.assertRaises(UserError):
            inventory.action_cancel_draft()
        with self.assertRaises(UserError):
            inventory.line_ids.unlink()
