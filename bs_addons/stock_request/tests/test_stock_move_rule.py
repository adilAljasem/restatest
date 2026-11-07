from odoo.tests import TransactionCase, new_test_user


class TestStockMoveRule(TransactionCase):
    def test_inventory_user_reads_move_without_stock_request(self):
        user = new_test_user(
            self.env,
            login="stock_move_rule_inventory_user",
            groups="stock.group_stock_user",
            context={"no_reset_password": True},
        )
        product = self.env["product.product"].create(
            {"name": "Standard stock move", "type": "consu", "is_storable": True}
        )
        move = self.env["stock.move"].create(
            {
                "product_id": product.id,
                "product_uom_qty": 1.0,
                "product_uom": product.uom_id.id,
                "location_id": self.env.ref("stock.stock_location_stock").id,
                "location_dest_id": self.env.ref("stock.stock_location_customers").id,
                "company_id": self.env.company.id,
            }
        )

        self.assertFalse(move.stock_request_line_id)
        self.assertEqual(move.with_user(user).read(["id"])[0]["id"], move.id)
