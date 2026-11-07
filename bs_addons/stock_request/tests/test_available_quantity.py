# Copyright 2026 Mohamed Elmalky
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl.html).

from odoo import Command
from lxml import etree

from odoo.addons.base.tests.common import BaseCommon


class TestAvailableQuantity(BaseCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref("base.main_company")
        cls.destination_warehouse = cls.env.ref("stock.warehouse0")
        cls.source_warehouse = cls.env["stock.warehouse"].create(
            {
                "name": "Available Quantity Source",
                "code": "AQTY",
                "company_id": cls.company.id,
            }
        )
        cls.product = cls.env["product.product"].create(
            {
                "name": "Available Quantity Product",
                "type": "consu",
                "is_storable": True,
                "company_id": cls.company.id,
            }
        )

    def _create_stock_quant(self, location, quantity):
        self.env["stock.quant"].create(
            {
                "product_id": self.product.id,
                "location_id": location.id,
                "quantity": quantity,
            }
        )

    def test_two_step_available_quantity_uses_source_warehouse(self):
        self.env["res.config.settings"].create(
            {
                "stock_request_transfer_flow": "two_step",
                "stock_request_show_available_qty": True,
            }
        ).set_values()
        self._create_stock_quant(self.source_warehouse.lot_stock_id, 10.0)
        self._create_stock_quant(self.destination_warehouse.lot_stock_id, 4.0)

        order = self.env["stock.request.order"].create(
            {
                "operation_type": "transfer",
                "source_warehouse_id": self.source_warehouse.id,
                "warehouse_id": self.destination_warehouse.id,
                "stock_request_ids": [
                    Command.create(
                        {
                            "product_id": self.product.id,
                            "product_uom_id": self.product.uom_id.id,
                            "product_uom_qty": 3.0,
                        }
                    )
                ],
            }
        )

        self.assertEqual(order.transfer_flow, "two_step")
        self.assertTrue(order.show_available_qty)
        self.assertEqual(
            order.stock_request_ids.source_location_id,
            self.source_warehouse.lot_stock_id,
        )
        self.assertEqual(order.stock_request_ids.available_qty, 10.0)

        order_form = etree.fromstring(
            self.env.ref(
                "stock_request.stock_request_order_form"
            ).get_combined_arch()
        )
        self.assertTrue(
            order_form.xpath(
                "//field[@name='stock_request_ids']/list/field"
                "[@name='available_qty' and @string='Available Qty' and "
                "@optional='show' and @readonly='1' and "
                "@column_invisible='not parent.show_available_qty']"
            )
        )
        self.assertTrue(
            order_form.xpath(
                "//sheet/field[@name='show_available_qty' and @invisible='1']"
            )
        )

        self.env["res.config.settings"].create(
            {
                "stock_request_transfer_flow": "two_step",
                "stock_request_show_available_qty": False,
            }
        ).set_values()
        order.invalidate_recordset(["show_available_qty"])
        self.assertFalse(order.show_available_qty)
