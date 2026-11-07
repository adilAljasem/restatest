# Copyright 2026 Mohamed Elmalky
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl.html).

from unittest.mock import patch

from lxml import etree

from odoo import Command
from odoo.exceptions import ValidationError
from odoo.modules import module
from odoo.tests import Form
from odoo.addons.base.tests.common import BaseCommon

from ..models.res_config_settings import (
    ALLOW_PURCHASE_LINE_WAREHOUSE_PARAMETER,
)


class TestPurchaseMultiWarehouse(BaseCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref("base.main_company")
        cls.company.po_double_validation = "one_step"
        cls.warehouse_1 = cls.env.ref("stock.warehouse0")
        cls.warehouse_2 = cls.env["stock.warehouse"].create(
            {
                "name": "Purchase Warehouse 2",
                "code": "PWH2",
                "company_id": cls.company.id,
            }
        )
        cls.vendor = cls.env["res.partner"].create(
            {"name": "Multi Warehouse Vendor", "supplier_rank": 1}
        )
        cls.product_1 = cls.env["product.product"].create(
            {
                "name": "Multi Warehouse Product 1",
                "type": "consu",
                "is_storable": True,
            }
        )
        cls.product_2 = cls.env["product.product"].create(
            {
                "name": "Multi Warehouse Product 2",
                "type": "consu",
                "is_storable": True,
            }
        )

    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_param(
            ALLOW_PURCHASE_LINE_WAREHOUSE_PARAMETER, False
        )

    def _enable_multi_warehouse(self):
        self.env["ir.config_parameter"].sudo().set_param(
            ALLOW_PURCHASE_LINE_WAREHOUSE_PARAMETER, True
        )

    def _create_purchase(self, line_values):
        return self.env["purchase.order"].create(
            {
                "partner_id": self.vendor.id,
                "company_id": self.company.id,
                "picking_type_id": self.warehouse_1.in_type_id.id,
                "order_line": [
                    Command.create(
                        {
                            "product_id": values["product"].id,
                            "product_qty": values.get("quantity", 1.0),
                            "product_uom_id": values["product"].uom_id.id,
                            "price_unit": 10.0,
                            "warehouse_id": (
                                values["warehouse"].id
                                if values.get("warehouse")
                                else False
                            ),
                        }
                    )
                    for values in line_values
                ],
            }
        )

    @staticmethod
    def _root_receipts(order, warehouses):
        picking_types = warehouses.mapped("in_type_id")
        return order.picking_ids.filtered(
            lambda picking: picking.picking_type_id in picking_types
        )

    def test_disabled_keeps_standard_purchase_receipt(self):
        order = self._create_purchase(
            [
                {"product": self.product_1, "warehouse": self.warehouse_1},
                {"product": self.product_2, "warehouse": self.warehouse_2},
            ]
        )

        order.button_confirm()

        receipts = self._root_receipts(
            order, self.warehouse_1 | self.warehouse_2
        )
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts.picking_type_id, self.warehouse_1.in_type_id)
        self.assertEqual(order.order_line.move_ids.warehouse_id, self.warehouse_1)

    def test_enabled_empty_warehouse_uses_order_picking_type(self):
        self._enable_multi_warehouse()
        order = self._create_purchase(
            [{"product": self.product_1, "warehouse": False}]
        )
        line = order.order_line

        self.assertFalse(line.warehouse_id)
        self.assertFalse(line._fields["warehouse_id"].required)

        order.button_confirm()

        self.assertEqual(order.state, "purchase")
        self.assertFalse(line.warehouse_id)
        self.assertEqual(len(order.picking_ids), 1)
        self.assertEqual(order.picking_ids.picking_type_id, order.picking_type_id)
        self.assertEqual(line.move_ids.picking_id, order.picking_ids)
        self.assertEqual(
            line.move_ids.warehouse_id,
            order.picking_type_id.warehouse_id,
        )

    def test_enabled_creates_one_receipt_per_warehouse(self):
        self._enable_multi_warehouse()
        order = self._create_purchase(
            [
                {"product": self.product_1, "warehouse": self.warehouse_1},
                {"product": self.product_2, "warehouse": self.warehouse_2},
            ]
        )

        order.button_confirm()

        receipts = self._root_receipts(
            order, self.warehouse_1 | self.warehouse_2
        )
        self.assertEqual(len(receipts), 2)
        for line in order.order_line:
            move = line.move_ids.filtered(
                lambda record: record.picking_id in receipts
            )
            self.assertEqual(move.picking_id.picking_type_id, line.warehouse_id.in_type_id)
            self.assertEqual(move.warehouse_id, line.warehouse_id)
            self.assertEqual(move.location_final_id, line.warehouse_id.lot_stock_id)

        warehouse_2_line = order.order_line.filtered(
            lambda line: line.warehouse_id == self.warehouse_2
        )
        warehouse_2_line.product_qty = 3.0
        self.assertEqual(
            len(
                self._root_receipts(
                    order, self.warehouse_1 | self.warehouse_2
                )
            ),
            2,
        )
        self.assertEqual(
            sum(
                warehouse_2_line.move_ids.filtered(
                    lambda move: move.picking_id.picking_type_id
                    == self.warehouse_2.in_type_id
                ).mapped("product_uom_qty")
            ),
            3.0,
        )

    def test_enabled_groups_lines_for_the_same_warehouse(self):
        self._enable_multi_warehouse()
        order = self._create_purchase(
            [
                {"product": self.product_1, "warehouse": self.warehouse_2},
                {"product": self.product_2, "warehouse": self.warehouse_2},
            ]
        )

        order.button_confirm()

        receipts = self._root_receipts(order, self.warehouse_2)
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts.move_ids.purchase_line_id, order.order_line)

    def test_warehouse_must_match_purchase_company(self):
        self._enable_multi_warehouse()
        # Stock's test-only automatic warehouse creation does not provide a
        # name, while stock_request intentionally requires one. Create the
        # company without that test shortcut and add the named warehouse here.
        with patch.object(module, "current_test", False):
            other_company = self.env["res.company"].create(
                {"name": "Other Company"}
            )
        other_warehouse = self.env["stock.warehouse"].create(
            {
                "name": "Other Company Warehouse",
                "code": "OCWH",
                "company_id": other_company.id,
            }
        )

        with self.assertRaises(ValidationError):
            self._create_purchase(
                [{"product": self.product_1, "warehouse": other_warehouse}]
            )

    def test_two_step_route_uses_selected_warehouse(self):
        self._enable_multi_warehouse()
        self.warehouse_2.reception_steps = "two_steps"
        order = self._create_purchase(
            [{"product": self.product_1, "warehouse": self.warehouse_2}]
        )

        order.button_confirm()

        root_receipt = self._root_receipts(order, self.warehouse_2)
        self.assertEqual(len(root_receipt), 1)
        self.assertEqual(
            root_receipt.location_dest_id,
            self.warehouse_2.in_type_id.default_location_dest_id,
        )
        root_move = order.order_line.move_ids.filtered(
            lambda move: move.picking_id == root_receipt
        )
        self.assertEqual(root_move.location_final_id, self.warehouse_2.lot_stock_id)
        root_move.quantity = root_move.product_uom_qty
        root_move.picked = True
        root_receipt.button_validate()

        self.assertTrue(root_move.move_dest_ids)
        self.assertEqual(
            root_move.move_dest_ids.location_dest_id,
            self.warehouse_2.lot_stock_id,
        )

    def test_purchase_agreement_warehouse_field_is_optional_and_configurable(self):
        agreement = self.env["purchase.requisition"].create(
            {
                "vendor_id": self.vendor.id,
                "requisition_type": "purchase_template",
                "line_ids": [
                    Command.create(
                        {
                            "product_id": self.product_1.id,
                            "product_qty": 2.0,
                            "product_uom_id": self.product_1.uom_id.id,
                        }
                    )
                ],
            }
        )
        warehouse_field = self.env["purchase.requisition.line"]._fields[
            "warehouse_id"
        ]
        self.assertEqual(warehouse_field.comodel_name, "stock.warehouse")
        self.assertFalse(warehouse_field.required)
        self.assertFalse(agreement.line_ids.warehouse_id)
        self.assertFalse(agreement.stock_request_multi_warehouse_enabled)

        self._enable_multi_warehouse()
        agreement.invalidate_recordset(["stock_request_multi_warehouse_enabled"])
        self.assertTrue(agreement.stock_request_multi_warehouse_enabled)

        form_arch = self.env["purchase.requisition"].get_view(
            self.env.ref(
                "purchase_requisition.view_purchase_requisition_form"
            ).id,
            "form",
        )["arch"]
        form_root = etree.fromstring(form_arch)
        warehouse_column = form_root.xpath(
            "//field[@name='line_ids']/list/field[@name='warehouse_id']"
        )[0]
        self.assertEqual(
            warehouse_column.get("column_invisible"),
            "not parent.stock_request_multi_warehouse_enabled",
        )

    def test_purchase_agreement_warehouses_are_copied_to_purchase_lines(self):
        self._enable_multi_warehouse()
        agreement = self.env["purchase.requisition"].create(
            {
                "vendor_id": self.vendor.id,
                "requisition_type": "purchase_template",
                "line_ids": [
                    Command.create(
                        {
                            "product_id": self.product_1.id,
                            "product_qty": 2.0,
                            "product_uom_id": self.product_1.uom_id.id,
                            "warehouse_id": self.warehouse_2.id,
                        }
                    ),
                    Command.create(
                        {
                            "product_id": self.product_2.id,
                            "product_qty": 3.0,
                            "product_uom_id": self.product_2.uom_id.id,
                        }
                    ),
                ],
            }
        )

        purchase_form = Form(
            self.env["purchase.order"].with_context(
                default_requisition_id=agreement.id,
                default_user_id=False,
            )
        )
        purchase = purchase_form.save()
        selected_line = purchase.order_line.filtered(
            lambda line: line.product_id == self.product_1
        )
        empty_line = purchase.order_line.filtered(
            lambda line: line.product_id == self.product_2
        )
        self.assertEqual(
            selected_line.stock_request_requisition_line_id,
            agreement.line_ids.filtered(
                lambda line: line.product_id == self.product_1
            ),
        )
        self.assertEqual(
            empty_line.stock_request_requisition_line_id,
            agreement.line_ids.filtered(
                lambda line: line.product_id == self.product_2
            ),
        )
        self.assertEqual(selected_line.warehouse_id, self.warehouse_2)
        self.assertFalse(empty_line.warehouse_id)
