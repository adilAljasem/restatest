from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "resta_barcode")
class TestRestaBarcode(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.Bc = env["resta.barcode"]
        cls.warehouse = env["stock.warehouse"].search([("company_id", "=", env.company.id)], limit=1)
        cls.stock = cls.warehouse.lot_stock_id
        cls.supplier_loc = env.ref("stock.stock_location_suppliers")
        cls.tomato = env["product.product"].create({"name": "طماطم", "is_storable": True, "barcode": "6281000000017"})
        cls.cheese = env["product.product"].create({"name": "جبن", "is_storable": True, "barcode": "6281000000024"})
        cls.milk = env["product.product"].create({"name": "حليب", "is_storable": True, "barcode": "6281000000031",
                                                  "tracking": "lot"})
        unit = env.ref("uom.product_uom_unit")
        pack = env["uom.uom"].create({"name": "كرتون 6", "relative_factor": 6, "relative_uom_id": unit.id})
        env["product.uom"].create({"product_id": cls.tomato.id, "uom_id": pack.id, "barcode": "16281000000014"})

    def _receipt(self, lines):
        picking = self.env["stock.picking"].create({
            "picking_type_id": self.warehouse.in_type_id.id, "location_id": self.supplier_loc.id,
            "location_dest_id": self.stock.id,
            "move_ids": [(0, 0, {"product_id": p.id, "product_uom_qty": q, "product_uom": p.uom_id.id,
                                 "location_id": self.supplier_loc.id, "location_dest_id": self.stock.id})
                         for p, q in lines]})
        picking.action_confirm()
        return picking

    def test_home_counts(self):
        self._receipt([(self.tomato, 5)])
        home = self.Bc.get_home()
        incoming = next(o for o in home["operations"] if o["code"] == "incoming")
        self.assertGreaterEqual(incoming["todo"], 1)

    def test_scan_receipt_partial_with_backorder(self):
        picking = self._receipt([(self.tomato, 5)])
        for _i in range(3):
            res = self.Bc.scan_picking(picking.id, "6281000000017")
        self.assertTrue(res["ok"])
        self.assertEqual(res["picking"]["moves"][0]["done"], 3)
        out = self.Bc.validate(picking.id, True)
        self.assertTrue(out["ok"])
        self.assertEqual(picking.state, "done")
        self.assertEqual(self.tomato.qty_available, 3)
        backorder = self.env["stock.picking"].search([("backorder_id", "=", picking.id)])
        self.assertEqual(backorder.move_ids.product_uom_qty, 2)

    def test_packaging_barcode_and_extra_product(self):
        picking = self._receipt([(self.tomato, 12)])
        res = self.Bc.scan_picking(picking.id, "16281000000014")
        self.assertEqual(res["picking"]["moves"][0]["done"], 6)
        res = self.Bc.scan_picking(picking.id, "6281000000024")  # cheese was not ordered
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["picking"]["moves"]), 2)
        self.Bc.validate(picking.id, False)
        self.assertEqual(self.cheese.qty_available, 1)
        self.assertFalse(self.env["stock.picking"].search([("backorder_id", "=", picking.id)]))

    def test_unknown_and_fill_all(self):
        picking = self._receipt([(self.tomato, 4), (self.cheese, 2)])
        self.assertFalse(self.Bc.scan_picking(picking.id, "000")["ok"])
        ui = self.Bc.fill_all(picking.id)
        self.assertTrue(ui["all_done"])
        self.assertTrue(self.Bc.validate(picking.id, True)["ok"])
        self.assertEqual(self.tomato.qty_available, 4)

    def test_lot_on_receipt(self):
        picking = self._receipt([(self.milk, 2)])
        res = self.Bc.scan_picking(picking.id, "6281000000031")
        self.assertTrue(res.get("need_lot"))
        self.Bc.scan_picking(picking.id, "6281000000031", "LOT-2026-11")
        self.Bc.scan_picking(picking.id, "6281000000031", "LOT-2026-11")
        line = picking.move_ids.move_line_ids
        self.assertEqual(line.lot_name, "LOT-2026-11")
        self.assertEqual(line.quantity, 2)
        self.assertTrue(self.Bc.validate(picking.id, True)["ok"])
        self.assertEqual(self.milk.qty_available, 2)

    def test_internal_refuses_unknown_product(self):
        self.env["stock.quant"]._update_available_quantity(self.tomato, self.stock, 10)
        internal = self.env["stock.picking"].create({
            "picking_type_id": self.warehouse.int_type_id.id, "location_id": self.stock.id,
            "location_dest_id": self.stock.id,
            "move_ids": [(0, 0, {"product_id": self.tomato.id, "product_uom_qty": 2, "product_uom": self.tomato.uom_id.id,
                                 "location_id": self.stock.id, "location_dest_id": self.stock.id})]})
        internal.action_confirm()
        self.assertFalse(self.Bc.scan_picking(internal.id, "6281000000024")["ok"])

    def test_inventory_count(self):
        self.env["stock.quant"]._update_available_quantity(self.cheese, self.stock, 5)
        self.Bc.scan_count(self.stock.id, "6281000000024")
        res = self.Bc.scan_count(self.stock.id, "6281000000024")
        line = next(l for l in res["lines"] if l["product"] == self.cheese.display_name)
        self.assertEqual(line["counted"], 2)
        self.assertEqual(line["diff"], -3)
        self.Bc.set_count(line["id"], 4)
        self.Bc.apply_count(self.stock.id)
        self.assertEqual(self.cheese.with_context(location=self.stock.id).qty_available, 4)

    def test_open_by_barcode(self):
        picking = self._receipt([(self.tomato, 1)])
        self.assertEqual(self.Bc.open_by_barcode(picking.name), picking.id)
        self.stock.barcode = "LOC-MAIN"
        self.assertEqual(self.Bc.find_location("LOC-MAIN"), self.stock.id)
