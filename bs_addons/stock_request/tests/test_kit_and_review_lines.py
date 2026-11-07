from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged, new_test_user
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestKitAndReviewLines(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.requester = new_test_user(cls.env, login="kit_review_requester",
            groups="stock_request.group_stock_request_user")
        cls.responsible = new_test_user(cls.env, login="kit_review_source",
            groups="stock_request.group_stock_request_warehouse_responsible")
        cls.other = new_test_user(cls.env, login="kit_review_other",
            groups="stock_request.group_stock_request_warehouse_responsible")
        cls.source = cls.env["stock.warehouse"].create({"name": "Review Source", "code": "KRS",
            "company_id": cls.company.id,
            "stock_request_responsible_user_ids": [Command.set(cls.responsible.ids)]})
        cls.destination = cls.env["stock.warehouse"].create({"name": "Review Destination", "code": "KRD",
            "company_id": cls.company.id,
            "default_stock_request_source_warehouse_id": cls.source.id})
        cls.product = cls.env["product.product"].create({"name": "Review regular", "type": "consu"})
        cls.component = cls.env["product.product"].create({"name": "Review component", "type": "consu"})

    def _values(self, product=None, quantity=2):
        product = product or self.product
        return {"product_id": product.id, "product_uom_id": product.uom_id.id,
                "product_uom_qty": quantity}

    def test_product_field_context_is_a_mapping_for_onchange(self):
        from collections.abc import Mapping
        from lxml import etree
        field = self.env["stock.request"]._fields["product_id"]
        self.assertIsInstance(field.context, Mapping)
        # web_read expands the context with ** during onchange, even for an
        # empty new line. Dynamic company context belongs in the XML view.
        self.env["product.product"].with_context(**field.context)
        for xmlid in ("stock_request.stock_request_order_form",
                      "stock_request.stock_request_one_step_transfer_form"):
            arch = etree.fromstring(self.env.ref(xmlid).arch_db)
            node = arch.xpath("//field[@name='stock_request_ids']/list/field[@name='product_id']")[0]
            self.assertEqual(node.get("context"), "{'stock_request_company_id': parent.company_id, 'stock_request_hide_kits': True}")

    def _order(self, operation="request"):
        order = self.env["stock.request.order"].sudo().create({
            "operation_type": operation, "company_id": self.company.id,
            "warehouse_id": self.destination.id, "source_warehouse_id": self.source.id,
            "requested_by": self.requester.id, "expected_date": fields.Datetime.now(),
            "stock_request_ids": [Command.create(self._values())],
        })
        order.write({"transfer_flow": "two_step"})
        return order

    def test_no_transit_requester_edits_and_preparation_summary(self):
        self.env["ir.config_parameter"].sudo().set_param("stock_request.preparation_summary_enabled", "True")
        report = self.env["stock.request.preparation.summary"].with_user(self.responsible)
        for stage in ("draft", "submitted", "ready", "in_transit"):
            order = self._order()
            order.write({"transfer_flow": "two_step_no_transit", "state": stage})
            if stage == "ready":
                order.stock_request_ids.write({"state": "open"})
            editable = order.with_user(self.requester)
            old_line = editable.stock_request_ids
            editable.write({"stock_request_ids": [
                Command.update(old_line.id, {"product_id": self.component.id,
                                            "product_uom_id": self.component.uom_id.id,
                                            "product_uom_qty": 7}),
                Command.create(self._values(quantity=3)),
            ]})
            self.assertEqual(editable.state, stage)
            self.assertFalse(editable.stock_request_ids.mapped("operation_move_ids"))
            domain = [("source_warehouse_id", "=", self.source.id)]
            if stage == "ready":
                rows = report.web_search_read(domain, {"quantity": {}, "order_count": {}})["records"]
                self.assertEqual(sum(row["quantity"] for row in rows), 10)
                self.assertTrue(all(row["order_count"] == 1 for row in rows))
            editable.write({"stock_request_ids": [Command.delete(old_line.id)]})
            self.assertEqual(editable.state, stage)
            if stage == "ready":
                rows = report.web_search_read(domain, {"quantity": {}})["records"]
                self.assertEqual(sum(row["quantity"] for row in rows), 3)
                editable.stock_request_ids.write({"product_uom_qty": 6})
                rows = report.web_search_read(domain, {"quantity": {}})["records"]
                self.assertEqual(sum(row["quantity"] for row in rows), 6)
            order.write({"state": "done"})
            with self.assertRaises(AccessError), self.cr.savepoint():
                editable.stock_request_ids.write({"product_uom_qty": 9})

    def test_draft_and_source_review_keep_stage_and_use_new_lines(self):
        order = self._order().with_user(self.requester)
        line = order.stock_request_ids
        order.write({"stock_request_ids": [Command.update(line.id, {"product_uom_qty": 5}),
                                          Command.create(self._values(self.component, 3))]})
        self.assertEqual(order.state, "draft")
        order.sudo().write({"state": "submitted"})
        review = order.with_user(self.responsible)
        review.write({"stock_request_ids": [Command.update(line.id, {"product_uom_qty": 8}),
                                           Command.create(self._values(self.component, 4))]})
        self.assertEqual(review.state, "submitted")
        self.assertEqual(line.product_uom_qty, 8)
        self.assertEqual(sorted(review.stock_request_ids.mapped("product_uom_qty")), [3, 4, 8])
        self.assertEqual(set(review.stock_request_ids.mapped("state")), {"draft"})
        self.assertFalse(review.stock_request_ids.mapped("operation_move_ids"))
        self.assertEqual(set(review.stock_request_ids.mapped("requested_by").ids), {self.requester.id})

    def test_no_transit_non_owner_can_edit_before_delivery(self):
        destination_user = new_test_user(self.env, login="kit_review_destination",
            groups="stock_request.group_stock_request_warehouse_responsible")
        manager = new_test_user(self.env, login="kit_review_manager",
            groups="stock_request.group_stock_request_manager")
        self.destination.stock_request_responsible_user_ids = destination_user
        for user in (self.responsible, destination_user, manager):
            for stage in ("draft", "submitted", "ready", "in_transit"):
                with self.subTest(user=user.login, stage=stage):
                    order = self._order()
                    order.write({"transfer_flow": "two_step_no_transit", "state": stage})
                    if stage == "ready":
                        order.stock_request_ids.write({"state": "open"})
                    editable = order.with_user(user).sudo(False)
                    self.assertTrue(editable.can_requester_edit_no_transit_lines)
                    line = editable.stock_request_ids
                    editable.write({"stock_request_ids": [
                        Command.update(line.id, {"product_uom_qty": 5}),
                        Command.create(self._values(self.component, 3)),
                    ]})
                    editable.write({"stock_request_ids": [Command.delete(line.id)]})
                    self.assertEqual(editable.state, stage)
                    self.assertEqual(editable.stock_request_ids.product_uom_qty, 3)
                    self.assertEqual(editable.requested_by, self.requester)
                    if stage == "in_transit":
                        self.assertEqual(editable.stock_request_ids.state, "open")
                        self.assertEqual(editable.stock_request_ids.delivered_qty, 3)
                        self.assertEqual(editable.stock_request_ids.remaining_receipt_qty, 3)
                        self.assertEqual(editable.stock_request_ids.remaining_delivery_qty, 0)
                        self.assertFalse(editable.stock_request_ids.operation_move_ids)
                    with self.assertRaises(AccessError), self.cr.savepoint():
                        order.with_user(self.other).sudo(False).write({
                            "stock_request_ids": [Command.create(self._values())]})
                    order.write({"state": "done"})
                    self.assertFalse(editable.can_requester_edit_no_transit_lines)
                    with self.assertRaises(UserError), self.cr.savepoint():
                        editable.stock_request_ids.unlink()

    def test_review_permissions_and_approved_lock(self):
        order = self._order()
        order.write({"state": "submitted"})
        for user in (self.requester, self.other):
            with self.assertRaises(AccessError), self.cr.savepoint():
                order.with_user(user).write({"stock_request_ids": [Command.create(self._values())]})
        for state in ("ready", "in_transit", "done", "cancelled", "rejected"):
            order.write({"state": state})
            for user in (self.responsible, self.env.user):
                normal_order = order.with_user(user).sudo(False)
                with self.assertRaises(AccessError), self.cr.savepoint():
                    normal_order.write({"stock_request_ids": [Command.create(self._values())]})
                with self.assertRaises(AccessError), self.cr.savepoint():
                    normal_order.stock_request_ids.write({"product_uom_qty": 9})

    def test_kit_selection_new_lines_and_legacy_lines(self):
        if "mrp.bom" not in self.env:
            self.skipTest("Manufacturing is not installed")
        # The existing regular product becomes a Kit after the order exists.
        old = self._order()
        bom = self.env["mrp.bom"].create({
            "product_tmpl_id": self.product.product_tmpl_id.id,
            "type": "phantom", "product_qty": 1,
            "bom_line_ids": [Command.create({"product_id": self.component.id, "product_qty": 1})],
        })
        products = self.env["product.product"].with_user(self.responsible)
        selector = products.with_context(stock_request_hide_kits=True,
                                        stock_request_company_id=self.company.id)
        self.assertFalse(selector.search([("id", "=", self.product.id)]))
        self.assertFalse(selector.name_search(self.product.name, domain=[("id", "=", self.product.id)]))
        self.assertTrue(selector.search([("id", "=", self.component.id)]))
        self.assertTrue(products.search([("id", "=", self.product.id)]))
        self.assertFalse(products.search([("id", "=", self.product.id), ("stock_request_is_kit", "=", False)]))
        self.assertEqual(products.search([("id", "=", self.component.id), ("stock_request_is_kit", "=", False)]).id, self.component.id)
        old.with_user(self.requester).stock_request_ids.write({"product_uom_qty": 3})
        self.assertEqual(old.stock_request_ids.product_id, self.product)
        for operation in ("request", "transfer"):
            with self.assertRaises(ValidationError), self.cr.savepoint():
                self._order(operation)
        bom.type = "normal"
        self.assertTrue(products.search([("id", "=", self.product.id), ("stock_request_is_kit", "=", False)]))

    def test_review_commands_cannot_remove_or_replace_existing_products(self):
        order = self._order()
        order.write({"state": "submitted"})
        line = order.stock_request_ids
        for command in (Command.delete(line.id), Command.clear(),
                        Command.update(line.id, {"product_id": self.component.id}),
                        Command.create(dict(self._values(), closed_receipt_qty=20))):
            with self.assertRaises(AccessError), self.cr.savepoint():
                order.with_user(self.responsible).write({"stock_request_ids": [command]})
