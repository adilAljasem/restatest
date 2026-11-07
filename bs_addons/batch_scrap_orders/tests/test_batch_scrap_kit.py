from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests import Form, tagged

from .test_batch_scrap_order import TestBatchScrapOrder


@tagged("post_install", "-at_install")
class TestBatchScrapKit(TestBatchScrapOrder):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.component_1 = cls.env["product.product"].create(
            {"name": "Kit Component 1", "is_storable": True}
        )
        cls.component_2 = cls.env["product.product"].create(
            {"name": "Kit Component 2", "is_storable": True}
        )
        cls.kit_bom = cls.env["mrp.bom"].create(
            {
                "product_id": cls.product_a.id,
                "product_tmpl_id": cls.product_a.product_tmpl_id.id,
                "product_uom_id": cls.product_a.uom_id.id,
                "type": "phantom",
                "product_qty": 1.0,
                "bom_line_ids": [
                    Command.create(
                        {"product_id": cls.component_1.id, "product_qty": 2.0}
                    ),
                    Command.create(
                        {"product_id": cls.component_2.id, "product_qty": 1.0}
                    ),
                ],
            }
        )
        cls.env["stock.quant"]._update_available_quantity(
            cls.component_1, cls.source_location, 100
        )
        cls.env["stock.quant"]._update_available_quantity(
            cls.component_2, cls.source_location, 100
        )

    def _create_kit_batch(self):
        return self.env["batch.scrap.order"].create(
            {
                "location_id": self.source_location.id,
                "scrap_location_id": self.scrap_location.id,
                "line_ids": [
                    Command.create(
                        {
                            "product_id": self.product_a.id,
                            "quantity": 2.0,
                        }
                    ),
                ],
            }
        )

    def test_kit_scrap_gets_kit_bom(self):
        """A kit product line must generate a scrap with the Kit BoM assigned."""
        batch = self._create_kit_batch()

        batch.action_validate()

        scrap = batch.scrap_ids
        self.assertEqual(len(scrap), 1)
        self.assertEqual(scrap.state, "done")
        self.assertEqual(scrap.bom_id, self.kit_bom)
        # The kit move explodes into its component moves.
        self.assertEqual(
            set(scrap.move_ids.product_id),
            {self.component_1, self.component_2},
        )
        # Components moved to the scrap location.
        self.assertEqual(
            self.env["stock.quant"]._get_available_quantity(
                self.component_1, self.scrap_location
            ),
            4.0,
        )
        self.assertEqual(
            self.env["stock.quant"]._get_available_quantity(
                self.component_2, self.scrap_location
            ),
            2.0,
        )

    def test_kit_bom_from_other_company_is_rejected(self):
        """A kit product whose Kit BoM belongs to another company is blocked.

        The BoM is unusable in this company, so the batch must refuse the
        scrap instead of creating a kit scrap without its bom_id.
        """
        other_company = self.env["res.company"].create(
            {"name": "Kit BoM Other Company"}
        )
        self.kit_bom.company_id = other_company
        batch = self._create_kit_batch()

        with self.assertRaisesRegex(ValidationError, "no available Kit BoM"):
            batch.action_validate()

        self.assertEqual(batch.state, "draft")
        self.assertFalse(batch.scrap_ids)

    def test_non_kit_line_keeps_standard_behavior(self):
        """Products without a Kit BoM are scrapped without any bom_id."""
        batch = self.env["batch.scrap.order"].create(
            {
                "location_id": self.source_location.id,
                "scrap_location_id": self.scrap_location.id,
                "line_ids": [
                    Command.create(
                        {
                            "product_id": self.product_b.id,
                            "quantity": 3.0,
                        }
                    ),
                ],
            }
        )

        batch.action_validate()

        self.assertEqual(batch.state, "done")
        self.assertEqual(len(batch.scrap_ids), 1)
        self.assertFalse(any(batch.scrap_ids.mapped("bom_id")))
        self.assertEqual(batch.scrap_ids.scrap_qty, 3.0)

    def test_manual_kit_scrap_form_still_assigns_bom(self):
        """The standard manual scrap flow keeps working with a kit product."""
        with Form(self.env["stock.scrap"]) as scrap_form:
            scrap_form.product_id = self.product_a
        scrap = scrap_form.save()
        self.assertEqual(scrap.bom_id, self.kit_bom)
