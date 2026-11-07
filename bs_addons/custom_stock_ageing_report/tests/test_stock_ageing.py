from datetime import datetime, time, timedelta
from io import BytesIO
from zipfile import ZipFile

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestStockAgeingReport(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.tz = "UTC"
        cls.company = cls.env.company
        cls.warehouse = cls.env["stock.warehouse"].search(
            [("company_id", "=", cls.company.id)], limit=1
        )
        cls.stock_location = cls.warehouse.lot_stock_id
        cls.supplier_location = cls.env.ref("stock.stock_location_suppliers")
        cls.customer_location = cls.env.ref("stock.stock_location_customers")
        cls.inventory_location = cls.env["stock.location"].search(
            [("usage", "=", "inventory"), "|", ("company_id", "=", False), ("company_id", "=", cls.company.id)],
            limit=1,
        )
        cls.category = cls.env["product.category"].create({"name": "Ageing FIFO Test"})
        cls.category.with_company(cls.company).property_cost_method = "fifo"
        cls.category.with_company(cls.company).property_valuation = "periodic"
        cls.today = fields.Date.today()

    def _new_product(self, name, cost=10.0):
        product = self.env["product.product"].create({
            "name": name,
            "default_code": name.upper().replace(" ", "-"),
            "is_storable": True,
            "categ_id": self.category.id,
        })
        product.with_company(self.company).standard_price = cost
        return product

    def _datetime(self, date_value):
        return datetime.combine(date_value, time(hour=12))

    def _done_move(
        self,
        product,
        quantity,
        source,
        destination,
        date_value,
        value=None,
        origin_returned_move=None,
    ):
        move = self.env["stock.move"].create({
            "product_id": product.id,
            "product_uom_qty": quantity,
            "product_uom": product.uom_id.id,
            "location_id": source.id,
            "location_dest_id": destination.id,
            "origin_returned_move_id": origin_returned_move.id if origin_returned_move else False,
        })
        move._action_confirm()
        move._action_assign()
        move.picked = True
        move._action_done()
        event_datetime = self._datetime(date_value)
        move.write({"date": event_datetime})
        move.move_line_ids.write({"date": event_datetime})
        if value is not None:
            move.value = value
        return move

    def _wizard(self, product, report_date=None, warehouses=None, locations=None):
        return self.env["stock.ageing.report.wizard"].create({
            "company_id": self.company.id,
            "warehouse_ids": [(6, 0, (warehouses or self.warehouse).ids)],
            "location_ids": [(6, 0, locations.ids)] if locations else False,
            "product_ids": [(6, 0, product.ids)],
            "report_date": report_date or self.today,
            "period_length": 30,
            "period_count": 4,
        })

    def _single_line(self, wizard):
        data = self.env["stock.ageing.engine"]._get_report_data(wizard)
        self.assertEqual(len(data["lines"]), 1)
        line = data["lines"][0]
        self.assertAlmostEqual(
            sum(bucket["quantity"] for bucket in line["buckets"]),
            line["total_qty"],
        )
        self.assertAlmostEqual(
            sum(bucket["value"] for bucket in line["buckets"]),
            line["total_value"],
        )
        return data, line

    def test_01_period_boundaries_have_no_gaps_or_overlap(self):
        engine = self.env["stock.ageing.engine"]
        periods = engine._build_periods(30, 4)
        self.assertEqual(
            [(period["min_days"], period["max_days"]) for period in periods],
            [(0, 30), (31, 60), (61, 90), (91, None)],
        )
        self.assertEqual([period["label"] for period in periods], ["0-30", "31-60", "61-90", "> 90"])
        self.assertEqual(
            [engine._bucket_index(age, 30, 4) for age in (0, 30, 31, 60, 61, 90, 91, 500)],
            [0, 0, 1, 1, 2, 2, 3, 3],
        )

    def test_02_fifo_remaining_quantity_value_and_historical_cutoff(self):
        product = self._new_product("FIFO Historical")
        self._done_move(
            product, 10, self.supplier_location, self.stock_location,
            self.today - timedelta(days=100), value=100,
        )
        self._done_move(
            product, 10, self.supplier_location, self.stock_location,
            self.today - timedelta(days=40), value=200,
        )
        self._done_move(
            product, 12, self.stock_location, self.customer_location,
            self.today - timedelta(days=10),
        )

        data, line = self._single_line(self._wizard(product))
        self.assertAlmostEqual(line["total_qty"], 8)
        self.assertAlmostEqual(line["total_value"], 160)
        self.assertAlmostEqual(line["buckets"][1]["quantity"], 8)
        self.assertAlmostEqual(data["totals"]["quantity"], 8)

        historical = self._wizard(product, report_date=self.today - timedelta(days=20))
        _, historical_line = self._single_line(historical)
        self.assertAlmostEqual(historical_line["total_qty"], 20)
        self.assertAlmostEqual(historical_line["buckets"][0]["quantity"], 10)
        self.assertAlmostEqual(historical_line["buckets"][2]["quantity"], 10)

    def test_03_internal_transfer_keeps_original_age_and_cost(self):
        second_warehouse = self.env["stock.warehouse"].create({
            "name": "Ageing Second Warehouse",
            "code": "ASW",
            "company_id": self.company.id,
        })
        product = self._new_product("Internal Transfer", cost=7)
        self._done_move(
            product, 5, self.supplier_location, self.stock_location,
            self.today - timedelta(days=100), value=35,
        )
        self._done_move(
            product, 5, self.stock_location, second_warehouse.lot_stock_id,
            self.today - timedelta(days=5),
        )

        wizard = self._wizard(product, warehouses=second_warehouse)
        _, line = self._single_line(wizard)
        self.assertEqual(line["warehouse_id"], second_warehouse.id)
        self.assertAlmostEqual(line["total_qty"], 5)
        self.assertAlmostEqual(line["total_value"], 35)
        self.assertAlmostEqual(line["buckets"][3]["quantity"], 5)
        self.assertAlmostEqual(line["buckets"][0]["quantity"], 0)

    def test_04_customer_return_restores_original_layer(self):
        product = self._new_product("Customer Return", cost=11)
        self._done_move(
            product, 5, self.supplier_location, self.stock_location,
            self.today - timedelta(days=100), value=55,
        )
        delivery = self._done_move(
            product, 5, self.stock_location, self.customer_location,
            self.today - timedelta(days=60),
        )
        self._done_move(
            product, 2, self.customer_location, self.stock_location,
            self.today, value=22, origin_returned_move=delivery,
        )

        _, line = self._single_line(self._wizard(product))
        self.assertAlmostEqual(line["total_qty"], 2)
        self.assertAlmostEqual(line["total_value"], 22)
        self.assertAlmostEqual(line["buckets"][3]["quantity"], 2)
        self.assertAlmostEqual(line["buckets"][0]["quantity"], 0)

    def test_05_inventory_adjustment_decrease_uses_fifo(self):
        product = self._new_product("Inventory Adjustment", cost=4)
        self._done_move(
            product, 4, self.inventory_location, self.stock_location,
            self.today - timedelta(days=70), value=16,
        )
        self._done_move(
            product, 1, self.stock_location, self.inventory_location,
            self.today - timedelta(days=5),
        )

        _, line = self._single_line(self._wizard(product))
        self.assertAlmostEqual(line["total_qty"], 3)
        self.assertAlmostEqual(line["total_value"], 12)
        self.assertAlmostEqual(line["buckets"][2]["quantity"], 3)

    def test_06_excel_and_pdf_payloads(self):
        product = self._new_product("Report Outputs", cost=9)
        self._done_move(
            product, 3, self.supplier_location, self.stock_location,
            self.today - timedelta(days=15), value=27,
        )
        wizard = self._wizard(product)
        report_data = self.env["stock.ageing.engine"]._get_report_data(wizard)
        xlsx_content = wizard._generate_xlsx(report_data)
        self.assertTrue(xlsx_content.startswith(b"PK"))
        self.assertGreater(len(xlsx_content), 1000)
        with ZipFile(BytesIO(xlsx_content)) as archive:
            xml_payload = b"".join(
                archive.read(name) for name in archive.namelist() if name.endswith(".xml")
            )
        self.assertNotIn(b"Valuation Method", xml_payload)
        self.assertNotIn(b"Analytical FIFO", xml_payload)

        paperformat = self.env.ref("custom_stock_ageing_report.paperformat_stock_ageing")
        self.assertEqual(paperformat.margin_top, 40)
        self.assertEqual(paperformat.header_spacing, 32)

        report_values = self.env[
            "report.custom_stock_ageing_report.report_stock_ageing_document"
        ]._get_report_values(wizard.ids)
        self.assertEqual(report_values["doc_ids"], wizard.ids)
        self.assertEqual(report_values["reports"][0]["data"]["lines"][0]["product_id"], product.id)

        preview_action = wizard.action_view_report()
        self.assertEqual(preview_action["target"], "new")
        self.assertEqual(wizard.state, "results")
        self.assertIn("Total Qty", wizard.result_html)
        self.assertIn("Valuation method", wizard.result_html)

        pdf_action = wizard.action_print_pdf()
        self.assertEqual(pdf_action["type"], "ir.actions.report")
        self.assertEqual(pdf_action["report_type"], "qweb-pdf")

        download_action = wizard.action_export_xlsx()
        self.assertEqual(download_action["target"], "new")
        self.assertEqual(download_action["res_model"], "stock.ageing.report.download")
        download = self.env["stock.ageing.report.download"].browse(download_action["res_id"])
        self.assertTrue(download.exists())
        self.assertTrue(download.file_data)
        self.assertTrue(download.filename.endswith(".xlsx"))

    def test_07_selected_warehouse_does_not_include_other_warehouses(self):
        second_warehouse = self.env["stock.warehouse"].create({
            "name": "Ageing Filter Warehouse",
            "code": "AFW",
            "company_id": self.company.id,
        })
        product = self._new_product("Warehouse Scope", cost=5)
        self._done_move(
            product, 3, self.supplier_location, self.stock_location,
            self.today - timedelta(days=20), value=15,
        )
        self._done_move(
            product, 8, self.supplier_location, second_warehouse.lot_stock_id,
            self.today - timedelta(days=20), value=40,
        )

        _, line = self._single_line(self._wizard(product, warehouses=self.warehouse))
        self.assertEqual(line["warehouse_id"], self.warehouse.id)
        self.assertAlmostEqual(line["total_qty"], 3)

    def test_08_category_and_location_modes_apply_the_active_filter(self):
        included = self._new_product("Category Included", cost=6)
        other_category = self.env["product.category"].create({"name": "Ageing Other Category"})
        excluded = self._new_product("Category Excluded", cost=6)
        excluded.categ_id = other_category
        self._done_move(
            included, 2, self.supplier_location, self.stock_location,
            self.today - timedelta(days=10), value=12,
        )
        self._done_move(
            excluded, 7, self.supplier_location, self.stock_location,
            self.today - timedelta(days=10), value=42,
        )
        wizard = self.env["stock.ageing.report.wizard"].create({
            "company_id": self.company.id,
            "filter_by": "category",
            "category_ids": [(6, 0, self.category.ids)],
            "report_based_on": "location",
            "location_ids": [(6, 0, self.stock_location.ids)],
            "report_date": self.today,
            "period_length": 30,
            "period_count": 4,
        })

        data, line = self._single_line(wizard)
        self.assertEqual(line["product_id"], included.id)
        self.assertAlmostEqual(data["totals"]["quantity"], 2)

    def test_09_empty_result_has_clear_preview_message(self):
        product = self._new_product("No Historical Stock")
        wizard = self._wizard(product)
        wizard.action_view_report()
        self.assertFalse(wizard.result_data["lines"])
        self.assertIn("No stock ageing results were found", wizard.result_html)
