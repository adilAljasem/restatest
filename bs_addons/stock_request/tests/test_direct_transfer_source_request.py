# Copyright 2026 Mohamed Elmalky
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl.html).

from lxml import etree

from odoo.exceptions import ValidationError
from odoo.addons.base.tests.common import BaseCommon


class TestDirectTransferSourceRequest(BaseCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref("base.main_company")
        cls.destination_warehouse = cls.env.ref("stock.warehouse0")
        cls.source_warehouse = cls.env["stock.warehouse"].create(
            {
                "name": "Linked Transfer Source",
                "code": "LTSR",
                "company_id": cls.company.id,
            }
        )
        cls.order_model = cls.env["stock.request.order"].sudo()

    @classmethod
    def _create_order(cls, operation_type, **values):
        return cls.order_model.create(
            {
                "operation_type": operation_type,
                "company_id": cls.company.id,
                "source_warehouse_id": cls.source_warehouse.id,
                "warehouse_id": cls.destination_warehouse.id,
                **values,
            }
        )

    def test_source_document_field_and_views(self):
        field = self.order_model._fields["source_request_id"]
        self.assertEqual(field.comodel_name, "stock.request.order")
        self.assertFalse(field.required)
        self.assertTrue(field.check_company)

        for view_xmlid in (
            "stock_request.stock_request_order_form",
            "stock_request.stock_request_one_step_transfer_form",
        ):
            view_root = etree.fromstring(self.env.ref(view_xmlid).arch_db)
            source_field = view_root.xpath(
                "//field[@name='priority']/following-sibling::field[1]"
                "[@name='source_request_id']"
            )
            self.assertEqual(len(source_field), 1)
            self.assertIn(
                "('operation_type', '=', 'request')",
                source_field[0].get("domain"),
            )
            self.assertTrue(
                view_root.xpath("//button[@name='action_view_source_request']")
            )

        request_view = etree.fromstring(
            self.env.ref("stock_request.stock_request_order_form").arch_db
        )
        self.assertTrue(
            request_view.xpath(
                "//button[@name='action_view_direct_transfers']"
                "/field[@name='direct_transfer_count']"
            )
        )

    def test_only_stock_requests_can_be_selected(self):
        request = self._create_order("request")
        transfer = self._create_order("transfer", source_request_id=request.id)
        another_transfer = self._create_order("transfer")

        self.assertEqual(transfer.source_request_id, request)
        with self.assertRaisesRegex(ValidationError, "Source Document"):
            transfer.source_request_id = another_transfer

    def test_smart_buttons_open_linked_records(self):
        request = self._create_order("request")
        transfer = self._create_order("transfer", source_request_id=request.id)

        source_action = transfer.action_view_source_request()
        self.assertEqual(source_action["res_id"], request.id)
        self.assertEqual(source_action["res_model"], "stock.request.order")
        self.assertEqual(source_action["view_mode"], "form")

        request.invalidate_recordset(
            ["direct_transfer_ids", "direct_transfer_count"]
        )
        self.assertEqual(request.direct_transfer_count, 1)
        transfer_action = request.action_view_direct_transfers()
        self.assertEqual(transfer_action["res_id"], transfer.id)
        self.assertEqual(
            transfer_action["domain"],
            [("source_request_id", "=", request.id)],
        )

    def test_changing_and_clearing_source_updates_both_sides(self):
        first_request = self._create_order("request")
        second_request = self._create_order("request")
        transfer = self._create_order(
            "transfer", source_request_id=first_request.id
        )

        transfer.source_request_id = second_request
        (first_request | second_request).invalidate_recordset(
            ["direct_transfer_ids", "direct_transfer_count"]
        )
        self.assertFalse(first_request.direct_transfer_ids)
        self.assertEqual(first_request.direct_transfer_count, 0)
        self.assertEqual(second_request.direct_transfer_ids, transfer)
        self.assertEqual(second_request.direct_transfer_count, 1)

        transfer.source_request_id = False
        second_request.invalidate_recordset(
            ["direct_transfer_ids", "direct_transfer_count"]
        )
        self.assertFalse(transfer.source_request_id)
        self.assertFalse(second_request.direct_transfer_ids)
        self.assertEqual(second_request.direct_transfer_count, 0)
