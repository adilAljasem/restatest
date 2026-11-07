from psycopg2.errors import CheckViolation

from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase
from odoo.tools import mute_logger


class TestBatchScrapOrder(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.warehouse = cls.env["stock.warehouse"].search(
            [("company_id", "=", cls.env.company.id)], limit=1
        )
        cls.source_location = cls.warehouse.lot_stock_id
        cls.scrap_location = cls.env["stock.location"].search(
            [
                ("company_id", "=", cls.env.company.id),
                ("usage", "=", "inventory"),
            ],
            limit=1,
        )
        cls.product_a = cls.env["product.product"].create(
            {"name": "Batch Scrap Product A", "is_storable": True}
        )
        cls.product_b = cls.env["product.product"].create(
            {"name": "Batch Scrap Product B", "is_storable": True}
        )
        cls.reason = cls.env["stock.scrap.reason.tag"].create(
            {"name": "Batch Scrap Test Reason"}
        )
        cls.env["stock.quant"]._update_available_quantity(
            cls.product_a, cls.source_location, 10
        )
        cls.env["stock.quant"]._update_available_quantity(
            cls.product_b, cls.source_location, 10
        )

    def _create_batch(self):
        return self.env["batch.scrap.order"].create(
            {
                "source_document": "TEST-SOURCE",
                "reference": "TEST-REFERENCE",
                "location_id": self.source_location.id,
                "scrap_location_id": self.scrap_location.id,
                "line_ids": [
                    Command.create(
                        {
                            "product_id": self.product_a.id,
                            "quantity": 2.0,
                            "scrap_reason_tag_ids": [Command.set(self.reason.ids)],
                        }
                    ),
                    Command.create(
                        {
                            "product_id": self.product_b.id,
                            "quantity": 3.0,
                        }
                    ),
                ],
            }
        )

    def test_validate_creates_and_completes_scraps(self):
        batch = self._create_batch()

        batch.action_validate()

        self.assertEqual(batch.state, "done")
        self.assertEqual(len(batch.scrap_ids), 2)
        self.assertTrue(all(scrap.state == "done" for scrap in batch.scrap_ids))
        self.assertTrue(all(scrap.move_ids for scrap in batch.scrap_ids))
        self.assertTrue(batch.name.startswith("BSO/"))
        self.assertEqual(
            batch.scrap_ids.mapped("origin"), ["TEST-SOURCE", "TEST-SOURCE"]
        )
        self.assertEqual(
            batch.scrap_ids.mapped("batch_reference"), [batch.name, batch.name]
        )
        self.assertEqual(batch.scrap_ids.mapped("batch_scrap_order_id"), batch)
        self.assertEqual(batch.scrap_ids.mapped("location_id"), self.source_location)
        self.assertEqual(
            batch.scrap_ids.mapped("scrap_location_id"), self.scrap_location
        )
        scrap_a = batch.scrap_ids.filtered(
            lambda scrap: scrap.product_id == self.product_a
        )
        self.assertEqual(scrap_a.scrap_qty, 2.0)
        self.assertEqual(scrap_a.product_uom_id, self.product_a.uom_id)
        self.assertEqual(scrap_a.scrap_reason_tag_ids, self.reason)

    def test_cancel_draft_creates_no_scraps(self):
        batch = self._create_batch()

        batch.action_cancel()

        self.assertEqual(batch.state, "cancel")
        self.assertFalse(batch.scrap_ids)

    def test_batch_requires_lines(self):
        batch = self.env["batch.scrap.order"].create(
            {
                "location_id": self.source_location.id,
                "scrap_location_id": self.scrap_location.id,
            }
        )

        with self.assertRaises(ValidationError):
            batch.action_validate()

    def test_quantity_must_be_positive(self):
        with (
            self.assertRaisesRegex(CheckViolation, "batch_scrap_order_line_positive_quantity"),
            mute_logger("odoo.sql_db"),
            self.cr.savepoint(),
        ):
            self.env["batch.scrap.order"].create(
                {
                    "location_id": self.source_location.id,
                    "scrap_location_id": self.scrap_location.id,
                    "line_ids": [
                        Command.create(
                            {"product_id": self.product_a.id, "quantity": 0}
                        )
                    ],
                }
            )

    def test_failed_scrap_rolls_back_generated_records(self):
        batch = self._create_batch()
        batch.line_ids[1].quantity = 1000

        with self.assertRaises(UserError):
            batch.action_validate()

        self.assertEqual(batch.state, "draft")
        self.assertFalse(batch.scrap_ids)

    def test_lines_are_locked_after_validation(self):
        batch = self._create_batch()
        batch.action_validate()

        with self.assertRaises(UserError):
            batch.line_ids[0].quantity = 5
        with self.assertRaises(UserError):
            batch.line_ids[0].unlink()
        with self.assertRaises(UserError):
            batch.unlink()

    def test_smart_buttons_open_linked_records(self):
        batch = self._create_batch()
        batch.action_validate()

        batch_action = batch.action_view_scraps()
        self.assertEqual(
            batch_action["domain"], [("batch_scrap_order_id", "=", batch.id)]
        )
        scrap_action = batch.scrap_ids[0].action_open_batch_scrap_order()
        self.assertEqual(scrap_action["res_id"], batch.id)
