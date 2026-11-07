# Copyright 2017 ForgeFlow S.L.
# Copyright 2022-2023 Tecnativa - Víctor Martínez
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl-3.0).

from collections import Counter
from datetime import datetime, timedelta

from odoo import Command, exceptions, fields
from odoo.tests import Form, new_test_user
from odoo.tools.safe_eval import safe_eval

from odoo.addons.base.tests.common import BaseCommon


class TestStockRequest(BaseCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # common models
        cls.stock_request = cls.env["stock.request"]
        cls.request_order = cls.env["stock.request.order"]
        # refs
        cls.stock_request_user_group = cls.env.ref(
            "stock_request.group_stock_request_user"
        )
        cls.main_company = cls.env.ref("base.main_company")
        cls.main_company.internal_transit_location_id.active = True
        cls.warehouse = cls.env.ref("stock.warehouse0")
        cls.categ_unit = cls.env.ref("uom.product_uom_categ_unit")
        cls.virtual_loc = cls.env.ref("stock.stock_location_customers")
        # common data
        cls.company_2 = cls.env["res.company"].create(
            {"name": "Comp2", "parent_id": cls.main_company.id}
        )
        cls.company_2_address = (
            cls.env["res.partner"]
            .with_context(company_id=cls.company_2.id)
            .create({"name": "Peñiscola"})
        )
        cls.wh2 = cls.env["stock.warehouse"].search(
            [("company_id", "=", cls.company_2.id)], limit=1
        )
        cls.stock_request_user = new_test_user(
            cls.env,
            login="stock_request_user",
            groups="stock_request.group_stock_request_user",
            company_ids=[(4, cls.main_company.id), (4, cls.company_2.id)],
        )
        cls.stock_request_manager = new_test_user(
            cls.env,
            login="stock_request_manager",
            groups="stock_request.group_stock_request_manager",
            company_ids=[(4, cls.main_company.id), (4, cls.company_2.id)],
        )
        cls.request_source_warehouse = cls.env["stock.warehouse"].create(
            {
                "name": "Stock Request Source",
                "code": "SREQ",
                "company_id": cls.main_company.id,
            }
        )
        cls.warehouse.write(
            {
                "stock_request_responsible_user_ids": [
                    Command.set(cls.stock_request_manager.ids)
                ],
                "default_stock_request_source_warehouse_id": (
                    cls.request_source_warehouse.id
                ),
            }
        )
        cls.request_source_warehouse.stock_request_responsible_user_ids = [
            Command.set(cls.stock_request_manager.ids)
        ]
        cls.stock_request_user.default_stock_request_warehouse_id = cls.warehouse
        cls.product = cls._create_product("SH", "Shoes", False)
        cls.product_company_2 = cls._create_product("SH_2", "Shoes", cls.company_2.id)

        cls.ressuply_loc = cls._create_location(
            name="Ressuply",
            location_id=cls.warehouse.view_location_id.id,
            company_id=cls.main_company.id,
        )
        cls.ressuply_loc_2 = cls._create_location(
            name="Ressuply",
            location_id=cls.wh2.view_location_id.id,
            company_id=cls.company_2.id,
        )

        cls.route = cls._create_route(name="Transfer", company_id=cls.main_company.id)
        cls.route_2 = cls._create_route(name="Transfer", company_id=cls.company_2.id)
        cls.route_3 = cls._create_route(name="Transfer", company_id=cls.main_company.id)
        cls.uom_dozen = cls.env["uom.uom"].create(
            {
                "name": "Test-DozenA",
                "category_id": cls.categ_unit.id,
                "factor_inv": 12,
                "uom_type": "bigger",
                "rounding": 0.001,
            }
        )

        cls.env["stock.rule"].create(
            {
                "name": "Transfer",
                "route_id": cls.route.id,
                "location_src_id": cls.ressuply_loc.id,
                "location_dest_id": cls.warehouse.lot_stock_id.id,
                "action": "pull",
                "picking_type_id": cls.warehouse.int_type_id.id,
                "procure_method": "make_to_stock",
                "warehouse_id": cls.warehouse.id,
                "company_id": cls.main_company.id,
            }
        )

        cls.env["stock.rule"].create(
            {
                "name": "Transfer",
                "route_id": cls.route_2.id,
                "location_src_id": cls.ressuply_loc_2.id,
                "location_dest_id": cls.wh2.lot_stock_id.id,
                "action": "pull",
                "picking_type_id": cls.wh2.int_type_id.id,
                "procure_method": "make_to_stock",
                "warehouse_id": cls.wh2.id,
                "company_id": cls.company_2.id,
            }
        )

        cls.env["ir.config_parameter"].sudo().set_param(
            "stock.no_auto_scheduler", "True"
        )
        # The historical suite validates the original two-step workflow.
        cls.env["ir.config_parameter"].sudo().set_param(
            "stock_request.transfer_flow", "two_step"
        )
        cls.env[
            "res.config.settings"
        ]._sync_stock_request_transfer_flow_menus("two_step")

    @classmethod
    def _create_product(cls, default_code, name, company_id, **vals):
        return cls.env["product.product"].create(
            dict(
                name=name,
                default_code=default_code,
                uom_id=cls.env.ref("uom.product_uom_unit").id,
                company_id=company_id,
                type="consu",
                is_storable=True,
                **vals,
            )
        )

    @classmethod
    def _create_product_template_attribute_line(
        cls, product_tmpl_id, attribute_id, value_id
    ):
        return cls.env["product.template.attribute.line"].create(
            {
                "product_tmpl_id": product_tmpl_id,
                "attribute_id": attribute_id,
                "value_ids": value_id,
            }
        )

    @classmethod
    def _create_product_attribute_value(cls, name, attribute):
        return cls.env["product.attribute.value"].create(
            {"name": name, "attribute_id": attribute}
        )

    @classmethod
    def _create_product_attribute(cls, name):
        return cls.env["product.attribute"].create({"name": name})

    @classmethod
    def _create_location(cls, **vals):
        return cls.env["stock.location"].create(dict(usage="internal", **vals))

    @classmethod
    def _create_route(cls, **vals):
        return cls.env["stock.route"].create(
            dict(
                product_categ_selectable=False,
                product_selectable=True,
                sequence=10,
                **vals,
            )
        )


class TestStockRequestBase(TestStockRequest):
    def _create_stock_quant(self, product, location, qty):
        self.env["stock.quant"].create(
            {"product_id": product.id, "location_id": location.id, "quantity": qty}
        )

    def test_defaults(self):
        vals = {
            "product_id": self.product.id,
            "product_uom_id": self.product.uom_id.id,
            "product_uom_qty": 5.0,
        }
        stock_request = (
            self.stock_request.with_user(self.stock_request_user)
            .with_context(company_id=self.main_company.id)
            .sudo()
            .create(vals)
        )

        self.assertEqual(stock_request.requested_by, self.stock_request_user)

        self.assertEqual(stock_request.warehouse_id, self.warehouse)

        self.assertEqual(stock_request.location_id, self.warehouse.lot_stock_id)

    def test_defaults_order(self):
        vals = {}
        order = (
            self.request_order.with_user(self.stock_request_user)
            .with_context(company_id=self.main_company.id)
            .create(vals)
        )

        self.assertEqual(order.requested_by, self.stock_request_user)

        self.assertEqual(order.warehouse_id, self.warehouse)

        self.assertEqual(order.location_id, self.warehouse.lot_stock_id)

        self.assertEqual(
            order.source_warehouse_id, self.request_source_warehouse
        )

        self.assertEqual(
            order.source_location_id,
            self.request_source_warehouse.lot_stock_id,
        )

        self.assertEqual(order.request_date, fields.Date.context_today(order))

        self.assertEqual(order.priority, "0")

    def test_simplified_order_fields(self):
        for field_name in (
            "request_date",
            "priority",
            "reason",
            "source_warehouse_id",
            "source_location_id",
            "template_id",
        ):
            self.assertIn(field_name, self.request_order._fields)
        self.assertEqual(
            self.request_order._fields["template_id"].comodel_name,
            "stock.request.template",
        )
        self.assertNotIn("is_template", self.request_order._fields)
        self.assertNotIn("template_name", self.request_order._fields)
        self.assertNotIn("picking_policy", self.request_order._fields)
        self.assertNotIn("picking_policy", self.stock_request._fields)

    def test_order_template_copies_products_and_quantities(self):
        destination = self.env["stock.warehouse"].create(
            {
                "name": "Template Destination",
                "code": "TPLD",
                "company_id": self.main_company.id,
            }
        )
        template = self.env["stock.request.template"].create(
            {
                "name": "Shoes Replenishment",
                "priority": "1",
                "notes": "Replenish the main shoe stock.",
                "line_ids": [
                    Command.create(
                        {
                            "product_id": self.product.id,
                            "product_uom_qty": 7.0,
                        }
                    )
                ],
            }
        )

        order = self.request_order.create(
            {
                "template_id": template.id,
                "source_warehouse_id": self.request_source_warehouse.id,
                "warehouse_id": destination.id,
                "stock_request_ids": [
                    Command.create(
                        {
                            "product_id": self.product.id,
                            "product_uom_id": self.product.uom_id.id,
                            "product_uom_qty": 2.0,
                        }
                    ),
                    Command.create(
                        {
                            "product_id": self.product.id,
                            "product_uom_id": self.product.uom_id.id,
                            "product_uom_qty": 3.0,
                        }
                    ),
                ],
            }
        )
        self.assertEqual(len(order.stock_request_ids), 2)
        self.assertEqual(
            sorted(order.stock_request_ids.mapped("product_uom_qty")),
            [2.0, 3.0],
        )
        self.assertFalse(order.picking_ids)

        order.action_load_template()

        self.assertEqual(len(order.stock_request_ids), 1)
        self.assertEqual(order.stock_request_ids.product_id, self.product)
        self.assertEqual(order.stock_request_ids.product_uom_qty, 7.0)
        self.assertEqual(
            order.source_warehouse_id,
            self.request_source_warehouse,
        )
        self.assertEqual(order.warehouse_id, destination)
        self.assertEqual(
            order.stock_request_ids.source_location_id,
            self.request_source_warehouse.lot_stock_id,
        )
        self.assertEqual(
            order.stock_request_ids.location_id,
            destination.lot_stock_id,
        )
        self.assertEqual(order.priority, "1")
        self.assertEqual(order.reason, "Replenish the main shoe stock.")
        self.assertEqual(order.state, "draft")
        self.assertFalse(order.picking_ids)

        order.action_load_template()

        self.assertEqual(len(order.stock_request_ids), 1)
        self.assertEqual(order.stock_request_ids.product_uom_qty, 7.0)
        self.assertFalse(order.picking_ids)

        second_order = self.request_order.create(
            {
                "template_id": template.id,
                "source_warehouse_id": self.warehouse.id,
                "warehouse_id": self.request_source_warehouse.id,
            }
        )
        second_order.action_load_template()
        self.assertEqual(second_order.source_warehouse_id, self.warehouse)
        self.assertEqual(
            second_order.warehouse_id,
            self.request_source_warehouse,
        )
        self.assertEqual(
            second_order.stock_request_ids.product_uom_qty,
            7.0,
        )
        self.assertEqual(
            second_order.stock_request_ids.source_location_id,
            self.warehouse.lot_stock_id,
        )
        self.assertEqual(
            second_order.stock_request_ids.location_id,
            self.request_source_warehouse.lot_stock_id,
        )

    def test_user_default_request_warehouse(self):
        self.assertFalse(
            self.env["res.users"]._fields[
                "default_stock_request_warehouse_id"
            ].store
        )
        destination = self.env["stock.warehouse"].create(
            {
                "name": "Default Request Warehouse",
                "code": "DRW",
                "company_id": self.main_company.id,
            }
        )
        self.stock_request_user.default_stock_request_warehouse_id = destination

        order = (
            self.request_order.with_user(self.stock_request_user)
            .with_company(self.main_company)
            .create({})
        )

        self.assertEqual(order.warehouse_id, destination)
        self.assertEqual(order.location_id, destination.lot_stock_id)

    def test_warehouse_responsible_users_are_derived_automatically(self):
        self.assertFalse(
            self.request_order._fields["source_responsible_user_ids"].store
        )
        self.assertFalse(
            self.request_order._fields[
                "destination_responsible_user_ids"
            ].store
        )
        source_user = new_test_user(
            self.env,
            login="task05_source_responsible",
            groups="stock.group_stock_user",
            company_ids=[Command.link(self.main_company.id)],
        )
        destination_user = new_test_user(
            self.env,
            login="task05_destination_responsible",
            groups="stock.group_stock_user",
            company_ids=[Command.link(self.main_company.id)],
        )
        destination = self.env["stock.warehouse"].create(
            {
                "name": "Task 05 Destination",
                "code": "T05",
                "company_id": self.main_company.id,
            }
        )
        self.warehouse.stock_request_responsible_user_ids = [
            Command.set((source_user | self.stock_request_manager).ids)
        ]
        destination.stock_request_responsible_user_ids = [
            Command.set(destination_user.ids)
        ]

        order = self.request_order.create(
            {
                "source_warehouse_id": self.warehouse.id,
                "warehouse_id": destination.id,
            }
        )

        self.assertEqual(
            order.source_responsible_user_ids,
            source_user | self.stock_request_manager,
        )
        self.assertEqual(
            order.destination_responsible_user_ids,
            destination_user,
        )
        self.assertTrue(
            order.with_user(source_user).is_source_warehouse_responsible
        )
        self.assertTrue(
            order.with_user(destination_user).is_destination_warehouse_responsible
        )

    def test_only_source_responsible_can_accept_request(self):
        source_user = new_test_user(
            self.env,
            login="task05_accept_source",
            groups="stock.group_stock_user",
            company_ids=[Command.link(self.main_company.id)],
        )
        destination_user = new_test_user(
            self.env,
            login="task05_receive_destination",
            groups="stock.group_stock_user",
            company_ids=[Command.link(self.main_company.id)],
        )
        self.warehouse.stock_request_responsible_user_ids = [
            Command.set(source_user.ids)
        ]
        destination = self.env["stock.warehouse"].create(
            {
                "name": "Task 05 Receiving Warehouse",
                "code": "T5R",
                "company_id": self.main_company.id,
                "resupply_wh_ids": [Command.set(self.warehouse.ids)],
                "stock_request_responsible_user_ids": [
                    Command.set(destination_user.ids)
                ],
            }
        )
        order = self.request_order.create(
            {
                "source_warehouse_id": self.warehouse.id,
                "warehouse_id": destination.id,
                "stock_request_ids": [
                    Command.create(
                        {
                            "product_id": self.product.id,
                            "product_uom_id": self.product.uom_id.id,
                            "product_uom_qty": 5.0,
                        }
                    )
                ],
            }
        )

        self.assertTrue(order.with_user(destination_user).read(["name"]))
        with self.assertRaises(exceptions.AccessError):
            order.with_user(destination_user).write(
                {"reason": "Destination cannot accept"}
            )
        with self.assertRaises(exceptions.AccessError):
            order.with_user(destination_user).action_confirm()

        order.action_submit()
        order.with_user(source_user).action_confirm()
        self.assertEqual(order.state, "ready")

    def test_request_and_direct_transfer_flows(self):
        source_user = new_test_user(
            self.env,
            login="task06_source_responsible",
            groups="stock.group_stock_user",
            company_ids=[Command.link(self.main_company.id)],
        )
        other_inventory_user = new_test_user(
            self.env,
            login="task06_other_inventory_user",
            groups="stock.group_stock_user",
            company_ids=[Command.link(self.main_company.id)],
        )
        self.warehouse.stock_request_responsible_user_ids = [
            Command.set(source_user.ids)
        ]
        self.assertTrue(
            source_user.has_group(
                "stock_request.group_stock_request_warehouse_responsible"
            )
        )
        self.assertFalse(
            other_inventory_user.has_group(
                "stock_request.group_stock_request_warehouse_responsible"
            )
        )
        destination = self.env["stock.warehouse"].create(
            {
                "name": "Task 06 Destination",
                "code": "T06",
                "company_id": self.main_company.id,
                "resupply_wh_ids": [Command.set(self.warehouse.ids)],
                "stock_request_responsible_user_ids": [
                    Command.set(self.stock_request_manager.ids)
                ],
            }
        )
        line_vals = {
            "product_id": self.product.id,
            "product_uom_id": self.product.uom_id.id,
            "product_uom_qty": 5.0,
        }
        request = self.request_order.with_user(self.stock_request_user).create(
            {
                "operation_type": "request",
                "source_warehouse_id": self.warehouse.id,
                "warehouse_id": destination.id,
                "stock_request_ids": [Command.create(line_vals)],
            }
        )

        request.with_user(self.stock_request_user).action_submit()
        self.assertEqual(request.state, "submitted")
        self.assertTrue(request.submitted_date)
        self.assertFalse(request.picking_ids)
        with self.assertRaises(exceptions.AccessError):
            request.with_user(self.stock_request_user).action_confirm()
        request.with_user(source_user).action_confirm()
        self.assertEqual(request.state, "ready")
        self.assertTrue(request.ready_date)
        self.assertGreaterEqual(request.review_time, 0.0)
        self.assertTrue(request.picking_ids)

        direct_transfer = self.request_order.with_user(source_user).create(
            {
                "operation_type": "transfer",
                "source_warehouse_id": self.warehouse.id,
                "warehouse_id": destination.id,
                "stock_request_ids": [Command.create(line_vals)],
            }
        )
        self.assertEqual(direct_transfer.state, "draft")
        direct_transfer.with_user(source_user).action_confirm()
        self.assertEqual(direct_transfer.state, "ready")
        self.assertFalse(direct_transfer.submitted_date)
        self.assertTrue(direct_transfer.ready_date)
        self.assertEqual(direct_transfer.review_time, 0.0)
        self.assertTrue(direct_transfer.picking_ids)

        with self.assertRaises(exceptions.AccessError):
            self.request_order.with_user(other_inventory_user).create(
                {
                    "operation_type": "transfer",
                    "source_warehouse_id": self.warehouse.id,
                    "warehouse_id": destination.id,
                    "stock_request_ids": [Command.create(line_vals)],
                }
            )

    def test_delivery_and_receipt_follow_warehouse_responsibilities(self):
        source_user = new_test_user(
            self.env,
            login="task08_source_responsible",
            groups="stock.group_stock_user",
            company_ids=[Command.link(self.main_company.id)],
        )
        destination_user = new_test_user(
            self.env,
            login="task08_destination_responsible",
            groups="stock.group_stock_user",
            company_ids=[Command.link(self.main_company.id)],
        )
        self.warehouse.stock_request_responsible_user_ids = [
            Command.set(source_user.ids)
        ]
        destination = self.env["stock.warehouse"].create(
            {
                "name": "Task 08 Destination",
                "code": "T08",
                "company_id": self.main_company.id,
                "stock_request_responsible_user_ids": [
                    Command.set(destination_user.ids)
                ],
            }
        )
        order = self.request_order.with_user(self.stock_request_user).create(
            {
                "source_warehouse_id": self.warehouse.id,
                "warehouse_id": destination.id,
                "stock_request_ids": [
                    Command.create(
                        {
                            "product_id": self.product.id,
                            "product_uom_id": self.product.uom_id.id,
                            "product_uom_qty": 5.0,
                        }
                    )
                ],
            }
        )
        self._create_stock_quant(self.product, self.warehouse.lot_stock_id, 5)

        activity_model = self.env["mail.activity"].sudo()
        review_type = self.env.ref(
            "stock_request.mail_activity_type_stock_request_review"
        )
        receipt_type = self.env.ref(
            "stock_request.mail_activity_type_stock_request_receipt"
        )

        def activity_domain(activity_type):
            return [
                ("res_model", "=", "stock.request.order"),
                ("res_id", "=", order.id),
                ("activity_type_id", "=", activity_type.id),
            ]

        order.with_user(self.stock_request_user).action_submit()
        self.assertEqual(order.state, "submitted")
        self.assertFalse(order.picking_ids)
        self.assertTrue(order.has_pending_delivery)
        self.assertFalse(order.has_pending_receipt)
        review_activity = activity_model.search(activity_domain(review_type))
        self.assertEqual(len(review_activity), 1)
        self.assertEqual(review_activity.user_id, source_user)
        self.assertEqual(
            review_activity.summary, "New stock request requires review."
        )
        order._schedule_review_activities()
        self.assertEqual(
            activity_model.search_count(activity_domain(review_type)), 1
        )

        order.with_user(source_user).action_confirm()
        self.assertEqual(order.state, "ready")
        self.assertTrue(order.has_pending_delivery)
        self.assertFalse(order.has_pending_receipt)
        self.assertFalse(activity_model.search(activity_domain(review_type)))
        accepted_messages = order.message_ids.filtered(
            lambda message: "has been accepted" in str(message.body)
        )
        self.assertTrue(accepted_messages)
        self.assertIn(
            self.stock_request_user.partner_id,
            accepted_messages[0].partner_ids,
        )
        delivery = order.picking_ids.filtered(
            lambda picking: picking.stock_request_operation == "delivery"
        )
        receipt = order.picking_ids.filtered(
            lambda picking: picking.stock_request_operation == "receipt"
        )
        self.assertEqual(
            order.with_user(source_user).action_deliver_products()["res_id"],
            delivery.id,
        )
        with self.assertRaises(exceptions.AccessError):
            order.with_user(destination_user).action_deliver_products()
        with self.assertRaises(exceptions.AccessError):
            delivery.with_user(destination_user).button_validate()

        delivery.with_user(source_user).action_assign()
        delivery.move_line_ids.quantity = 5
        delivery.move_line_ids.picked = True
        delivery.with_user(source_user)._action_done()
        self.assertEqual(order.state, "in_transit")
        self.assertFalse(order.has_pending_delivery)
        self.assertTrue(order.has_pending_receipt)
        receipt_activity = activity_model.search(activity_domain(receipt_type))
        self.assertEqual(len(receipt_activity), 1)
        self.assertEqual(receipt_activity.user_id, destination_user)
        self.assertEqual(
            receipt_activity.summary, "Stock transfer is ready to receive."
        )
        order._schedule_receipt_activities()
        self.assertEqual(
            activity_model.search_count(activity_domain(receipt_type)), 1
        )
        deliver_domain = safe_eval(
            self.env.ref("stock_request.stock_request_to_deliver_action").domain,
            {"uid": source_user.id},
        )
        receive_domain = safe_eval(
            self.env.ref("stock_request.stock_request_to_receive_action").domain,
            {"uid": destination_user.id},
        )
        self.assertNotIn(
            order,
            self.request_order.with_user(source_user).search(deliver_domain),
        )
        self.assertIn(
            order,
            self.request_order.with_user(destination_user).search(receive_domain),
        )

        with self.assertRaises(exceptions.AccessError):
            order.with_user(source_user).action_receive_products()
        with self.assertRaises(exceptions.AccessError):
            receipt.with_user(source_user).button_validate()
        self.assertEqual(
            order.with_user(destination_user).action_receive_products()["res_id"],
            receipt.id,
        )

        receipt.with_user(destination_user).action_assign()
        receipt.move_line_ids.quantity = 5
        receipt.move_line_ids.picked = True
        receipt.with_user(destination_user)._action_done()

        self.assertEqual(order.state, "done")
        self.assertFalse(order.has_pending_delivery)
        self.assertFalse(order.has_pending_receipt)
        self.assertFalse(activity_model.search(activity_domain(receipt_type)))
        received_messages = order.message_ids.filtered(
            lambda message: "has been received" in str(message.body)
        )
        self.assertTrue(received_messages)
        self.assertIn(
            self.stock_request_user.partner_id,
            received_messages[0].partner_ids,
        )
        receipt_audit = order.message_ids.filtered(
            lambda message: receipt.display_name in str(message.body)
            and destination_user.display_name in str(message.body)
        )
        self.assertTrue(receipt_audit)
        self.assertNotIn(
            order,
            self.request_order.with_user(destination_user).search(receive_domain),
        )
        self.assertTrue(all(line.state == "done" for line in order.stock_request_ids))

    def test_two_step_transfer_configuration_validations(self):
        transit_location = self.main_company.internal_transit_location_id
        self.assertTrue(transit_location)
        self.assertTrue(transit_location.active)
        self.assertEqual(transit_location.usage, "transit")

        line_vals = {
            "product_id": self.product.id,
            "product_uom_id": self.product.uom_id.id,
            "product_uom_qty": 2.0,
        }
        same_warehouse_order = self.request_order.create(
            {
                "source_warehouse_id": self.warehouse.id,
                "warehouse_id": self.warehouse.id,
                "stock_request_ids": [Command.create(line_vals)],
            }
        )
        with self.assertRaises(exceptions.UserError):
            same_warehouse_order.action_submit()

        missing_transit_order = self.request_order.create(
            {
                "source_warehouse_id": self.request_source_warehouse.id,
                "warehouse_id": self.warehouse.id,
                "stock_request_ids": [Command.create(line_vals)],
            }
        )
        self.main_company.internal_transit_location_id = False
        with self.assertRaises(exceptions.UserError):
            missing_transit_order.action_submit()

    def test_transfer_flow_settings_control_menus(self):
        parameter_model = self.env["ir.config_parameter"].sudo()
        parameter_model.search(
            [("key", "=", "stock_request.transfer_flow")]
        ).unlink()
        defaults = self.env["res.config.settings"].default_get(
            ["stock_request_transfer_flow"]
        )
        self.assertEqual(defaults["stock_request_transfer_flow"], "one_step")

        one_step_settings = self.env["res.config.settings"].create(
            {"stock_request_transfer_flow": "one_step"}
        )
        one_step_settings.set_values()
        self.assertEqual(
            parameter_model.get_param("stock_request.transfer_flow"),
            "one_step",
        )
        self.assertFalse(
            self.env.ref("stock_request.menu_stock_request_operations").active
        )
        self.assertFalse(
            self.env.ref("stock_request.stock_request_to_transfer_menu").active
        )
        self.assertFalse(
            self.env.ref("stock_request.stock_request_warehouse_setup_menu").active
        )
        self.assertFalse(
            self.env.ref(
                "stock_request.stock_request_direct_transfer_menu"
            ).active
        )
        self.assertTrue(
            self.env.ref(
                "stock_request.stock_request_one_step_transfer_menu"
            ).active
        )
        self.assertTrue(
            self.env.ref(
                "stock_request.stock_request_one_step_return_menu"
            ).active
        )
        self.assertEqual(
            self.env.ref(
                "stock_request.stock_request_one_step_transfer_menu"
            ).name,
            "Transfer",
        )
        for menu_xmlid in (
            "stock_request.stock_request_to_deliver_menu",
            "stock_request.stock_request_to_receive_menu",
            "stock_request.stock_request_in_transit_menu",
        ):
            self.assertFalse(self.env.ref(menu_xmlid).active)
        for menu_xmlid in (
            "stock_request.menu_stock_request_reporting",
            "stock_request.menu_stock_request_configuration",
        ):
            self.assertTrue(self.env.ref(menu_xmlid).active)
        self.assertEqual(
            self.env.ref("stock_request.menu_stock_request_root").action,
            self.env.ref(
                "stock_request.stock_request_one_step_transfer_action"
            ),
        )

        two_step_settings = self.env["res.config.settings"].create(
            {"stock_request_transfer_flow": "two_step"}
        )
        two_step_settings.set_values()
        self.assertTrue(
            self.env.ref("stock_request.menu_stock_request_operations").active
        )
        self.assertTrue(
            self.env.ref("stock_request.stock_request_warehouse_setup_menu").active
        )
        self.assertFalse(
            self.env.ref("stock_request.stock_request_to_transfer_menu").active
        )
        self.assertTrue(
            self.env.ref(
                "stock_request.stock_request_direct_transfer_menu"
            ).active
        )
        self.assertEqual(
            self.env.ref(
                "stock_request.stock_request_direct_transfer_menu"
            ).name,
            "Direct Transfers",
        )
        self.assertFalse(
            self.env.ref(
                "stock_request.stock_request_one_step_transfer_menu"
            ).active
        )
        self.assertFalse(
            self.env.ref(
                "stock_request.stock_request_one_step_return_menu"
            ).active
        )
        for menu_xmlid in (
            "stock_request.stock_request_to_deliver_menu",
            "stock_request.stock_request_to_receive_menu",
            "stock_request.stock_request_in_transit_menu",
        ):
            self.assertTrue(self.env.ref(menu_xmlid).active)
        self.assertEqual(
            self.env.ref("stock_request.menu_stock_request_root").action,
            self.env.ref("stock_request.stock_request_my_requests_action"),
        )

    def test_one_step_transfer_moves_directly_and_completes(self):
        self.env["res.config.settings"].create(
            {"stock_request_transfer_flow": "one_step"}
        ).set_values()
        self._create_stock_quant(
            self.product,
            self.request_source_warehouse.lot_stock_id,
            10.0,
        )
        self._create_stock_quant(
            self.product,
            self.warehouse.lot_stock_id,
            4.0,
        )
        order = self.request_order.with_user(self.stock_request_manager).create(
            {
                "operation_type": "transfer",
                "source_warehouse_id": self.request_source_warehouse.id,
                "warehouse_id": self.warehouse.id,
            }
        )
        order.with_user(self.stock_request_manager).write(
            {
                "stock_request_ids": [
                    Command.create(
                        {
                            "product_id": self.product.id,
                            "product_uom_id": self.product.uom_id.id,
                            "product_uom_qty": 3.0,
                            # Reproduce a stale inline-editor context. The
                            # parent order must remain authoritative.
                            "source_location_id": self.warehouse.lot_stock_id.id,
                        }
                    )
                ],
            }
        )
        self.assertEqual(order.transfer_flow, "one_step")
        self.assertEqual(
            order.stock_request_ids.source_location_id,
            order.source_location_id,
        )
        self.assertEqual(order.stock_request_ids.available_qty, 10.0)

        order.with_user(
            self.stock_request_manager
        ).action_confirm_one_step_transfer()
        self.assertEqual(order.state, "done")
        self.assertEqual(order.one_step_transfer_state, "transferred")
        self.assertFalse(order.transit_location_id)
        self.assertEqual(len(order.stock_request_operation_picking_ids), 1)
        picking = order.stock_request_operation_picking_ids
        self.assertEqual(picking.state, "done")
        self.assertEqual(picking.stock_request_operation, "transfer")
        self.assertEqual(
            picking.location_id,
            self.request_source_warehouse.lot_stock_id,
        )
        self.assertEqual(picking.location_dest_id, self.warehouse.lot_stock_id)
        self.assertEqual(len(picking.move_ids), 1)
        self.assertEqual(len(picking.move_ids.allocation_ids), 1)
        with self.assertRaises(exceptions.UserError):
            order.with_user(self.stock_request_manager).action_receive_products()

        order.invalidate_recordset()
        line = order.stock_request_ids
        line.invalidate_recordset(["available_qty"])
        self.assertEqual(order.state, "done")
        self.assertEqual(line.available_qty, 7.0)
        self.assertEqual(line.delivered_qty, 3.0)
        self.assertEqual(line.received_qty, 3.0)
        self.assertEqual(line.in_transit_qty, 0.0)
        self.assertEqual(line.state, "done")
        self.assertEqual(order.delivered_by_id, self.stock_request_manager)
        self.assertEqual(order.received_by_id, self.stock_request_manager)
        self.assertEqual(order.delivery_date, order.receipt_date)
        self.assertFalse(order.has_pending_delivery)
        self.assertFalse(order.has_pending_receipt)
        self.assertTrue(
            order.message_ids.filtered(
                lambda message: "has been completed" in str(message.body)
            )
        )

        transfer_form = self.env.ref(
            "stock_request.stock_request_one_step_transfer_form"
        ).get_combined_arch()
        transfer_list = self.env.ref(
            "stock_request.stock_request_one_step_transfer_list"
        ).get_combined_arch()
        for removed_field in ("requested_by", "request_date"):
            self.assertFalse(
                transfer_list.xpath("//list/field[@name='%s']" % removed_field)
            )
        self.assertTrue(
            transfer_form.xpath(
                "//button[@name='action_confirm_one_step_transfer']"
            )
        )
        self.assertTrue(
            transfer_form.xpath(
                "//field[@name='one_step_transfer_state' and "
                "@statusbar_visible='draft,transferred,cancelled']"
            )
        )
        self.assertTrue(
            transfer_form.xpath(
                "//group[@string='Products']/field"
                "[@name='stock_request_ids' and @colspan='2']"
            )
        )
        self.assertFalse(
            transfer_form.xpath(
                "//sheet/group/group/field[@name='requested_by' or "
                "@name='request_date']"
            )
        )
        for hidden_field in ("requested_by", "request_date"):
            self.assertTrue(
                transfer_form.xpath(
                    "//sheet/field[@name='%s' and @invisible='1']" % hidden_field
                )
            )
        self.assertTrue(
            transfer_form.xpath(
                "//field[@name='stock_request_ids']/list/field"
                "[@name='available_qty' and @string='Available Qty' and "
                "@optional='show' and @readonly='1' and "
                "@column_invisible='not parent.show_available_qty']"
            )
        )
        self.assertTrue(
            transfer_form.xpath(
                "//sheet/field[@name='show_available_qty' and @invisible='1']"
            )
        )
        parameter_model = self.env["ir.config_parameter"].sudo()
        self.env["res.config.settings"].create(
            {
                "stock_request_transfer_flow": "one_step",
                "stock_request_show_available_qty": False,
            }
        ).set_values()
        self.assertEqual(
            parameter_model.get_param("stock_request.show_available_qty"),
            False,
        )
        order.invalidate_recordset(["show_available_qty"])
        self.assertFalse(order.show_available_qty)
        self.env["res.config.settings"].create(
            {
                "stock_request_transfer_flow": "one_step",
                "stock_request_show_available_qty": True,
            }
        ).set_values()
        order.invalidate_recordset(["show_available_qty"])
        self.assertTrue(order.show_available_qty)
        for forbidden_field in (
            "delivered_qty",
            "received_qty",
            "remaining_delivery_qty",
            "remaining_receipt_qty",
            "transit_location_id",
        ):
            self.assertFalse(
                transfer_form.xpath("//field[@name='%s']" % forbidden_field)
            )

        transfer_action = self.env.ref(
            "stock_request.stock_request_one_step_transfer_action"
        )
        self.assertEqual(transfer_action.name, "Transfer")
        self.assertEqual(transfer_action.res_model, "stock.request.order")
        self.assertEqual(transfer_action.view_mode, "list,form")
        self.assertEqual(
            transfer_action.view_id,
            self.env.ref("stock_request.stock_request_one_step_transfer_list"),
        )
        self.assertIn(
            ("transfer_flow", "=", "one_step"),
            safe_eval(transfer_action.domain),
        )
        self.assertIn(
            ("is_return", "=", False),
            safe_eval(transfer_action.domain),
        )

        cancelled_order = self.request_order.with_user(
            self.stock_request_manager
        ).create(
            {
                "operation_type": "transfer",
                "source_warehouse_id": self.request_source_warehouse.id,
                "warehouse_id": self.warehouse.id,
                "stock_request_ids": [
                    Command.create(
                        {
                            "product_id": self.product.id,
                            "product_uom_id": self.product.uom_id.id,
                            "product_uom_qty": 1.0,
                        }
                    )
                ],
            }
        )
        self.assertEqual(cancelled_order.one_step_transfer_state, "draft")
        cancelled_order.with_user(self.stock_request_manager).action_cancel()
        self.assertEqual(cancelled_order.state, "cancelled")
        self.assertEqual(cancelled_order.one_step_transfer_state, "cancelled")

    def test_one_step_returns_are_separate_linked_and_quantity_safe(self):
        self.env["res.config.settings"].create(
            {"stock_request_transfer_flow": "one_step"}
        ).set_values()
        self._create_stock_quant(
            self.product,
            self.request_source_warehouse.lot_stock_id,
            10.0,
        )
        original = self.request_order.with_user(
            self.stock_request_manager
        ).create(
            {
                "operation_type": "transfer",
                "source_warehouse_id": self.request_source_warehouse.id,
                "warehouse_id": self.warehouse.id,
                "stock_request_ids": [
                    Command.create(
                        {
                            "product_id": self.product.id,
                            "product_uom_id": self.product.uom_id.id,
                            "product_uom_qty": 6.0,
                        }
                    )
                ],
            }
        )
        original.with_user(
            self.stock_request_manager
        ).action_confirm_one_step_transfer()
        self.assertEqual(original.state, "done")
        self.assertTrue(original.name.startswith("SRO/"))

        return_action = original.with_user(
            self.stock_request_manager
        ).action_create_return()
        generated_return = self.request_order.browse(return_action["res_id"])
        self.assertTrue(generated_return.name.startswith("RET/"))
        self.assertEqual(generated_return.state, "draft")
        self.assertEqual(generated_return.original_transfer_id, original)
        self.assertEqual(generated_return.source_warehouse_id, self.warehouse)
        self.assertEqual(
            generated_return.warehouse_id,
            self.request_source_warehouse,
        )
        generated_return.unlink()

        return_model = self.request_order.with_user(
            self.stock_request_manager
        ).with_context(default_is_return=True)
        first_return = return_model.create(
            {
                "is_return": True,
                "original_transfer_id": original.id,
                "stock_request_ids": [
                    Command.create(
                        {
                            "product_id": self.product.id,
                            "product_uom_id": self.product.uom_id.id,
                            "product_uom_qty": 2.0,
                        }
                    )
                ],
            }
        )
        self.assertTrue(first_return.name.startswith("RET/"))
        self.assertEqual(first_return.original_transfer_id, original)
        self.assertEqual(first_return.source_warehouse_id, self.warehouse)
        self.assertEqual(
            first_return.warehouse_id,
            self.request_source_warehouse,
        )
        first_return.with_user(
            self.stock_request_manager
        ).action_confirm_one_step_return()
        self.assertEqual(first_return.state, "done")
        self.assertEqual(first_return.one_step_return_state, "returned")

        second_return = return_model.create(
            {
                "is_return": True,
                "original_transfer_id": original.id,
                "stock_request_ids": [
                    Command.create(
                        {
                            "product_id": self.product.id,
                            "product_uom_id": self.product.uom_id.id,
                            "product_uom_qty": 4.0,
                        }
                    )
                ],
            }
        )
        second_return.with_user(
            self.stock_request_manager
        ).action_confirm_one_step_return()
        original.invalidate_recordset(["return_count"])
        self.assertEqual(original.return_count, 2)
        self.assertNotEqual(first_return.name, second_return.name)

        excessive_return = return_model.create(
            {
                "is_return": True,
                "original_transfer_id": original.id,
                "stock_request_ids": [
                    Command.create(
                        {
                            "product_id": self.product.id,
                            "product_uom_id": self.product.uom_id.id,
                            "product_uom_qty": 1.0,
                        }
                    )
                ],
            }
        )
        with self.assertRaises(exceptions.UserError):
            excessive_return.with_user(
                self.stock_request_manager
            ).action_confirm_one_step_return()

        returns_action = original.action_view_returns()
        self.assertEqual(
            returns_action["domain"],
            [("original_transfer_id", "=", original.id)],
        )
        original_action = first_return.action_view_original_transfer()
        self.assertEqual(original_action["res_id"], original.id)
        with self.assertRaises(exceptions.UserError):
            original.action_create_return()

        return_action = self.env.ref(
            "stock_request.stock_request_one_step_return_action"
        )
        self.assertIn(("is_return", "=", True), safe_eval(return_action.domain))
        return_form = self.env.ref(
            "stock_request.stock_request_one_step_return_form"
        ).get_combined_arch()
        self.assertTrue(
            return_form.xpath(
                "//field[@name='one_step_return_state' and "
                "@statusbar_visible='draft,returned,cancelled']"
            )
        )
        self.assertTrue(
            return_form.xpath(
                "//button[@name='action_view_original_transfer']"
            )
        )
        self.assertTrue(
            return_form.xpath(
                "//field[@name='original_transfer_id']"
            )
        )
        for readonly_line_field in ("product_id", "product_uom_id"):
            self.assertTrue(
                return_form.xpath(
                    "//field[@name='stock_request_ids']/list/field"
                    "[@name='%s' and @readonly='1' and @force_save='1']"
                    % readonly_line_field
                )
            )

    def test_two_step_transfer_requires_active_internal_picking_types(self):
        order = self.request_order.create(
            {
                "source_warehouse_id": self.request_source_warehouse.id,
                "warehouse_id": self.warehouse.id,
                "stock_request_ids": [
                    Command.create(
                        {
                            "product_id": self.product.id,
                            "product_uom_id": self.product.uom_id.id,
                            "product_uom_qty": 2.0,
                        }
                    )
                ],
            }
        )
        self.request_source_warehouse.int_type_id.active = False

        with self.assertRaises(exceptions.UserError):
            order.action_submit()

    def test_submitted_request_can_be_rejected(self):
        source_user = new_test_user(
            self.env,
            login="task06_reject_source_responsible",
            groups="stock.group_stock_user",
            company_ids=[Command.link(self.main_company.id)],
        )
        self.warehouse.stock_request_responsible_user_ids = [
            Command.set(source_user.ids)
        ]
        destination = self.env["stock.warehouse"].create(
            {
                "name": "Task 06 Rejection Destination",
                "code": "T06R",
                "company_id": self.main_company.id,
                "stock_request_responsible_user_ids": [
                    Command.set(self.stock_request_manager.ids)
                ],
            }
        )
        request = self.request_order.with_user(self.stock_request_user).create(
            {
                "source_warehouse_id": self.warehouse.id,
                "warehouse_id": destination.id,
                "stock_request_ids": [
                    Command.create(
                        {
                            "product_id": self.product.id,
                            "product_uom_id": self.product.uom_id.id,
                            "product_uom_qty": 2.0,
                        }
                    )
                ],
            }
        )

        request.with_user(self.stock_request_user).action_submit()
        review_type = self.env.ref(
            "stock_request.mail_activity_type_stock_request_review"
        )
        review_domain = [
            ("res_model", "=", "stock.request.order"),
            ("res_id", "=", request.id),
            ("activity_type_id", "=", review_type.id),
        ]
        self.assertEqual(
            self.env["mail.activity"].sudo().search_count(review_domain), 1
        )
        with self.assertRaises(exceptions.UserError):
            request.with_user(source_user).action_reject()
        with self.assertRaises(exceptions.AccessError):
            request.with_user(self.stock_request_user).write(
                {"rejection_reason": "Requester cannot reject"}
            )
        rejection_reason = "The requested quantity is not available."
        request.with_user(source_user).write(
            {"rejection_reason": rejection_reason}
        )
        request.with_user(source_user).action_reject()

        self.assertEqual(request.state, "rejected")
        self.assertTrue(request.rejected_date)
        self.assertEqual(request.rejected_requests_count, 1)
        self.assertFalse(request.picking_ids)
        self.assertFalse(
            self.env["mail.activity"].sudo().search(review_domain)
        )
        rejection_messages = request.message_ids.filtered(
            lambda message: rejection_reason in str(message.body)
        )
        self.assertTrue(rejection_messages)
        self.assertTrue(
            rejection_messages.filtered(
                lambda message: self.stock_request_user.partner_id
                in message.partner_ids
            )
        )
        form_arch = self.env.ref(
            "stock_request.stock_request_order_form"
        ).get_combined_arch()
        rejection_fields = form_arch.xpath("//field[@name='rejection_reason']")
        self.assertTrue(rejection_fields)
        self.assertIn(
            "is_source_warehouse_responsible",
            rejection_fields[0].get("readonly"),
        )

    def test_operation_actions_and_menu_security(self):
        request_action = self.env.ref("stock_request.stock_request_order_action")
        transfer_action = self.env.ref(
            "stock_request.stock_request_direct_transfer_action"
        )
        transfer_menu = self.env.ref(
            "stock_request.stock_request_direct_transfer_menu"
        )
        warehouse_responsible_group = self.env.ref(
            "stock_request.group_stock_request_warehouse_responsible"
        )

        self.assertIn("default_operation_type", request_action.context)
        self.assertIn("request", request_action.context)
        self.assertIn("default_operation_type", transfer_action.context)
        self.assertIn("transfer", transfer_action.context)
        self.assertIn("operation_type", request_action.domain)
        self.assertIn("operation_type", transfer_action.domain)
        self.assertIn(warehouse_responsible_group, transfer_menu.group_ids)

        context_transfer = self.request_order.with_context(
            default_operation_type="transfer"
        ).create(
            {
                "source_warehouse_id": self.warehouse.id,
                "warehouse_id": self.warehouse.id,
            }
        )
        self.assertEqual(context_transfer.operation_type, "transfer")

        form_arch = self.env.ref(
            "stock_request.stock_request_order_form"
        ).get_combined_arch()
        operation_fields = form_arch.xpath("//field[@name='operation_type']")
        self.assertTrue(operation_fields)
        self.assertTrue(
            all(field.get("invisible") == "1" for field in operation_fields)
        )
        transfer_statusbars = form_arch.xpath(
            "//field[@name='state'][contains(@invisible, \"operation_type != 'transfer'\")]"
        )
        self.assertEqual(len(transfer_statusbars), 1)
        self.assertNotIn(
            "submitted", transfer_statusbars[0].get("statusbar_visible")
        )

    def test_all_internal_users_can_be_selected_as_warehouse_responsible(self):
        responsible_field = self.env["stock.warehouse"]._fields[
            "stock_request_responsible_user_ids"
        ]
        self.assertEqual(responsible_field.domain, [("share", "=", False)])
        selectable_users = self.env["res.users"].sudo().search(
            responsible_field.domain
        )
        self.assertIn(self.env.ref("base.user_admin"), selectable_users)
        self.assertIn(self.stock_request_user, selectable_users)
        self.assertFalse(
            self.stock_request_user.has_group("stock.group_stock_user")
        )

        self.warehouse.stock_request_responsible_user_ids = [
            Command.set(self.stock_request_user.ids)
        ]

        self.assertTrue(
            self.stock_request_user.has_group(
                "stock_request.group_stock_request_warehouse_responsible"
            )
        )
        self.assertTrue(
            self.stock_request_user.has_group("stock.group_stock_user")
        )

    def test_task09_role_hierarchy_and_exact_record_visibility(self):
        source_user = new_test_user(
            self.env,
            login="task09_visibility_source",
            groups="stock.group_stock_user",
            company_ids=[Command.link(self.main_company.id)],
        )
        destination_user = new_test_user(
            self.env,
            login="task09_visibility_destination",
            groups="stock.group_stock_user",
            company_ids=[Command.link(self.main_company.id)],
        )
        unrelated_user = new_test_user(
            self.env,
            login="task09_visibility_unrelated",
            groups="stock_request.group_stock_request_user",
            company_ids=[Command.link(self.main_company.id)],
        )
        self.warehouse.stock_request_responsible_user_ids = [
            Command.set(source_user.ids)
        ]
        destination = self.env["stock.warehouse"].create(
            {
                "name": "Task 09 Visibility Destination",
                "code": "T9V",
                "company_id": self.main_company.id,
                "stock_request_responsible_user_ids": [
                    Command.set(destination_user.ids)
                ],
            }
        )
        order = self.request_order.with_user(self.stock_request_user).create(
            {
                "source_warehouse_id": self.warehouse.id,
                "warehouse_id": destination.id,
                "stock_request_ids": [
                    Command.create(
                        {
                            "product_id": self.product.id,
                            "product_uom_id": self.product.uom_id.id,
                            "product_uom_qty": 1.0,
                        }
                    )
                ],
            }
        )
        order.sudo().message_subscribe(partner_ids=unrelated_user.partner_id.ids)

        responsible_group = self.env.ref(
            "stock_request.group_stock_request_warehouse_responsible"
        )
        self.assertTrue(source_user.has_group("stock.group_stock_user"))
        self.assertIn(responsible_group, source_user.group_ids)
        self.assertIn(responsible_group, destination_user.group_ids)
        self.assertTrue(
            self.stock_request_manager.has_group(
                "stock_request.group_stock_request_warehouse_responsible"
            )
        )
        self.assertTrue(
            self.stock_request_manager.has_group("stock.group_stock_manager")
        )
        self.assertFalse(
            self.env.ref("stock_request.stock_request_followers_rule").active
        )
        self.assertFalse(
            self.env.ref("stock_request.stock_request_order_followers_rule").active
        )
        for user in (
            self.stock_request_user,
            source_user,
            destination_user,
            self.stock_request_manager,
        ):
            self.assertEqual(
                self.request_order.with_user(user).search_count(
                    [("id", "=", order.id)]
                ),
                1,
            )
        self.assertEqual(
            self.request_order.with_user(unrelated_user).search_count(
                [("id", "=", order.id)]
            ),
            0,
        )

    def test_task09_user_cannot_bypass_workflow_permissions(self):
        source_user = new_test_user(
            self.env,
            login="task09_backend_source",
            groups="stock.group_stock_user",
            company_ids=[Command.link(self.main_company.id)],
        )
        self.warehouse.stock_request_responsible_user_ids = [
            Command.set(source_user.ids)
        ]
        destination = self.env["stock.warehouse"].create(
            {
                "name": "Task 09 Backend Destination",
                "code": "T9B",
                "company_id": self.main_company.id,
                "stock_request_responsible_user_ids": [
                    Command.set(self.stock_request_manager.ids)
                ],
            }
        )
        order = self.request_order.with_user(self.stock_request_user).create(
            {
                "source_warehouse_id": self.warehouse.id,
                "warehouse_id": destination.id,
                "stock_request_ids": [
                    Command.create(
                        {
                            "product_id": self.product.id,
                            "product_uom_id": self.product.uom_id.id,
                            "product_uom_qty": 1.0,
                        }
                    )
                ],
            }
        )
        order.with_user(self.stock_request_user).write({"reason": "Draft edit"})
        order.stock_request_ids.with_user(self.stock_request_user).write(
            {"product_uom_qty": 2.0}
        )
        with self.assertRaises(exceptions.AccessError):
            order.with_user(self.stock_request_user).write({"state": "submitted"})
        with self.assertRaises(exceptions.AccessError):
            self.request_order.with_user(self.stock_request_user).create(
                {
                    "operation_type": "transfer",
                    "source_warehouse_id": self.warehouse.id,
                    "warehouse_id": destination.id,
                }
            )

        order.with_user(self.stock_request_user).action_submit()
        with self.assertRaises(exceptions.AccessError):
            order.with_user(self.stock_request_user).write({"reason": "RPC edit"})
        with self.assertRaises(exceptions.AccessError):
            order.stock_request_ids.with_user(self.stock_request_user).write(
                {"product_uom_qty": 3.0}
            )
        with self.assertRaises(exceptions.AccessError):
            order.with_user(self.stock_request_user).action_confirm()
        with self.assertRaises(exceptions.UserError):
            order.with_user(self.stock_request_user).action_cancel()

        order.with_user(self.stock_request_manager).action_cancel()
        self.assertEqual(order.state, "cancelled")
        self.assertTrue(order.cancelled_date)
        self.assertEqual(order.cancelled_requests_count, 1)

    def test_task09_only_manager_manages_warehouse_responsibles(self):
        responsible_user = new_test_user(
            self.env,
            login="task09_manage_responsibles",
            groups="stock.group_stock_user",
            company_ids=[Command.link(self.main_company.id)],
        )
        self.warehouse.stock_request_responsible_user_ids = [
            Command.set(responsible_user.ids)
        ]

        with self.assertRaises(exceptions.AccessError):
            self.warehouse.with_user(responsible_user).write(
                {"stock_request_responsible_user_ids": [Command.clear()]}
            )
        self.warehouse.with_user(self.stock_request_manager).write(
            {"stock_request_responsible_user_ids": [Command.set(responsible_user.ids)]}
        )
        self.assertEqual(
            self.warehouse.stock_request_responsible_user_ids,
            responsible_user,
        )

    def test_task10_operational_menu_domains(self):
        source_user = new_test_user(
            self.env,
            login="task10_queue_source",
            groups="stock_request.group_stock_request_warehouse_responsible",
            company_ids=[Command.link(self.main_company.id)],
        )
        destination_user = new_test_user(
            self.env,
            login="task10_queue_destination",
            groups="stock_request.group_stock_request_warehouse_responsible",
            company_ids=[Command.link(self.main_company.id)],
        )
        self.warehouse.stock_request_responsible_user_ids = [
            Command.set(source_user.ids)
        ]
        destination = self.env["stock.warehouse"].create(
            {
                "name": "Task 10 Queue Destination",
                "code": "T10Q",
                "company_id": self.main_company.id,
                "stock_request_responsible_user_ids": [
                    Command.set(destination_user.ids)
                ],
            }
        )
        order = self.request_order.with_user(self.stock_request_user).create(
            {
                "source_warehouse_id": self.warehouse.id,
                "warehouse_id": destination.id,
                "stock_request_ids": [
                    Command.create(
                        {
                            "product_id": self.product.id,
                            "product_uom_id": self.product.uom_id.id,
                            "product_uom_qty": 2.0,
                        }
                    )
                ],
            }
        )
        order.with_user(self.stock_request_user).action_submit()

        my_requests_action = self.env.ref(
            "stock_request.stock_request_my_requests_action"
        )
        to_review_action = self.env.ref(
            "stock_request.stock_request_to_review_action"
        )
        to_deliver_action = self.env.ref(
            "stock_request.stock_request_to_deliver_action"
        )
        to_receive_action = self.env.ref(
            "stock_request.stock_request_to_receive_action"
        )
        all_operations_action = self.env.ref(
            "stock_request.stock_request_all_operations_action"
        )
        my_domain = safe_eval(
            my_requests_action.domain,
            {"uid": self.stock_request_user.id},
        )
        review_domain = safe_eval(
            to_review_action.domain,
            {"uid": source_user.id},
        )
        deliver_domain = safe_eval(
            to_deliver_action.domain,
            {"uid": source_user.id},
        )
        receive_domain = safe_eval(
            to_receive_action.domain,
            {"uid": destination_user.id},
        )
        all_domain = safe_eval(all_operations_action.domain)

        self.assertIn(
            order,
            self.request_order.with_user(self.stock_request_user).search(my_domain),
        )
        self.assertIn(
            order,
            self.request_order.with_user(source_user).search(review_domain),
        )
        self.assertNotIn(
            order,
            self.request_order.with_user(source_user).search(deliver_domain),
        )
        self.assertNotIn(
            order,
            self.request_order.with_user(destination_user).search(receive_domain),
        )
        self.assertIn(
            order,
            self.request_order.with_user(self.stock_request_manager).search(
                all_domain
            ),
        )

        responsible_group = self.env.ref(
            "stock_request.group_stock_request_warehouse_responsible"
        )
        manager_group = self.env.ref("stock_request.group_stock_request_manager")
        user_group = self.env.ref("stock_request.group_stock_request_user")
        self.assertIn(
            responsible_group,
            self.env.ref("stock_request.stock_request_to_review_menu").group_ids,
        )
        self.assertIn(
            responsible_group,
            self.env.ref("stock_request.stock_request_to_deliver_menu").group_ids,
        )
        self.assertIn(
            responsible_group,
            self.env.ref("stock_request.stock_request_to_receive_menu").group_ids,
        )
        self.assertIn(
            manager_group,
            self.env.ref("stock_request.stock_request_all_requests_menu").group_ids,
        )
        self.assertIn(
            user_group,
            self.env.ref("stock_request.stock_request_order_menu").group_ids,
        )
        root_menu = self.env.ref("stock_request.menu_stock_request_root")
        self.assertEqual(
            root_menu.with_context(lang="en_US").name,
            "Stock Transfer",
        )
        self.assertEqual(
            root_menu.web_icon,
            "stock_request,static/description/stock_transfer_icon.png",
        )
        module = self.env["ir.module.module"].search(
            [("name", "=", "stock_request")], limit=1
        )
        self.assertEqual(
            module.with_context(lang="en_US").shortdesc,
            "Stock Transfer",
        )
        self.assertEqual(
            module.with_context(lang="en_US").summary,
            "Warehouse requests and internal transfers",
        )
        self.assertEqual(
            module.icon,
            "/stock_request/static/description/stock_transfer_icon.png",
        )

    def test_task15_warehouse_setup_status_security_and_menu(self):
        responsible_user = new_test_user(
            self.env,
            login="task15_warehouse_responsible",
            groups="stock.group_stock_user",
            company_ids=[Command.link(self.main_company.id)],
        )
        destination = self.env["stock.warehouse"].create(
            {
                "name": "Task 15 Destination",
                "code": "T15D",
                "company_id": self.main_company.id,
                "stock_request_responsible_user_ids": [
                    Command.set(responsible_user.ids)
                ],
            }
        )

        self.assertEqual(destination.stock_request_transit_mode, "odoo_default")
        self.assertEqual(
            destination.stock_request_configuration_status, "missing_source"
        )
        destination.with_user(self.stock_request_manager).write(
            {
                "default_stock_request_source_warehouse_id": (
                    self.request_source_warehouse.id
                )
            }
        )
        self.assertEqual(destination.stock_request_configuration_status, "ready")

        setup_fields = [
            "stock_request_responsible_user_ids",
            "default_stock_request_source_warehouse_id",
            "stock_request_receiving_transit_location_id",
            "stock_request_transit_mode",
            "stock_request_configuration_status",
        ]
        self.assertTrue(destination.with_user(responsible_user).read(setup_fields))
        with self.assertRaises(exceptions.AccessError):
            destination.with_user(responsible_user).write(
                {
                    "default_stock_request_source_warehouse_id": self.warehouse.id
                }
            )
        with self.assertRaises(exceptions.AccessError):
            destination.with_user(
                responsible_user
            ).action_create_stock_request_receiving_transit_location()

        self.main_company.internal_transit_location_id = False
        self.assertEqual(destination.stock_request_transit_mode, "missing")
        self.assertEqual(
            destination.stock_request_configuration_status, "missing_transit"
        )
        destination.with_user(
            self.stock_request_manager
        ).action_create_stock_request_receiving_transit_location()
        transit_location = destination.stock_request_receiving_transit_location_id
        self.assertEqual(transit_location.name, "[T15D] Receiving Transit")
        self.assertEqual(transit_location.usage, "transit")
        self.assertEqual(transit_location.company_id, self.main_company)
        self.assertEqual(destination.stock_request_transit_mode, "custom")
        self.assertEqual(destination.stock_request_configuration_status, "ready")
        destination.with_user(
            self.stock_request_manager
        ).action_create_stock_request_receiving_transit_location()
        self.assertEqual(
            destination.stock_request_receiving_transit_location_id,
            transit_location,
        )

        setup_action = self.env.ref(
            "stock_request.stock_request_warehouse_setup_action"
        )
        setup_menu = self.env.ref(
            "stock_request.stock_request_warehouse_setup_menu"
        )
        manager_group = self.env.ref("stock_request.group_stock_request_manager")
        self.assertEqual(setup_action.res_model, "stock.warehouse")
        self.assertEqual(setup_menu.action, setup_action)
        self.assertIn(manager_group, setup_menu.group_ids)
        setup_list_arch = self.env.ref(
            "stock_request.stock_request_warehouse_setup_list"
        ).get_combined_arch()
        setup_form_arch = self.env.ref(
            "stock_request.stock_request_warehouse_setup_form"
        ).get_combined_arch()
        for arch in (setup_list_arch, setup_form_arch):
            for field_name in (
                "default_stock_request_source_warehouse_id",
                "stock_request_receiving_transit_location_id",
            ):
                field_nodes = arch.xpath("//field[@name='%s']" % field_name)
                self.assertTrue(field_nodes)
                self.assertTrue(
                    all(
                        "no_create" in node.get("options", "")
                        for node in field_nodes
                    )
                )
        with self.assertRaisesRegex(
            exceptions.ValidationError,
            "Warehouse Name is required",
        ):
            self.env["stock.warehouse"].with_user(
                self.stock_request_manager
            ).create(
                {
                    "code": "NONAME",
                    "company_id": self.main_company.id,
                }
            )
        self.assertFalse(
            self.stock_request_user.has_group(
                "stock_request.group_stock_request_warehouse_responsible"
            )
        )

    def test_task15_defaults_and_warehouse_transit_are_operational(self):
        destination = self.env["stock.warehouse"].create(
            {
                "name": "Task 15 Operational Destination",
                "code": "T15O",
                "company_id": self.main_company.id,
                "stock_request_responsible_user_ids": [
                    Command.set(self.stock_request_manager.ids)
                ],
                "default_stock_request_source_warehouse_id": (
                    self.request_source_warehouse.id
                ),
            }
        )
        transit_location = self.env["stock.location"].create(
            {
                "name": "Task 15 Custom Transit",
                "usage": "transit",
                "company_id": self.main_company.id,
            }
        )
        destination.stock_request_receiving_transit_location_id = transit_location
        self.stock_request_user.default_stock_request_warehouse_id = destination

        default_order = self.request_order.with_user(
            self.stock_request_user
        ).create({})
        self.assertEqual(default_order.warehouse_id, destination)
        self.assertEqual(
            default_order.source_warehouse_id, self.request_source_warehouse
        )

        transfer = self.request_order.with_user(self.stock_request_manager).create(
            {
                "operation_type": "transfer",
                "source_warehouse_id": self.request_source_warehouse.id,
                "warehouse_id": destination.id,
                "stock_request_ids": [
                    Command.create(
                        {
                            "product_id": self.product.id,
                            "product_uom_id": self.product.uom_id.id,
                            "product_uom_qty": 2.0,
                        }
                    )
                ],
            }
        )
        transfer.with_user(self.stock_request_manager).action_confirm()
        delivery = transfer.stock_request_operation_picking_ids.filtered(
            lambda picking: picking.stock_request_operation == "delivery"
        )
        receipt = transfer.stock_request_operation_picking_ids.filtered(
            lambda picking: picking.stock_request_operation == "receipt"
        )
        self.assertEqual(delivery.location_dest_id, transit_location)
        self.assertEqual(receipt.location_id, transit_location)

    def test_task16_receiving_transit_is_selected_and_locked(self):
        source = self.env["stock.warehouse"].create(
            {
                "name": "Task 16 Source",
                "code": "T16S",
                "company_id": self.main_company.id,
                "stock_request_responsible_user_ids": [
                    Command.set(self.stock_request_manager.ids)
                ],
            }
        )
        destination = self.env["stock.warehouse"].create(
            {
                "name": "Task 16 Destination",
                "code": "T16D",
                "company_id": self.main_company.id,
                "stock_request_responsible_user_ids": [
                    Command.set(self.stock_request_manager.ids)
                ],
            }
        )
        source_transit, destination_transit, replacement_transit = self.env[
            "stock.location"
        ].create(
            [
                {
                    "name": "Task 16 Source Transit",
                    "usage": "transit",
                    "company_id": self.main_company.id,
                },
                {
                    "name": "Task 16 Destination Transit",
                    "usage": "transit",
                    "company_id": self.main_company.id,
                },
                {
                    "name": "Task 16 Replacement Transit",
                    "usage": "transit",
                    "company_id": self.main_company.id,
                },
            ]
        )
        source.stock_request_receiving_transit_location_id = source_transit
        destination.stock_request_receiving_transit_location_id = (
            destination_transit
        )
        line_values = {
            "product_id": self.product.id,
            "product_uom_id": self.product.uom_id.id,
            "product_uom_qty": 2.0,
        }

        transfer = self.request_order.with_user(self.stock_request_manager).create(
            {
                "operation_type": "transfer",
                "source_warehouse_id": source.id,
                "warehouse_id": destination.id,
                "stock_request_ids": [Command.create(line_values)],
            }
        )
        self.assertFalse(transfer.transit_location_id)
        transfer.with_user(self.stock_request_manager).action_confirm()
        self.assertEqual(transfer.transit_location_id, destination_transit)
        delivery = transfer.stock_request_operation_picking_ids.filtered(
            lambda picking: picking.stock_request_operation == "delivery"
        )
        receipt = transfer.stock_request_operation_picking_ids.filtered(
            lambda picking: picking.stock_request_operation == "receipt"
        )
        self.assertEqual(delivery.location_dest_id, destination_transit)
        self.assertEqual(receipt.location_id, destination_transit)

        request_order = self.request_order.with_user(
            self.stock_request_user
        ).create(
            {
                "source_warehouse_id": source.id,
                "warehouse_id": destination.id,
                "stock_request_ids": [Command.create(line_values.copy())],
            }
        )
        request_order.with_user(self.stock_request_user).action_submit()
        request_order.with_user(self.stock_request_manager).action_confirm()
        self.assertEqual(request_order.transit_location_id, destination_transit)
        self.assertEqual(
            request_order.transit_location_id,
            transfer.transit_location_id,
        )
        request_deliveries = request_order.stock_request_operation_picking_ids.filtered(
            lambda picking: picking.stock_request_operation == "delivery"
        )
        self.assertTrue(
            all(
                picking.location_dest_id == destination_transit
                for picking in request_deliveries
            )
        )

        destination.stock_request_receiving_transit_location_id = (
            replacement_transit
        )
        transfer.invalidate_recordset()
        self.assertEqual(transfer.transit_location_id, destination_transit)
        self.assertEqual(delivery.location_dest_id, destination_transit)
        self.assertEqual(receipt.location_id, destination_transit)

        reverse_transfer = self.request_order.with_user(
            self.stock_request_manager
        ).create(
            {
                "operation_type": "transfer",
                "source_warehouse_id": destination.id,
                "warehouse_id": source.id,
                "stock_request_ids": [Command.create(line_values)],
            }
        )
        reverse_transfer.with_user(self.stock_request_manager).action_confirm()
        self.assertEqual(reverse_transfer.transit_location_id, source_transit)

        source.stock_request_receiving_transit_location_id = False
        fallback_transfer = self.request_order.with_user(
            self.stock_request_manager
        ).create(
            {
                "operation_type": "transfer",
                "source_warehouse_id": destination.id,
                "warehouse_id": source.id,
                "stock_request_ids": [Command.create(line_values)],
            }
        )
        fallback_transfer.with_user(self.stock_request_manager).action_confirm()
        self.assertEqual(
            fallback_transfer.transit_location_id,
            self.main_company.internal_transit_location_id,
        )

        transit_field = self.request_order._fields["transit_location_id"]
        self.assertTrue(transit_field.readonly)
        self.assertFalse(transit_field.copy)
        self.assertTrue(transit_field.check_company)
        with self.assertRaises(exceptions.AccessError):
            fallback_transfer.with_user(self.stock_request_manager).write(
                {"transit_location_id": replacement_transit.id}
            )

        # Simulate an operation created before the stored field existed. Its
        # picking locations remain authoritative and are never rewritten.
        transfer.sudo().transit_location_id = False
        self.assertFalse(transfer.transit_location_id)
        self.assertEqual(
            transfer._get_effective_transit_location(),
            destination_transit,
        )
        self.assertEqual(delivery.location_dest_id, destination_transit)
        self.assertEqual(receipt.location_id, destination_transit)

    def test_task17_warehouse_defaults_and_early_validation(self):
        destination = self.env["stock.warehouse"].create(
            {
                "name": "Task 17 Destination",
                "code": "T17D",
                "company_id": self.main_company.id,
                "stock_request_responsible_user_ids": [
                    Command.set(self.stock_request_manager.ids)
                ],
            }
        )
        self.stock_request_user.default_stock_request_warehouse_id = destination
        line_values = {
            "product_id": self.product.id,
            "product_uom_id": self.product.uom_id.id,
            "product_uom_qty": 1.0,
        }

        request_without_source = self.request_order.with_user(
            self.stock_request_user
        ).create({"stock_request_ids": [Command.create(line_values)]})
        self.assertEqual(request_without_source.warehouse_id, destination)
        self.assertFalse(request_without_source.source_warehouse_id)
        with self.assertRaisesRegex(exceptions.UserError, "Source Warehouse"):
            request_without_source.with_user(
                self.stock_request_user
            ).action_submit()

        destination.default_stock_request_source_warehouse_id = (
            self.request_source_warehouse
        )
        configured_request = self.request_order.with_user(
            self.stock_request_user
        ).create({"stock_request_ids": [Command.create(line_values)]})
        self.assertEqual(
            configured_request.source_warehouse_id,
            self.request_source_warehouse,
        )

        destination.stock_request_responsible_user_ids = [Command.clear()]
        with self.assertRaisesRegex(
            exceptions.UserError,
            "No responsible users are configured",
        ):
            configured_request.with_user(self.stock_request_user).action_submit()

        destination.stock_request_responsible_user_ids = [
            Command.set(self.stock_request_manager.ids)
        ]
        default_transit = self.main_company.internal_transit_location_id
        self.main_company.internal_transit_location_id = False
        with self.assertRaisesRegex(
            exceptions.UserError,
            "No transit location is configured for the receiving warehouse",
        ):
            configured_request.with_user(self.stock_request_user).action_submit()

        direct_transfer = self.request_order.with_user(
            self.stock_request_manager
        ).create(
            {
                "operation_type": "transfer",
                "source_warehouse_id": self.request_source_warehouse.id,
                "warehouse_id": destination.id,
                "stock_request_ids": [Command.create(line_values.copy())],
            }
        )
        with self.assertRaisesRegex(
            exceptions.UserError,
            "No transit location is configured for the receiving warehouse",
        ):
            direct_transfer.with_user(self.stock_request_manager).action_confirm()

        self.main_company.internal_transit_location_id = default_transit
        configured_request.with_user(self.stock_request_user).action_submit()
        self.assertEqual(configured_request.state, "submitted")
        self.assertFalse(configured_request.transit_location_id)

        self.main_company.internal_transit_location_id = False
        with self.assertRaisesRegex(
            exceptions.UserError,
            "No transit location is configured for the receiving warehouse",
        ):
            configured_request.with_user(self.stock_request_manager).action_confirm()
        self.main_company.internal_transit_location_id = default_transit

    def test_task14_template_security_company_and_direct_transfer(self):
        template_model = self.env["stock.request.template"]
        destination = self.env["stock.warehouse"].create(
            {
                "name": "Task 14 Destination",
                "code": "T14D",
                "company_id": self.main_company.id,
            }
        )
        template = template_model.with_user(self.stock_request_manager).create(
            {
                "name": "Task 14 Weekly Replenishment",
                "priority": "1",
                "notes": "Weekly replenishment",
                "line_ids": [
                    Command.create(
                        {
                            "product_id": self.product.id,
                            "product_uom_qty": 4.0,
                        }
                    )
                ],
            }
        )

        self.assertIn(
            template,
            template_model.with_user(self.stock_request_user).search([]),
        )
        self.assertTrue(
            template.line_ids.with_user(self.stock_request_user).read(
                ["product_id", "product_uom_qty"]
            )
        )
        with self.assertRaises(exceptions.AccessError):
            template_model.with_user(self.stock_request_user).create(
                {
                    "name": "Forbidden Template",
                }
            )
        with self.assertRaises(exceptions.AccessError):
            template.with_user(self.stock_request_user).write(
                {"name": "Forbidden Edit"}
            )
        with self.assertRaises(exceptions.ValidationError):
            template.with_user(self.stock_request_manager).write(
                {
                    "line_ids": [
                        Command.create(
                            {
                                "product_id": self.product.id,
                                "product_uom_qty": 2.0,
                            }
                        )
                    ]
                }
            )

        company_2_template = template_model.with_company(self.company_2).create(
            {
                "name": "Other Company Template",
                "company_id": self.company_2.id,
            }
        )
        main_company_templates = template_model.with_user(
            self.stock_request_user
        ).with_context(allowed_company_ids=[self.main_company.id])
        self.assertIn(template, main_company_templates.search([]))
        self.assertNotIn(company_2_template, main_company_templates.search([]))

        source_user = new_test_user(
            self.env,
            login="task14_source_responsible",
            groups="stock.group_stock_user",
            company_ids=[Command.link(self.main_company.id)],
        )
        self.warehouse.stock_request_responsible_user_ids = [
            Command.set(source_user.ids)
        ]
        direct_transfer = self.request_order.with_user(source_user).create(
            {
                "operation_type": "transfer",
                "source_warehouse_id": self.warehouse.id,
                "warehouse_id": destination.id,
                "template_id": template.id,
            }
        )
        direct_transfer.with_user(source_user).action_load_template()
        self.assertEqual(direct_transfer.state, "draft")
        self.assertFalse(direct_transfer.picking_ids)
        self.assertEqual(direct_transfer.source_warehouse_id, self.warehouse)
        self.assertEqual(direct_transfer.warehouse_id, destination)
        self.assertEqual(len(direct_transfer.stock_request_ids), 1)
        self.assertEqual(
            direct_transfer.stock_request_ids.product_uom_qty,
            4.0,
        )
        direct_transfer.stock_request_ids.with_user(source_user).write(
            {"product_uom_qty": 6.0}
        )
        self.assertEqual(
            direct_transfer.stock_request_ids.product_uom_qty,
            6.0,
        )

        template_action = self.env.ref(
            "stock_request.stock_request_template_action"
        )
        self.assertEqual(template_action.res_model, "stock.request.template")
        template_form = self.env.ref(
            "stock_request.stock_request_template_form_v14"
        ).get_combined_arch()
        template_list = self.env.ref(
            "stock_request.stock_request_template_tree_v14"
        ).get_combined_arch()
        self.assertTrue(template_form.xpath("//field[@name='line_ids']"))
        for warehouse_field in (
            "source_warehouse_id",
            "destination_warehouse_id",
        ):
            self.assertFalse(
                template_form.xpath(
                    "//field[@name='%s']" % warehouse_field
                )
            )
            self.assertFalse(
                template_list.xpath(
                    "//field[@name='%s']" % warehouse_field
                )
            )
        template_uom = template_form.xpath(
            "//field[@name='line_ids']//field[@name='product_uom_id']"
        )
        self.assertEqual(template_uom[0].get("string"), "UOM")
        self.assertFalse(template_model._fields["source_warehouse_id"].required)
        self.assertFalse(
            template_model._fields["destination_warehouse_id"].required
        )
        self.assertEqual(
            self.stock_request._fields["product_uom_id"].string,
            "UOM",
        )
        order_form = self.env.ref(
            "stock_request.stock_request_order_form"
        ).get_combined_arch()
        self.assertTrue(order_form.xpath("//button[@name='action_load_template']"))
        notebook_pages = order_form.xpath("//notebook/page")
        page_names = [page.get("name") for page in notebook_pages]
        self.assertEqual(
            page_names.index("transfer_execution"),
            page_names.index("items") + 1,
        )
        self.assertTrue(
            order_form.xpath("//page[@name='items']//field[@name='reason']")
        )
        self.assertTrue(
            order_form.xpath(
                "//page[@name='transfer_execution']"
                "//field[@name='delivered_by_id']"
            )
        )
        self.assertTrue(
            order_form.xpath(
                "//page[@name='transfer_execution']"
                "//field[@name='received_by_id']"
            )
        )
        self.assertIn(
            self.env.ref("stock_request.group_stock_request_manager"),
            self.env.ref("stock_request.stock_request_template_menu").group_ids,
        )

    def test_task11_partial_quantities_backorders_and_smart_buttons(self):
        alternate_manager = new_test_user(
            self.env,
            login="task12_alternate_manager",
            groups="stock_request.group_stock_request_manager",
            company_ids=[Command.link(self.main_company.id)],
        )
        destination = self.env["stock.warehouse"].create(
            {
                "name": "Task 11 Destination",
                "code": "T11D",
                "company_id": self.main_company.id,
                "stock_request_responsible_user_ids": [
                    Command.set(alternate_manager.ids)
                ],
            }
        )
        self._create_stock_quant(self.product, self.warehouse.lot_stock_id, 5)
        order = self.request_order.with_user(self.stock_request_user).create(
            {
                "source_warehouse_id": self.warehouse.id,
                "warehouse_id": destination.id,
                "stock_request_ids": [
                    Command.create(
                        {
                            "product_id": self.product.id,
                            "product_uom_id": self.product.uom_id.id,
                            "product_uom_qty": 5.0,
                        }
                    )
                ],
            }
        )
        order.with_user(self.stock_request_user).action_submit()
        order.with_user(self.stock_request_manager).action_confirm()
        receipt_activity_type = self.env.ref(
            "stock_request.mail_activity_type_stock_request_receipt"
        )
        receipt_activity_domain = [
            ("res_model", "=", "stock.request.order"),
            ("res_id", "=", order.id),
            ("activity_type_id", "=", receipt_activity_type.id),
        ]
        receipt_activity_model = self.env["mail.activity"].sudo()
        line = order.stock_request_ids
        delivery = order.stock_request_operation_picking_ids.filtered(
            lambda picking: picking.stock_request_operation == "delivery"
        )

        delivery.with_user(self.stock_request_manager).action_assign()
        delivery.move_line_ids.quantity = 2.0
        delivery.move_line_ids.picked = True
        delivery.with_user(self.stock_request_manager)._action_done()
        order.invalidate_recordset()
        line.invalidate_recordset()

        self.assertEqual(order.state, "in_transit")
        self.assertEqual(order.first_delivery_date, delivery.date_done)
        self.assertGreaterEqual(order.preparation_time, 0.0)
        self.assertEqual(line.delivered_qty, 2.0)
        self.assertEqual(line.received_qty, 0.0)
        self.assertEqual(line.in_transit_qty, 2.0)
        self.assertEqual(
            line.in_transit_qty,
            line.delivered_qty - line.received_qty,
        )
        self.assertEqual(line.remaining_delivery_qty, 3.0)
        self.assertEqual(line.remaining_receipt_qty, 2.0)
        self.assertEqual(line.fulfillment_status, "pending")
        self.assertEqual(order.next_action, "deliver_remaining")
        self.assertEqual(order.delivery_picking_count, 2)
        self.assertEqual(order.receipt_picking_count, 1)
        self.assertTrue(order.has_pending_delivery)
        self.assertTrue(order.has_pending_receipt)
        self.assertEqual(
            receipt_activity_model.search_count(receipt_activity_domain),
            1,
        )

        delivery_backorder = order.stock_request_operation_picking_ids.filtered(
            lambda picking: picking.stock_request_operation == "delivery"
            and picking.state != "done"
        )
        self.assertEqual(
            delivery_backorder.location_dest_id,
            order.transit_location_id,
        )
        self.assertEqual(
            order.with_user(self.stock_request_manager).action_deliver_products()[
                "res_id"
            ],
            delivery_backorder.id,
        )
        delivery_backorder.with_user(alternate_manager).action_assign()
        delivery_backorder.move_line_ids.quantity = 3.0
        delivery_backorder.move_line_ids.picked = True
        delivery_backorder.with_user(alternate_manager)._action_done()
        line.invalidate_recordset()
        self.assertEqual(line.delivered_qty, 5.0)
        self.assertEqual(line.in_transit_qty, 5.0)
        self.assertEqual(line.remaining_delivery_qty, 0.0)
        self.assertEqual(line.remaining_receipt_qty, 5.0)
        self.assertEqual(line.fulfillment_status, "pending")
        self.assertEqual(order.next_action, "receive_products")
        self.assertEqual(order.state, "in_transit")
        self.assertEqual(
            receipt_activity_model.search_count(receipt_activity_domain),
            1,
        )

        receipt = order.stock_request_operation_picking_ids.filtered(
            lambda picking: picking.stock_request_operation == "receipt"
            and picking.state != "done"
        )
        self.assertTrue(order.has_pending_receipt)
        self.assertEqual(
            order.with_user(self.stock_request_manager).action_receive_products()[
                "res_id"
            ],
            receipt.id,
        )
        receipt.with_user(self.stock_request_manager).action_assign()
        receipt.move_line_ids.quantity = 2.0
        receipt.move_line_ids.picked = True
        receipt.with_user(self.stock_request_manager)._action_done()
        order.invalidate_recordset()
        line.invalidate_recordset()

        self.assertEqual(line.received_qty, 2.0)
        self.assertEqual(line.in_transit_qty, 3.0)
        self.assertFalse(order.receipt_date)
        self.assertEqual(line.remaining_receipt_qty, 3.0)
        self.assertEqual(line.fulfillment_status, "partially_received")
        self.assertEqual(order.next_action, "receive_remaining")
        self.assertEqual(order.state, "in_transit")
        self.assertEqual(order.receipt_picking_count, 2)
        self.assertEqual(
            receipt_activity_model.search_count(receipt_activity_domain),
            1,
        )
        self.assertFalse(
            order.message_ids.filtered(
                lambda message: "has been received" in str(message.body)
            )
        )

        receipt_backorder = order.stock_request_operation_picking_ids.filtered(
            lambda picking: picking.stock_request_operation == "receipt"
            and picking.state != "done"
        )
        self.assertEqual(
            receipt_backorder.location_id,
            order.transit_location_id,
        )
        receipt_backorder.with_user(alternate_manager).action_assign()
        receipt_backorder.move_line_ids.quantity = 3.0
        receipt_backorder.move_line_ids.picked = True
        receipt_backorder.with_user(alternate_manager)._action_done()
        order.invalidate_recordset()
        line.invalidate_recordset()

        self.assertEqual(line.delivered_qty, 5.0)
        self.assertEqual(line.received_qty, 5.0)
        self.assertEqual(line.in_transit_qty, 0.0)
        self.assertEqual(line.remaining_delivery_qty, 0.0)
        self.assertEqual(line.remaining_receipt_qty, 0.0)
        self.assertEqual(line.fulfillment_status, "fully_received")
        self.assertEqual(order.next_action, "no_action")
        self.assertEqual(order.state, "done")
        self.assertEqual(order.requests_count, 1)
        self.assertEqual(order.completed_requests_count, 1)
        self.assertGreaterEqual(order.transit_time, 0.0)
        self.assertGreaterEqual(order.total_cycle_time, 0.0)
        self.assertFalse(order.has_pending_delivery)
        self.assertFalse(order.has_pending_receipt)
        self.assertFalse(
            receipt_activity_model.search(receipt_activity_domain)
        )
        self.assertTrue(
            order.message_ids.filtered(
                lambda message: "has been received" in str(message.body)
            )
        )

        self.assertEqual(order.delivered_by_id, alternate_manager)
        self.assertEqual(order.delivery_date, delivery_backorder.date_done)
        self.assertEqual(order.received_by_id, alternate_manager)
        self.assertEqual(order.receipt_date, receipt_backorder.date_done)
        completed_operations = order.stock_request_operation_picking_ids.filtered(
            lambda picking: picking.state == "done"
        )
        self.assertEqual(len(completed_operations), 4)
        for picking in completed_operations:
            expected_user = (
                alternate_manager
                if picking in (delivery_backorder | receipt_backorder)
                else self.stock_request_manager
            )
            self.assertEqual(picking.stock_request_validated_by_id, expected_user)
            self.assertEqual(picking.stock_request_validation_date, picking.date_done)

        audit_chatter = " ".join(order.message_ids.mapped("body"))
        for picking in completed_operations:
            self.assertIn(picking.display_name, audit_chatter)
        self.assertIn(self.stock_request_manager.display_name, audit_chatter)
        self.assertIn(alternate_manager.display_name, audit_chatter)

        with self.assertRaises(exceptions.AccessError):
            order.with_user(self.stock_request_manager).write(
                {"delivered_by_id": self.stock_request_manager.id}
            )
        with self.assertRaises(exceptions.AccessError):
            delivery_backorder.with_user(self.stock_request_manager).write(
                {"stock_request_validated_by_id": self.stock_request_manager.id}
            )

        delivery_action = order.action_view_deliveries()
        receipt_action = order.action_view_receipts()
        self.assertEqual(
            set(self.env["stock.picking"].search(delivery_action["domain"]).ids),
            set(
                order.stock_request_operation_picking_ids.filtered(
                    lambda picking: picking.stock_request_operation == "delivery"
                ).ids
            ),
        )
        self.assertEqual(
            set(self.env["stock.picking"].search(receipt_action["domain"]).ids),
            set(
                order.stock_request_operation_picking_ids.filtered(
                    lambda picking: picking.stock_request_operation == "receipt"
                ).ids
            ),
        )

        form_arch = self.env.ref(
            "stock_request.stock_request_order_form"
        ).get_combined_arch()
        self.assertTrue(form_arch.xpath("//button[@name='action_view_deliveries']"))
        self.assertTrue(form_arch.xpath("//button[@name='action_view_receipts']"))
        for field_name in (
            "delivered_qty",
            "received_qty",
            "remaining_delivery_qty",
            "remaining_receipt_qty",
        ):
            self.assertTrue(
                form_arch.xpath(
                    "//field[@name='stock_request_ids']//field[@name='%s']"
                    % field_name
                )
            )
        for field_name in (
            "delivered_by_id",
            "delivery_date",
            "received_by_id",
            "receipt_date",
        ):
            audit_fields = form_arch.xpath("//field[@name='%s']" % field_name)
            self.assertTrue(audit_fields)
            self.assertEqual(audit_fields[0].get("readonly"), "1")

        picking_arch = self.env.ref(
            "stock_request.view_picking_form"
        ).get_combined_arch()
        for field_name in (
            "stock_request_validated_by_id",
            "stock_request_validation_date",
        ):
            audit_fields = picking_arch.xpath("//field[@name='%s']" % field_name)
            self.assertTrue(audit_fields)
            self.assertEqual(audit_fields[0].get("readonly"), "1")

    def test_task11_cancelled_remainders_recompute_progress(self):
        destination = self.env["stock.warehouse"].create(
            {
                "name": "Task 11 Cancellation Destination",
                "code": "T11C",
                "company_id": self.main_company.id,
                "stock_request_responsible_user_ids": [
                    Command.set(self.stock_request_manager.ids)
                ],
            }
        )
        self._create_stock_quant(self.product, self.warehouse.lot_stock_id, 10)
        order = self.request_order.with_user(self.stock_request_user).create(
            {
                "source_warehouse_id": self.warehouse.id,
                "warehouse_id": destination.id,
                "stock_request_ids": [
                    Command.create(
                        {
                            "product_id": self.product.id,
                            "product_uom_id": self.product.uom_id.id,
                            "product_uom_qty": 10.0,
                        }
                    )
                ],
            }
        )
        order.with_user(self.stock_request_user).action_submit()
        order.with_user(self.stock_request_manager).action_confirm()
        line = order.stock_request_ids
        delivery = order.stock_request_operation_picking_ids.filtered(
            lambda picking: picking.stock_request_operation == "delivery"
        )
        delivery.with_user(self.stock_request_manager).action_assign()
        delivery.move_line_ids.quantity = 8.0
        delivery.move_line_ids.picked = True
        delivery_action = delivery.with_user(
            self.stock_request_manager
        ).button_validate()
        self.assertEqual(
            delivery_action.get("res_model"),
            "stock.backorder.confirmation",
        )
        delivery_wizard = Form(
            self.env["stock.backorder.confirmation"]
            .with_user(self.stock_request_manager)
            .with_context(delivery_action["context"])
        ).save()
        delivery_wizard.process_cancel_backorder()
        line.invalidate_recordset()

        self.assertEqual(line.delivered_qty, 8.0)
        self.assertEqual(line.cancelled_delivery_qty, 2.0)
        self.assertEqual(line.remaining_delivery_qty, 0.0)
        self.assertEqual(line.remaining_receipt_qty, 8.0)
        self.assertEqual(order.state, "in_transit")

        receipt = order.stock_request_operation_picking_ids.filtered(
            lambda picking: picking.stock_request_operation == "receipt"
        )
        receipt.with_user(self.stock_request_manager).action_assign()
        receipt.move_line_ids.quantity = 8.0
        receipt.move_line_ids.picked = True
        receipt_action = receipt.with_user(self.stock_request_manager).button_validate()
        self.assertEqual(
            receipt_action.get("res_model"),
            "stock.backorder.confirmation",
        )
        receipt_wizard = Form(
            self.env["stock.backorder.confirmation"]
            .with_user(self.stock_request_manager)
            .with_context(receipt_action["context"])
        ).save()
        receipt_wizard.process_cancel_backorder()
        order.invalidate_recordset()
        line.invalidate_recordset()

        self.assertEqual(line.received_qty, 8.0)
        self.assertEqual(line.remaining_receipt_qty, 0.0)
        self.assertEqual(order.state, "done")
        self.assertIn(
            "No Backorder / Cancel Remaining",
            " ".join(order.message_ids.mapped("body")),
        )

    def test_task3_receive_delivered_portion_before_delivery_backorder(self):
        destination = self.env["stock.warehouse"].create(
            {
                "name": "Task 3 Partial Destination",
                "code": "T3PD",
                "company_id": self.main_company.id,
                "stock_request_responsible_user_ids": [
                    Command.set(self.stock_request_manager.ids)
                ],
            }
        )
        self._create_stock_quant(self.product, self.warehouse.lot_stock_id, 10.0)
        order = self.request_order.with_user(self.stock_request_user).create(
            {
                "source_warehouse_id": self.warehouse.id,
                "warehouse_id": destination.id,
                "stock_request_ids": [
                    Command.create(
                        {
                            "product_id": self.product.id,
                            "product_uom_id": self.product.uom_id.id,
                            "product_uom_qty": 10.0,
                        }
                    )
                ],
            }
        )
        order.with_user(self.stock_request_user).action_submit()
        order.with_user(self.stock_request_manager).action_confirm()
        line = order.stock_request_ids
        delivery = order.stock_request_operation_picking_ids.filtered(
            lambda picking: picking.stock_request_operation == "delivery"
        )

        delivery.with_user(self.stock_request_manager).action_assign()
        delivery.move_line_ids.quantity = 8.0
        delivery.move_line_ids.picked = True
        delivery_action = delivery.with_user(
            self.stock_request_manager
        ).button_validate()
        self.assertEqual(
            delivery_action.get("res_model"),
            "stock.backorder.confirmation",
        )
        Form(
            self.env["stock.backorder.confirmation"]
            .with_user(self.stock_request_manager)
            .with_context(delivery_action["context"])
        ).save().process()

        order.invalidate_recordset()
        line.invalidate_recordset()
        self.assertEqual(line.delivered_qty, 8.0)
        self.assertEqual(line.remaining_delivery_qty, 2.0)
        self.assertEqual(line.remaining_receipt_qty, 8.0)
        self.assertTrue(order.has_pending_delivery)
        self.assertTrue(order.has_pending_receipt)
        receive_action = order.with_user(
            self.stock_request_manager
        ).action_receive_products()
        receipt = self.env["stock.picking"].browse(receive_action["res_id"])
        receipt.with_user(self.stock_request_manager).action_assign()
        receipt.move_line_ids.quantity = 10.0
        receipt.move_line_ids.picked = True
        with self.assertRaisesRegex(exceptions.UserError, "exceeds"):
            receipt.with_user(self.stock_request_manager).button_validate()
        line.invalidate_recordset()
        self.assertEqual(line.received_qty, 0.0)
        receipt.move_line_ids.quantity = 8.0
        receipt.move_line_ids.picked = True
        receipt_action = receipt.with_user(
            self.stock_request_manager
        ).button_validate()
        self.assertEqual(
            receipt_action.get("res_model"),
            "stock.backorder.confirmation",
        )
        Form(
            self.env["stock.backorder.confirmation"]
            .with_user(self.stock_request_manager)
            .with_context(receipt_action["context"])
        ).save().process()

        order.invalidate_recordset()
        line.invalidate_recordset()
        self.assertEqual(line.received_qty, 8.0)
        self.assertEqual(line.in_transit_qty, 0.0)
        self.assertEqual(line.remaining_delivery_qty, 2.0)
        self.assertEqual(line.remaining_receipt_qty, 0.0)
        self.assertEqual(order.state, "in_transit")
        self.assertTrue(order.has_pending_delivery)
        self.assertFalse(order.has_pending_receipt)
        delivery_backorder = delivery.backorder_ids
        receipt_backorder = receipt.backorder_ids
        self.assertEqual(len(delivery_backorder), 1)
        self.assertEqual(len(receipt_backorder), 1)
        self.assertTrue(
            receipt_backorder.move_ids.move_orig_ids & delivery_backorder.move_ids
        )
        self.assertIn(
            "Create Backorder",
            " ".join(order.message_ids.mapped("body")),
        )

        delivery_backorder.with_user(self.stock_request_manager).action_assign()
        delivery_backorder.move_line_ids.quantity = 2.0
        delivery_backorder.move_line_ids.picked = True
        delivery_backorder.with_user(self.stock_request_manager).button_validate()
        receipt_backorder.with_user(self.stock_request_manager).action_assign()
        receipt_backorder.move_line_ids.quantity = 2.0
        receipt_backorder.move_line_ids.picked = True
        receipt_backorder.with_user(self.stock_request_manager).button_validate()
        order.invalidate_recordset()
        line.invalidate_recordset()
        self.assertEqual(line.delivered_qty, 10.0)
        self.assertEqual(line.received_qty, 10.0)
        self.assertEqual(order.state, "done")

    def test_task3_no_backorder_cannot_cancel_sent_not_received(self):
        destination = self.env["stock.warehouse"].create(
            {
                "name": "Task 3 Transit Destination",
                "code": "T3TD",
                "company_id": self.main_company.id,
                "stock_request_responsible_user_ids": [
                    Command.set(self.stock_request_manager.ids)
                ],
            }
        )
        self._create_stock_quant(self.product, self.warehouse.lot_stock_id, 10.0)
        order = self.request_order.with_user(self.stock_request_user).create(
            {
                "source_warehouse_id": self.warehouse.id,
                "warehouse_id": destination.id,
                "stock_request_ids": [
                    Command.create(
                        {
                            "product_id": self.product.id,
                            "product_uom_id": self.product.uom_id.id,
                            "product_uom_qty": 10.0,
                        }
                    )
                ],
            }
        )
        order.with_user(self.stock_request_user).action_submit()
        order.with_user(self.stock_request_manager).action_confirm()
        line = order.stock_request_ids
        delivery = order.stock_request_operation_picking_ids.filtered(
            lambda picking: picking.stock_request_operation == "delivery"
        )
        delivery.with_user(self.stock_request_manager).action_assign()
        delivery.move_line_ids.quantity = 10.0
        delivery.move_line_ids.picked = True
        delivery.with_user(self.stock_request_manager).button_validate()

        receipt = order.stock_request_operation_picking_ids.filtered(
            lambda picking: picking.stock_request_operation == "receipt"
        )
        receipt.with_user(self.stock_request_manager).action_assign()
        receipt.move_line_ids.quantity = 8.0
        receipt.move_line_ids.picked = True
        receipt_action = receipt.with_user(
            self.stock_request_manager
        ).button_validate()
        wizard = Form(
            self.env["stock.backorder.confirmation"]
            .with_user(self.stock_request_manager)
            .with_context(receipt_action["context"])
        ).save()
        with self.assertRaisesRegex(exceptions.UserError, "No Backorder"):
            wizard.process_cancel_backorder()

        line.invalidate_recordset()
        self.assertEqual(line.received_qty, 0.0)
        self.assertEqual(line.in_transit_qty, 10.0)
        self.assertNotEqual(receipt.state, "done")
        wizard.process()
        line.invalidate_recordset()
        self.assertEqual(line.received_qty, 8.0)
        self.assertEqual(line.in_transit_qty, 2.0)
        self.assertEqual(len(receipt.backorder_ids), 1)
        audit_count = len(
            order.message_ids.filtered(
                lambda message: "Partial quantity decision" in str(message.body)
            )
        )
        wizard.process()
        self.assertEqual(len(receipt.backorder_ids), 1)
        self.assertEqual(
            len(
                order.message_ids.filtered(
                    lambda message: "Partial quantity decision"
                    in str(message.body)
                )
            ),
            audit_count,
        )

        close_action = order.with_user(
            self.stock_request_manager
        ).action_close_remaining_receipt()
        close_wizard = self.env[
            "stock.request.close.receipt.wizard"
        ].with_user(self.stock_request_manager).browse(close_action["res_id"])
        self.assertEqual(close_wizard.line_ids.received_quantity, 8.0)
        self.assertEqual(close_wizard.line_ids.remaining_quantity, 2.0)
        with self.assertRaisesRegex(exceptions.UserError, "transit location"):
            close_wizard.action_confirm_close()
        order.invalidate_recordset()
        line.invalidate_recordset()
        self.assertEqual(order.state, "in_transit")
        self.assertEqual(line.cancelled_receipt_qty, 0.0)
        self.assertEqual(line.remaining_receipt_qty, 2.0)
        self.assertEqual(len(receipt.backorder_ids), 1)

    def test_task18_in_transit_operational_report(self):
        destination = self.env["stock.warehouse"].create(
            {
                "name": "Task 18 Destination",
                "code": "T18D",
                "company_id": self.main_company.id,
                "stock_request_responsible_user_ids": [
                    Command.set(self.stock_request_manager.ids)
                ],
            }
        )
        self._create_stock_quant(self.product, self.warehouse.lot_stock_id, 2)
        expected_date = fields.Datetime.now() - timedelta(days=2)
        order = self.request_order.with_user(self.stock_request_user).create(
            {
                "source_warehouse_id": self.warehouse.id,
                "warehouse_id": destination.id,
                "expected_date": expected_date,
                "stock_request_ids": [
                    Command.create(
                        {
                            "product_id": self.product.id,
                            "product_uom_id": self.product.uom_id.id,
                            "product_uom_qty": 2.0,
                        }
                    )
                ],
            }
        )
        order.with_user(self.stock_request_user).action_submit()
        order.with_user(self.stock_request_manager).action_confirm()
        delivery = order.stock_request_operation_picking_ids.filtered(
            lambda picking: picking.stock_request_operation == "delivery"
        )
        receipt = order.stock_request_operation_picking_ids.filtered(
            lambda picking: picking.stock_request_operation == "receipt"
        )
        delivery.with_user(self.stock_request_manager).action_assign()
        delivery.move_line_ids.quantity = 2.0
        delivery.move_line_ids.picked = True
        delivery.with_user(self.stock_request_manager)._action_done()

        line = order.stock_request_ids
        line.invalidate_recordset()
        self.assertEqual(line.in_transit_qty, 2.0)
        self.assertEqual(line.source_warehouse_id, self.warehouse)
        self.assertEqual(line.warehouse_id, destination)
        self.assertEqual(line.transit_location_id, order.transit_location_id)
        self.assertEqual(line.operation_type, "request")
        self.assertEqual(line.delivered_by_id, self.stock_request_manager)
        self.assertEqual(line.delivery_date, delivery.date_done)
        self.assertEqual(line.operation_state, "in_transit")
        self.assertEqual(line.product_category_id, self.product.categ_id)
        self.assertTrue(line.overdue)
        self.assertGreaterEqual(line.days_in_transit, 0)

        report_action = self.env.ref(
            "stock_request.stock_request_in_transit_action"
        )
        report_domain = safe_eval(report_action.domain)
        self.assertIn(line, self.stock_request.search(report_domain))

        requester_line = line.with_user(self.stock_request_user)
        self.assertEqual(
            requester_line.action_open_stock_request_order()["res_id"],
            order.id,
        )
        delivery_action = requester_line.action_open_delivery_transfers()
        self.assertEqual(delivery_action["res_id"], delivery.id)
        self.assertEqual(
            delivery_action["views"][0][0],
            self.env.ref(
                "stock_request.stock_request_in_transit_picking_form"
            ).id,
        )
        receipt_action = requester_line.action_open_receipt_transfers()
        self.assertEqual(receipt_action["res_id"], receipt.id)

        receipt.with_user(self.stock_request_manager).action_cancel()
        line.invalidate_recordset()
        self.assertEqual(line.received_qty, 0.0)
        self.assertEqual(line.in_transit_qty, 2.0)
        self.assertEqual(
            line.in_transit_qty,
            line.delivered_qty - line.received_qty,
        )
        self.assertIn(line, self.stock_request.search(report_domain))

        report_arch = self.env.ref(
            "stock_request.stock_request_in_transit_list"
        ).get_combined_arch()
        for field_name in (
            "order_id",
            "operation_type",
            "source_warehouse_id",
            "warehouse_id",
            "transit_location_id",
            "product_id",
            "product_uom_id",
            "product_uom_qty",
            "delivered_qty",
            "received_qty",
            "in_transit_qty",
            "delivery_date",
            "expected_date",
            "days_in_transit",
            "delivered_by_id",
            "operation_state",
            "overdue",
        ):
            self.assertTrue(report_arch.xpath("//field[@name='%s']" % field_name))
        for method_name in (
            "action_open_stock_request_order",
            "action_open_delivery_transfers",
            "action_open_receipt_transfers",
        ):
            self.assertTrue(report_arch.xpath("//button[@name='%s']" % method_name))

        search_arch = self.env.ref(
            "stock_request.stock_request_in_transit_search"
        ).get_combined_arch()
        for filter_name in (
            "my_related_operations",
            "in_transit",
            "overdue",
            "stock_requests",
            "direct_transfers",
            "group_source_warehouse",
            "group_destination_warehouse",
            "group_transit_location",
            "group_product",
            "group_product_category",
            "group_delivered_by",
            "group_delivery_month",
        ):
            self.assertTrue(search_arch.xpath("//filter[@name='%s']" % filter_name))
        self.assertEqual(
            self.env.ref("stock_request.stock_request_in_transit_menu").action,
            report_action,
        )

    def test_task19_request_and_product_movement_analysis_views(self):
        analysis_fields = self.request_order._fields
        self.assertEqual(
            self.request_order._analysis_duration_days(
                datetime(2026, 1, 1, 0, 0, 0),
                datetime(2026, 1, 2, 12, 0, 0),
            ),
            1.5,
        )
        for field_name in (
            "submitted_date",
            "ready_date",
            "first_delivery_date",
            "receipt_date",
            "rejected_date",
            "cancelled_date",
        ):
            self.assertIn(field_name, analysis_fields)
        for field_name in (
            "requests_count",
            "completed_requests_count",
            "rejected_requests_count",
            "cancelled_requests_count",
        ):
            self.assertEqual(analysis_fields[field_name].aggregator, "sum")
        for field_name in (
            "review_time",
            "preparation_time",
            "transit_time",
            "total_cycle_time",
        ):
            self.assertEqual(analysis_fields[field_name].aggregator, "avg")

        request_pivot = self.env.ref(
            "stock_request.stock_request_order_analysis_pivot"
        ).get_combined_arch()
        request_measures = {
            field.get("name")
            for field in request_pivot.xpath("//pivot/field[@type='measure']")
        }
        self.assertEqual(
            request_measures,
            {
                "requests_count",
                "completed_requests_count",
                "rejected_requests_count",
                "cancelled_requests_count",
                "review_time",
                "preparation_time",
                "transit_time",
                "total_cycle_time",
            },
        )
        request_search = self.env.ref(
            "stock_request.stock_request_order_analysis_search"
        ).get_combined_arch()
        for filter_name in (
            "group_operation_type",
            "group_source_warehouse",
            "group_destination_warehouse",
            "group_requested_by",
            "group_delivered_by",
            "group_received_by",
            "group_status",
            "group_request_month",
        ):
            self.assertTrue(
                request_search.xpath("//filter[@name='%s']" % filter_name)
            )

        movement_pivot = self.env.ref(
            "stock_request.stock_request_product_movement_pivot"
        ).get_combined_arch()
        default_rows = [
            field.get("name")
            for field in movement_pivot.xpath("//pivot/field[@type='row']")
        ]
        self.assertEqual(default_rows[:2], ["product_id", "product_uom_id"])
        movement_measures = {
            field.get("name")
            for field in movement_pivot.xpath("//pivot/field[@type='measure']")
        }
        self.assertEqual(
            movement_measures,
            {
                "product_uom_qty",
                "delivered_qty",
                "received_qty",
                "in_transit_qty",
                "remaining_delivery_qty",
                "remaining_receipt_qty",
            },
        )
        movement_search = self.env.ref(
            "stock_request.stock_request_product_movement_search"
        ).get_combined_arch()
        for filter_name in (
            "group_product",
            "group_uom",
            "group_product_category",
            "group_source_warehouse",
            "group_destination_warehouse",
            "group_transit_location",
            "group_request_month",
            "group_operation_type",
        ):
            self.assertTrue(
                movement_search.xpath("//filter[@name='%s']" % filter_name)
            )

        for action_xmlid, model_name in (
            (
                "stock_request.stock_request_order_analysis_action",
                "stock.request.order",
            ),
            (
                "stock_request.stock_request_product_movement_action",
                "stock.request",
            ),
        ):
            action = self.env.ref(action_xmlid)
            self.assertEqual(action.res_model, model_name)
            self.assertEqual(action.view_mode, "pivot,graph,list")
        self.assertEqual(
            self.env.ref("stock_request.stock_request_order_analysis_menu").action,
            self.env.ref("stock_request.stock_request_order_analysis_action"),
        )
        self.assertEqual(
            self.env.ref("stock_request.stock_request_product_movement_menu").action,
            self.env.ref("stock_request.stock_request_product_movement_action"),
        )

    def test_task20_printable_stock_request_and_transfer_receipt(self):
        order = self.request_order.with_user(self.stock_request_user).create(
            {
                "source_warehouse_id": self.request_source_warehouse.id,
                "warehouse_id": self.warehouse.id,
                "reason": "Replenish branch stock",
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
        order.with_user(self.stock_request_user).action_submit()
        order.with_user(self.stock_request_manager).action_confirm()
        order.invalidate_recordset()
        self.assertEqual(order.accepted_by_id, self.stock_request_manager)

        report = self.env.ref("stock_request.action_report_stock_request_order")
        self.assertEqual(report.model, "stock.request.order")
        self.assertEqual(report.report_type, "qweb-pdf")
        self.assertEqual(
            report.report_name,
            "stock_request.report_stock_request_order_document",
        )
        action = order.with_user(self.stock_request_user).action_print_stock_request()
        self.assertEqual(action["type"], "ir.actions.report")
        self.assertEqual(action["report_name"], report.report_name)
        report_values = self.env[
            "report.stock_request.report_stock_request_order_document"
        ].with_user(self.stock_request_user)._get_report_values(order.ids)
        self.assertEqual(report_values["docs"].ids, order.ids)

        form_arch = self.env.ref(
            "stock_request.stock_request_order_form"
        ).get_combined_arch()
        self.assertTrue(
            form_arch.xpath("//button[@name='action_print_stock_request']")
        )

        report_arch = self.env.ref(
            "stock_request.report_stock_request_order_document"
        ).get_combined_arch()
        for expression in (
            "o.name",
            "o.operation_type",
            "o.state",
            "o.request_date",
            "o.expected_date",
            "o.requested_by",
            "o.source_warehouse_id",
            "o.warehouse_id",
            "o.reason",
            "o.accepted_by_id",
            "o.ready_date",
            "o.delivered_by_id",
            "o.delivery_date",
            "o.received_by_id",
            "o.receipt_date",
            "line.product_id",
            "line.product_uom_id",
            "line.product_uom_qty",
            "line.delivered_qty",
            "line.received_qty",
        ):
            self.assertTrue(
                report_arch.xpath("//*[@t-field='%s']" % expression), expression
            )
        self.assertTrue(
            report_arch.xpath(
                "//*[@t-esc='line.remaining_delivery_qty + line.in_transit_qty']"
            )
        )
        self.assertTrue(
            report_arch.xpath(
                "//*[@t-value='o._get_effective_transit_location()']"
            )
        )

        unrelated_user = new_test_user(
            self.env,
            login="task20_unrelated_user",
            groups="stock_request.group_stock_request_user",
            company_ids=[(4, self.main_company.id)],
        )
        with self.assertRaises(exceptions.AccessError):
            order.with_user(unrelated_user).action_print_stock_request()
        with self.assertRaises(exceptions.AccessError):
            self.env[
                "report.stock_request.report_stock_request_order_document"
            ].with_user(unrelated_user)._get_report_values(order.ids)

    def test_task21_final_user_experience(self):
        manager_group = self.env.ref(
            "stock_request.group_stock_request_manager"
        )
        user_group = self.env.ref("stock_request.group_stock_request_user")
        order = self.request_order.with_user(self.stock_request_user).create(
            {
                "source_warehouse_id": self.request_source_warehouse.id,
                "warehouse_id": self.warehouse.id,
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
        line = order.stock_request_ids
        self.assertEqual(order.next_action, "submit_request")
        self.assertEqual(line.fulfillment_status, "pending")

        order.with_user(self.stock_request_user).action_submit()
        self.assertEqual(order.next_action, "review_request")
        order.with_user(self.stock_request_manager).action_confirm()
        self.assertEqual(order.next_action, "deliver_products")

        cancelled_order = self.request_order.with_user(
            self.stock_request_user
        ).create(
            {
                "source_warehouse_id": self.request_source_warehouse.id,
                "warehouse_id": self.warehouse.id,
                "stock_request_ids": [
                    Command.create(
                        {
                            "product_id": self.product.id,
                            "product_uom_id": self.product.uom_id.id,
                            "product_uom_qty": 1.0,
                        }
                    )
                ],
            }
        )
        cancelled_order.with_user(self.stock_request_user).action_cancel()
        self.assertEqual(cancelled_order.next_action, "no_action")
        self.assertEqual(
            cancelled_order.stock_request_ids.fulfillment_status,
            "not_fulfilled",
        )

        main_tree = self.env.ref(
            "stock_request.stock_request_order_tree"
        ).get_combined_arch()
        queue_tree = self.env.ref(
            "stock_request.stock_request_order_work_queue_tree"
        ).get_combined_arch()
        for tree_arch in (main_tree, queue_tree):
            next_action_fields = tree_arch.xpath("//field[@name='next_action']")
            self.assertTrue(next_action_fields)
            self.assertEqual(next_action_fields[0].get("widget"), "badge")

        form_arch = self.env.ref(
            "stock_request.stock_request_order_form"
        ).get_combined_arch()
        line_list = form_arch.xpath(
            "//field[@name='stock_request_ids']/list"
        )[0]
        visible_draft_fields = {
            field.get("name")
            for field in line_list.xpath("./field")
            if not field.get("column_invisible")
        }
        self.assertEqual(
            visible_draft_fields,
            {"product_id", "product_uom_id", "product_uom_qty"},
        )
        for field_name in (
            "delivered_qty",
            "received_qty",
            "cancelled_receipt_qty",
            "remaining_delivery_qty",
            "remaining_receipt_qty",
            "fulfillment_status",
        ):
            field_node = line_list.xpath("./field[@name='%s']" % field_name)[0]
            self.assertIn("draft", field_node.get("column_invisible"))
            self.assertIn("submitted", field_node.get("column_invisible"))
        close_receipt_button = form_arch.xpath(
            "//button[@name='action_close_remaining_receipt']"
        )[0]
        self.assertEqual(
            close_receipt_button.get("string"),
            "Close Remaining Receipt",
        )
        self.assertIn(
            "can_close_remaining_receipt",
            close_receipt_button.get("invisible"),
        )
        transit_fields = form_arch.xpath(
            "//page[@name='other_information']"
            "//field[@name='transit_location_id']"
        )
        self.assertTrue(transit_fields)

        to_deliver_action = self.env.ref(
            "stock_request.stock_request_to_deliver_action"
        )
        to_deliver_menu = self.env.ref(
            "stock_request.stock_request_to_deliver_menu"
        )
        self.assertEqual(to_deliver_action.name, "To Deliver")
        self.assertEqual(to_deliver_menu.name, "To Deliver")

        for menu_xmlid in (
            "stock_request.menu_stock_request_reporting",
            "stock_request.stock_request_in_transit_menu",
            "stock_request.stock_request_order_analysis_menu",
            "stock_request.stock_request_product_movement_menu",
            "stock_request.menu_stock_request_configuration",
            "stock_request.stock_request_warehouse_setup_menu",
            "stock_request.stock_request_template_menu",
        ):
            menu = self.env.ref(menu_xmlid)
            self.assertIn(manager_group, menu.group_ids)
            self.assertNotIn(user_group, menu.group_ids)

    def test_purchase_fields_are_not_defined(self):
        self.assertNotIn("vendor_ids", self.stock_request._fields)
        self.assertNotIn("have_vendor", self.stock_request._fields)
        self.assertNotIn("purchase_order_ids", self.stock_request._fields)
        self.assertNotIn("purchase_order_count", self.stock_request._fields)
        self.assertNotIn("purchase_order_ids", self.request_order._fields)
        self.assertNotIn("purchase_order_count", self.request_order._fields)
        self.assertNotIn("have_vendor", self.env["stock.route"]._fields)

    def test_order_is_the_only_user_facing_request(self):
        action = self.env.ref("stock_request.stock_request_order_action")

        self.assertEqual(action.res_model, "stock.request.order")
        for xmlid in (
            "stock_request.menu_stock_request",
            "stock_request.action_stock_request_form",
            "stock_request.view_stock_request_tree",
            "stock_request.stock_request_search",
            "stock_request.view_stock_request_form",
            "stock_request.stock_route_form_view_inherit_stock_request",
        ):
            self.assertFalse(self.env.ref(xmlid, raise_if_not_found=False))
        self.assertNotIn("stock_request_count", self.request_order._fields)
        self.assertFalse(hasattr(self.request_order, "action_view_stock_requests"))

        with self.assertRaises(exceptions.UserError):
            self.stock_request.with_user(self.stock_request_user).create(
                {
                    "product_id": self.product.id,
                    "product_uom_id": self.product.uom_id.id,
                    "product_uom_qty": 5.0,
                    "company_id": self.main_company.id,
                    "warehouse_id": self.warehouse.id,
                    "location_id": self.warehouse.lot_stock_id.id,
                }
            )

    def test_obsolete_settings_are_removed(self):
        settings_fields = self.env["res.config.settings"]._fields
        company_fields = self.env["res.company"]._fields
        obsolete_settings = (
            "group_stock_request_order",
            "module_stock_request_purchase",
            "module_stock_request_mrp",
            "module_stock_request_kanban",
            "module_stock_request_analytic",
            "module_stock_request_submit",
            "stock_request_allow_virtual_loc",
            "stock_request_check_available_first",
        )
        for field_name in obsolete_settings:
            self.assertNotIn(field_name, settings_fields)
        self.assertNotIn("stock_request_allow_virtual_loc", company_fields)
        self.assertNotIn("stock_request_check_available_first", company_fields)
        self.assertNotIn("allow_virtual_location", self.stock_request._fields)
        self.assertNotIn("allow_virtual_location", self.request_order._fields)

        for xmlid in (
            "stock_request.menu_stock_request_config",
            "stock_request.action_stock_request_config",
            "stock_request.res_config_settings_view_form",
            "stock_request.group_stock_request_order",
        ):
            self.assertFalse(self.env.ref(xmlid, raise_if_not_found=False))

    def test_task22_legacy_procurement_is_removed(self):
        self.assertFalse(hasattr(self.stock_request, "_action_launch_procurement_rule"))
        self.assertFalse(hasattr(self.stock_request, "_prepare_procurement_values"))
        self.assertFalse(hasattr(self.stock_request, "_skip_procurement"))
        self.assertFalse(hasattr(self.request_order, "action_view_transfer"))
        self.assertFalse(hasattr(self.stock_request, "action_view_transfer"))

        standalone_line = self.stock_request.sudo().create(
            {
                "product_id": self.product.id,
                "product_uom_id": self.product.uom_id.id,
                "product_uom_qty": 1.0,
                "company_id": self.main_company.id,
                "warehouse_id": self.warehouse.id,
                "location_id": self.warehouse.lot_stock_id.id,
                "expected_date": fields.Datetime.now(),
            }
        )
        with self.assertRaisesRegex(
            exceptions.UserError,
            "Standalone Stock Request lines cannot be confirmed",
        ):
            standalone_line.action_confirm()

    def test_task24_standard_new_button_and_backend_security(self):
        main_tree = self.env.ref("stock_request.stock_request_order_tree")
        queue_tree = self.env.ref(
            "stock_request.stock_request_order_work_queue_tree"
        )
        main_list = main_tree.get_combined_arch().xpath("//list")[0]
        queue_list = queue_tree.get_combined_arch().xpath("//list")[0]
        self.assertNotIn(main_list.get("create"), ("0", "false"))
        self.assertEqual(queue_list.get("create"), "false")

        my_action = self.env.ref(
            "stock_request.stock_request_my_requests_action"
        )
        transfer_action = self.env.ref(
            "stock_request.stock_request_direct_transfer_action"
        )
        my_context = safe_eval(
            my_action.context, {"uid": self.stock_request_user.id}
        )
        transfer_context = safe_eval(transfer_action.context)
        self.assertEqual(my_action.view_id, main_tree)
        self.assertEqual(transfer_action.view_id, main_tree)
        self.assertEqual(my_context["default_operation_type"], "request")
        self.assertEqual(
            my_context["default_requested_by"], self.stock_request_user.id
        )
        self.assertEqual(
            safe_eval(my_action.domain, {"uid": self.stock_request_user.id}),
            [
                ("operation_type", "=", "request"),
                ("requested_by", "=", self.stock_request_user.id),
            ],
        )
        self.assertEqual(
            transfer_context["default_operation_type"], "transfer"
        )
        self.assertEqual(
            safe_eval(transfer_action.domain),
            [("operation_type", "=", "transfer")],
        )

        request = (
            self.request_order.with_user(self.stock_request_user)
            .with_context(**my_context)
            .create({})
        )
        self.assertEqual(request.operation_type, "request")
        self.assertEqual(request.requested_by, self.stock_request_user)
        self.assertEqual(request.state, "draft")

        for action_xmlid in (
            "stock_request.stock_request_to_review_action",
            "stock_request.stock_request_to_deliver_action",
            "stock_request.stock_request_to_receive_action",
            "stock_request.stock_request_all_requests_action",
            "stock_request.stock_request_all_operations_action",
        ):
            self.assertEqual(self.env.ref(action_xmlid).view_id, queue_tree)

        root_menu = self.env.ref("stock_request.menu_stock_request_root")
        request_menu = self.env.ref("stock_request.menu_stock_request_operations")
        self.assertEqual(request_menu.parent_id, root_menu)
        self.assertEqual(request_menu.name, "Stock Requests")
        request_menu_names = self.env["ir.ui.menu"].search(
            [("parent_id", "=", request_menu.id)], order="sequence, id"
        ).mapped("name")
        self.assertEqual(
            request_menu_names,
            [
                "My Requests",
                "To Review",
                "To Deliver",
                "To Receive",
                "All Requests",
            ],
        )
        direct_menu = self.env.ref(
            "stock_request.stock_request_direct_transfer_menu"
        )
        self.assertEqual(direct_menu.parent_id, root_menu)
        one_step_menu = self.env.ref(
            "stock_request.stock_request_one_step_transfer_menu"
        )
        self.assertEqual(one_step_menu.parent_id, root_menu)
        self.assertEqual(
            self.env.ref("stock_request.stock_request_template_menu").parent_id,
            self.env.ref("stock_request.menu_stock_request_configuration"),
        )
        configuration_menu = self.env.ref(
            "stock_request.menu_stock_request_configuration"
        )
        configuration_menu_names = self.env["ir.ui.menu"].search(
            [("parent_id", "=", configuration_menu.id)], order="sequence, id"
        ).mapped("name")
        self.assertEqual(
            configuration_menu_names,
            ["Settings", "Warehouse Setup", "Templates"],
        )
        self.assertFalse(
            self.env.ref(
                "stock_request.stock_request_all_operations_menu",
                raise_if_not_found=False,
            )
        )
        self.assertFalse(
            self.env.ref(
                "stock_request.menu_stock_request_master_data",
                raise_if_not_found=False,
            )
        )

        for report_view_xmlid in (
            "stock_request.stock_request_in_transit_list",
            "stock_request.stock_request_order_analysis_list",
            "stock_request.stock_request_product_movement_list",
        ):
            report_list = self.env.ref(report_view_xmlid).get_combined_arch().xpath(
                "//list"
            )[0]
            self.assertEqual(report_list.get("create"), "false")

        responsible = new_test_user(
            self.env,
            login="task24_source_responsible",
            groups="stock.group_stock_user",
            company_ids=[Command.link(self.main_company.id)],
        )
        self.request_source_warehouse.stock_request_responsible_user_ids = [
            Command.link(responsible.id)
        ]
        transfer_vals = {
            "source_warehouse_id": self.request_source_warehouse.id,
            "warehouse_id": self.warehouse.id,
        }
        with self.assertRaises(exceptions.AccessError):
            (
                self.request_order.with_user(self.stock_request_user)
                .with_context(**transfer_context)
                .create(transfer_vals)
            )
        transfer = (
            self.request_order.with_user(responsible)
            .with_context(**transfer_context)
            .create(transfer_vals)
        )
        self.assertEqual(transfer.operation_type, "transfer")
        self.assertEqual(transfer.state, "draft")

        manager_transfer = (
            self.request_order.with_user(self.stock_request_manager)
            .with_context(**transfer_context)
            .create(transfer_vals)
        )
        self.assertEqual(manager_transfer.operation_type, "transfer")

        self.assertFalse(
            self.env["ir.ui.menu"].with_context(lang="en_US").search(
                [
                    (
                        "name",
                        "in",
                        ["Create Stock Request", "Create Direct Transfer"],
                    )
                ]
            )
        )

    def test_task25_warehouse_setup_is_isolated_from_standard_form(self):
        standard_form = self.env.ref("stock.view_warehouse")
        standard_list = self.env.ref("stock.view_warehouse_tree")
        standard_action = self.env.ref("stock.action_warehouse_form")
        setup_form = self.env.ref(
            "stock_request.stock_request_warehouse_setup_form"
        )
        setup_list = self.env.ref(
            "stock_request.stock_request_warehouse_setup_list"
        )
        setup_action = self.env.ref(
            "stock_request.stock_request_warehouse_setup_action"
        )

        self.assertFalse(setup_form.inherit_id)
        self.assertFalse(setup_list.inherit_id)
        self.assertGreater(setup_form.priority, standard_form.priority)
        self.assertGreater(setup_list.priority, standard_list.priority)
        self.assertEqual(standard_action.view_id, standard_list)
        self.assertEqual(setup_action.view_id, setup_list)
        self.assertEqual(
            set(setup_action.view_ids.mapped("view_id").ids),
            {setup_list.id, setup_form.id},
        )
        self.assertFalse(
            self.env.ref(
                "stock_request.view_warehouse_inherit_bool_is_project_wh",
                raise_if_not_found=False,
            )
        )

        standard_arch = standard_form.get_combined_arch()
        self.assertFalse(
            standard_arch.xpath(
                "//field[@name='stock_request_responsible_user_ids']"
            )
        )
        self.assertFalse(standard_arch.xpath("//field[@name='is_project_wh']"))

        setup_arch = setup_form.get_combined_arch()
        for field_name in (
            "name",
            "stock_request_responsible_user_ids",
            "default_stock_request_source_warehouse_id",
            "stock_request_receiving_transit_location_id",
            "stock_request_transit_mode",
            "stock_request_configuration_status",
        ):
            self.assertTrue(setup_arch.xpath("//field[@name='%s']" % field_name))
        self.assertTrue(
            setup_arch.xpath(
                "//button[@name="
                "'action_create_stock_request_receiving_transit_location']"
            )
        )

    def test_task26_stock_transfer_rules_do_not_block_standard_pickings(self):
        source_user = new_test_user(
            self.env,
            login="task26_source_responsible",
            groups="stock.group_stock_user",
            company_ids=[Command.link(self.main_company.id)],
        )
        destination_user = new_test_user(
            self.env,
            login="task26_destination_responsible",
            groups="stock.group_stock_user",
            company_ids=[Command.link(self.main_company.id)],
        )
        unrelated_inventory_user = new_test_user(
            self.env,
            login="task26_unrelated_inventory",
            groups="stock.group_stock_user",
            company_ids=[Command.link(self.main_company.id)],
        )
        self.request_source_warehouse.stock_request_responsible_user_ids = [
            Command.set(source_user.ids)
        ]
        self.warehouse.stock_request_responsible_user_ids = [
            Command.set(destination_user.ids)
        ]

        order = self.request_order.with_user(self.stock_request_user).create(
            {
                "source_warehouse_id": self.request_source_warehouse.id,
                "warehouse_id": self.warehouse.id,
                "stock_request_ids": [
                    Command.create(
                        {
                            "product_id": self.product.id,
                            "product_uom_id": self.product.uom_id.id,
                            "product_uom_qty": 1.0,
                        }
                    )
                ],
            }
        )
        linked_picking = self.env["stock.picking"].sudo().create(
            {
                "picking_type_id": self.request_source_warehouse.int_type_id.id,
                "location_id": self.request_source_warehouse.lot_stock_id.id,
                "location_dest_id": self.warehouse.lot_stock_id.id,
                "company_id": self.main_company.id,
                "origin": order.name,
                "stock_request_order_id": order.id,
                "stock_request_operation": "delivery",
            }
        )
        linked_move = self.env["stock.move"].sudo().create(
            {
                "name": "Task 26 Stock Request Move",
                "product_id": self.product.id,
                "product_uom_qty": 1.0,
                "product_uom": self.product.uom_id.id,
                "location_id": self.request_source_warehouse.lot_stock_id.id,
                "location_dest_id": self.warehouse.lot_stock_id.id,
                "location_final_id": self.warehouse.lot_stock_id.id,
                "picking_id": linked_picking.id,
                "company_id": self.main_company.id,
                "stock_request_line_id": order.stock_request_ids.id,
            }
        )

        supplier_location = self.env.ref("stock.stock_location_suppliers")
        customer_location = self.env.ref("stock.stock_location_customers")
        standard_picking_values = (
            {
                "picking_type_id": self.warehouse.in_type_id.id,
                "location_id": supplier_location.id,
                "location_dest_id": self.warehouse.lot_stock_id.id,
                "origin": "Task 26 Purchase Receipt",
            },
            {
                "picking_type_id": self.warehouse.out_type_id.id,
                "location_id": self.warehouse.lot_stock_id.id,
                "location_dest_id": customer_location.id,
                "origin": "Task 26 Sales Delivery",
            },
            {
                "picking_type_id": self.request_source_warehouse.int_type_id.id,
                "location_id": self.request_source_warehouse.lot_stock_id.id,
                "location_dest_id": self.warehouse.lot_stock_id.id,
                "origin": "Task 26 Standard Internal Transfer",
            },
        )
        standard_pickings = self.env["stock.picking"].sudo().create(
            [
                {**values, "company_id": self.main_company.id}
                for values in standard_picking_values
            ]
        )
        standard_move = self.env["stock.move"].sudo().create(
            {
                "name": "Task 26 Standard Move",
                "product_id": self.product.id,
                "product_uom_qty": 1.0,
                "product_uom": self.product.uom_id.id,
                "location_id": self.request_source_warehouse.lot_stock_id.id,
                "location_dest_id": self.warehouse.lot_stock_id.id,
                "location_final_id": self.warehouse.lot_stock_id.id,
                "picking_id": standard_pickings[-1].id,
                "company_id": self.main_company.id,
            }
        )

        self.assertEqual(
            len(
                standard_pickings.with_user(unrelated_inventory_user).read(
                    ["name"]
                )
            ),
            3,
        )
        self.assertTrue(
            standard_move.with_user(unrelated_inventory_user).read(["name"])
        )
        self.assertEqual(
            len(
                standard_pickings.with_user(self.stock_request_manager).read(
                    ["name"]
                )
            ),
            3,
        )
        with self.assertRaises(exceptions.AccessError):
            standard_pickings[0].with_user(self.stock_request_user).read(["name"])
        with self.assertRaises(exceptions.AccessError):
            standard_move.with_user(self.stock_request_user).read(["name"])

        for user in (
            self.stock_request_user,
            source_user,
            destination_user,
            self.stock_request_manager,
        ):
            self.assertTrue(linked_picking.with_user(user).read(["name"]))
            self.assertTrue(linked_move.with_user(user).read(["name"]))
        with self.assertRaises(exceptions.AccessError):
            linked_picking.with_user(unrelated_inventory_user).read(["name"])
        with self.assertRaises(exceptions.AccessError):
            linked_move.with_user(unrelated_inventory_user).read(["name"])

    def test_onchanges_order(self):
        expected_date = fields.Datetime.now()
        vals = {
            "company_id": self.main_company.id,
            "warehouse_id": self.warehouse.id,
            "location_id": self.warehouse.lot_stock_id.id,
            "expected_date": expected_date,
            "stock_request_ids": [
                Command.create(
                    {
                        "product_id": self.product.id,
                        "product_uom_id": self.product.uom_id.id,
                        "product_uom_qty": 5.0,
                        "company_id": self.main_company.id,
                        "warehouse_id": self.warehouse.id,
                        "location_id": self.warehouse.lot_stock_id.id,
                        "expected_date": expected_date,
                    },
                )
            ],
        }
        order = self.request_order.with_user(self.stock_request_user).new(vals)
        self.stock_request_user.company_id = self.company_2
        order.company_id = self.company_2

        order.onchange_company_id()

        stock_request = order.stock_request_ids
        self.assertEqual(order.warehouse_id, self.wh2)
        self.assertEqual(order.location_id, self.wh2.lot_stock_id)
        self.assertEqual(order.warehouse_id, stock_request.warehouse_id)

        procurement_group = self.env["procurement.group"].create({"name": "TEST"})
        order.procurement_group_id = procurement_group
        order.onchange_procurement_group_id()
        self.assertEqual(
            order.procurement_group_id, order.stock_request_ids.procurement_group_id
        )

        order.procurement_group_id = procurement_group
        order.onchange_procurement_group_id()
        self.assertEqual(
            order.procurement_group_id, order.stock_request_ids.procurement_group_id
        )
        order.expected_date = datetime.now()
        order.onchange_expected_date()
        self.assertEqual(order.expected_date, order.stock_request_ids.expected_date)

        order.requested_by = self.stock_request_manager
        order.onchange_requested_by()
        self.assertEqual(order.requested_by, order.stock_request_ids.requested_by)

    def test_onchanges(self):
        self.product.route_ids = [Command.set(self.route.ids)]
        vals = {
            "product_uom_id": self.product.uom_id.id,
            "product_uom_qty": 5.0,
            "company_id": self.main_company.id,
        }
        stock_request = self.stock_request.with_user(self.stock_request_user).new(vals)
        stock_request.product_id = self.product
        vals = stock_request.default_get(["warehouse_id", "company_id"])
        stock_request.update(vals)
        stock_request.onchange_product_id()
        self.assertIn(self.route.id, stock_request.route_ids.ids)

        self.stock_request_user.company_id = self.company_2
        stock_request.company_id = self.company_2
        stock_request.onchange_company_id()

        self.assertEqual(stock_request.warehouse_id, self.wh2)
        self.assertEqual(stock_request.location_id, self.wh2.lot_stock_id)

        product = self.env["product.product"].create(
            {
                "name": "Wheat",
                "uom_id": self.env.ref("uom.product_uom_kgm").id,
                "uom_po_id": self.env.ref("uom.product_uom_kgm").id,
            }
        )

        # Test onchange_product_id
        stock_request.product_id = product
        stock_request.onchange_product_id()

        self.assertEqual(
            stock_request.product_uom_id, self.env.ref("uom.product_uom_kgm")
        )

        stock_request.product_id = self.env["product.product"]

        # Test onchange_warehouse_id
        wh2_2 = (
            self.env["stock.warehouse"]
            .with_context(company_id=self.company_2.id)
            .create(
                {
                    "name": "C2_2",
                    "code": "C2_2",
                    "company_id": self.company_2.id,
                    "partner_id": self.company_2_address.id,
                }
            )
        )
        stock_request.warehouse_id = wh2_2
        stock_request.onchange_warehouse_id()

        self.assertEqual(stock_request.warehouse_id, wh2_2)

        self.stock_request_user.company_id = self.main_company
        stock_request.warehouse_id = self.warehouse
        stock_request.onchange_warehouse_id()

        self.assertEqual(stock_request.company_id, self.main_company)
        self.assertEqual(stock_request.location_id, self.warehouse.lot_stock_id)

    def test_stock_request_order_validations_01(self):
        """Testing the discrepancy in warehouse_id between
        stock request and order"""
        expected_date = fields.Datetime.now()
        vals = {
            "company_id": self.main_company.id,
            "warehouse_id": self.wh2.id,
            "location_id": self.warehouse.lot_stock_id.id,
            "expected_date": expected_date,
            "stock_request_ids": [
                Command.create(
                    {
                        "product_id": self.product.id,
                        "product_uom_id": self.product.uom_id.id,
                        "product_uom_qty": 5.0,
                        "company_id": self.main_company.id,
                        "warehouse_id": self.warehouse.id,
                        "location_id": self.warehouse.lot_stock_id.id,
                        "expected_date": expected_date,
                    },
                )
            ],
        }
        order = self.request_order.with_user(self.stock_request_user).create(vals)
        self.assertEqual(order.company_id, self.wh2.company_id)
        self.assertEqual(order.location_id, self.wh2.lot_stock_id)
        self.assertEqual(order.stock_request_ids.warehouse_id, order.warehouse_id)

    def test_stock_request_order_validations_02(self):
        """Testing the discrepancy in location_id between
        stock request and order"""
        expected_date = fields.Datetime.now()
        vals = {
            "company_id": self.main_company.id,
            "warehouse_id": self.warehouse.id,
            "location_id": self.wh2.lot_stock_id.id,
            "expected_date": expected_date,
            "stock_request_ids": [
                Command.create(
                    {
                        "product_id": self.product.id,
                        "product_uom_id": self.product.uom_id.id,
                        "product_uom_qty": 5.0,
                        "company_id": self.main_company.id,
                        "warehouse_id": self.warehouse.id,
                        "location_id": self.warehouse.lot_stock_id.id,
                        "expected_date": expected_date,
                    },
                )
            ],
        }
        order = self.request_order.with_user(self.stock_request_user).create(vals)
        self.assertEqual(order.location_id, self.warehouse.lot_stock_id)
        self.assertEqual(order.stock_request_ids.location_id, order.location_id)

    def test_stock_request_order_validations_03(self):
        """Testing the discrepancy in requested_by between
        stock request and order"""
        expected_date = fields.Datetime.now()
        vals = {
            "company_id": self.main_company.id,
            "warehouse_id": self.warehouse.id,
            "location_id": self.warehouse.lot_stock_id.id,
            "requested_by": self.stock_request_user.id,
            "expected_date": expected_date,
            "stock_request_ids": [
                Command.create(
                    {
                        "product_id": self.product.id,
                        "product_uom_id": self.product.uom_id.id,
                        "product_uom_qty": 5.0,
                        "requested_by": self.stock_request_manager.id,
                        "company_id": self.main_company.id,
                        "warehouse_id": self.warehouse.id,
                        "location_id": self.warehouse.lot_stock_id.id,
                        "expected_date": expected_date,
                    },
                )
            ],
        }
        order = self.request_order.with_user(self.stock_request_user).create(vals)
        self.assertEqual(order.stock_request_ids.requested_by, order.requested_by)

    def test_stock_request_order_validations_04(self):
        """Testing the discrepancy in procurement_group_id between
        stock request and order"""
        procurement_group = self.env["procurement.group"].create(
            {"name": "Procurement"}
        )
        expected_date = fields.Datetime.now()
        vals = {
            "company_id": self.main_company.id,
            "warehouse_id": self.warehouse.id,
            "location_id": self.warehouse.lot_stock_id.id,
            "procurement_group_id": procurement_group.id,
            "expected_date": expected_date,
            "stock_request_ids": [
                Command.create(
                    {
                        "product_id": self.product.id,
                        "product_uom_id": self.product.uom_id.id,
                        "product_uom_qty": 5.0,
                        "company_id": self.main_company.id,
                        "warehouse_id": self.warehouse.id,
                        "location_id": self.warehouse.lot_stock_id.id,
                        "expected_date": expected_date,
                    },
                )
            ],
        }
        order = self.request_order.with_user(self.stock_request_user).create(vals)
        self.assertEqual(
            order.stock_request_ids.procurement_group_id,
            order.procurement_group_id,
        )

    def test_stock_request_order_validations_05(self):
        """Testing the discrepancy in company between
        stock request and order"""
        expected_date = fields.Datetime.now()
        vals = {
            "company_id": self.company_2.id,
            "warehouse_id": self.wh2.id,
            "location_id": self.wh2.lot_stock_id.id,
            "expected_date": expected_date,
            "stock_request_ids": [
                Command.create(
                    {
                        "product_id": self.product.id,
                        "product_uom_id": self.product.uom_id.id,
                        "product_uom_qty": 5.0,
                        "company_id": self.company_2.id,
                        "warehouse_id": self.warehouse.id,
                        "location_id": self.warehouse.lot_stock_id.id,
                        "expected_date": expected_date,
                    },
                )
            ],
        }
        order = self.request_order.with_user(self.stock_request_user).create(vals)
        self.assertEqual(order.stock_request_ids.company_id, order.company_id)
        self.assertEqual(order.stock_request_ids.warehouse_id, order.warehouse_id)

    def test_stock_request_order_validations_06(self):
        """Testing the discrepancy in expected dates between
        stock request and order"""
        expected_date = fields.Datetime.now()
        child_expected_date = "2015-01-01"
        vals = {
            "company_id": self.company_2.id,
            "warehouse_id": self.warehouse.id,
            "location_id": self.warehouse.lot_stock_id.id,
            "expected_date": expected_date,
            "stock_request_ids": [
                Command.create(
                    {
                        "product_id": self.product.id,
                        "product_uom_id": self.product.uom_id.id,
                        "product_uom_qty": 5.0,
                        "company_id": self.main_company.id,
                        "warehouse_id": self.warehouse.id,
                        "location_id": self.warehouse.lot_stock_id.id,
                        "expected_date": child_expected_date,
                    },
                )
            ],
        }
        order = self.request_order.create(vals)
        self.assertEqual(order.company_id, self.warehouse.company_id)
        self.assertEqual(order.stock_request_ids.expected_date, order.expected_date)

    def test_stock_request_order_source_location_is_automatic(self):
        expected_date = fields.Datetime.now()
        vals = {
            "company_id": self.main_company.id,
            "warehouse_id": self.warehouse.id,
            "location_id": self.warehouse.lot_stock_id.id,
            "expected_date": expected_date,
            "stock_request_ids": [
                Command.create(
                    {
                        "product_id": self.product.id,
                        "product_uom_id": self.product.uom_id.id,
                        "product_uom_qty": 5.0,
                        "company_id": self.main_company.id,
                        "warehouse_id": self.warehouse.id,
                        "location_id": self.warehouse.lot_stock_id.id,
                        "expected_date": expected_date,
                    },
                )
            ],
        }
        order = self.request_order.with_user(self.stock_request_user).create(vals)
        self.assertEqual(order.source_location_id, self.warehouse.lot_stock_id)
        self.assertEqual(
            order.stock_request_ids.source_location_id,
            order.source_location_id,
        )

    def test_stock_request_validations_01(self):
        vals = {
            "product_id": self.product.id,
            "product_uom_id": self.env.ref("uom.product_uom_kgm").id,
            "product_uom_qty": 5.0,
            "company_id": self.main_company.id,
            "warehouse_id": self.warehouse.id,
            "location_id": self.warehouse.lot_stock_id.id,
        }
        # Select a UoM that is incompatible with the product's UoM
        with self.assertRaises(exceptions.ValidationError):
            self.stock_request.with_user(self.stock_request_user).sudo().create(
                vals
            )

    def test_create_request_01(self):
        expected_date = fields.Datetime.now()
        vals = {
            "company_id": self.main_company.id,
            "source_warehouse_id": self.request_source_warehouse.id,
            "warehouse_id": self.warehouse.id,
            "location_id": self.warehouse.lot_stock_id.id,
            "expected_date": expected_date,
            "stock_request_ids": [
                Command.create(
                    {
                        "product_id": self.product.id,
                        "product_uom_id": self.product.uom_id.id,
                        "product_uom_qty": 5.0,
                        "company_id": self.main_company.id,
                        "warehouse_id": self.warehouse.id,
                        "location_id": self.warehouse.lot_stock_id.id,
                        "expected_date": expected_date,
                    },
                )
            ],
        }

        order = self.request_order.with_user(self.stock_request_user).create(vals)

        stock_request = order.stock_request_ids

        order.with_user(self.stock_request_user).action_submit()
        order.with_user(self.stock_request_manager).action_confirm()
        self.assertEqual(order.state, "ready")
        self.assertEqual(stock_request.state, "open")

        self.assertEqual(len(order.picking_ids), 2)
        self.assertEqual(len(order.move_ids), 2)
        self.assertEqual(len(stock_request.picking_ids), 2)
        self.assertEqual(len(stock_request.move_ids), 2)
        delivery = order.picking_ids.filtered(
            lambda picking: picking.stock_request_operation == "delivery"
        )
        receipt = order.picking_ids.filtered(
            lambda picking: picking.stock_request_operation == "receipt"
        )
        transit_location = self.main_company.internal_transit_location_id
        self.assertEqual(order.transit_location_id, transit_location)
        self.assertEqual(
            delivery.location_id,
            self.request_source_warehouse.lot_stock_id,
        )
        self.assertEqual(delivery.location_dest_id, transit_location)
        self.assertEqual(receipt.location_id, transit_location)
        self.assertEqual(receipt.location_dest_id, self.warehouse.lot_stock_id)
        self.assertEqual(receipt.move_ids.move_orig_ids, delivery.move_ids)
        self.assertEqual(receipt.state, "waiting")
        self.assertEqual(delivery.origin, order.name)
        self.assertEqual(receipt.origin, order.name)
        with self.assertRaises(exceptions.UserError):
            receipt.button_validate()
        self.assertEqual(stock_request.qty_in_progress, stock_request.product_uom_qty)
        transit_quantity_before = self.env["stock.quant"]._get_available_quantity(
            self.product, transit_location
        )
        destination_quantity_before = self.env[
            "stock.quant"
        ]._get_available_quantity(self.product, self.warehouse.lot_stock_id)
        self.env["stock.quant"].create(
            {
                "product_id": self.product.id,
                "location_id": self.request_source_warehouse.lot_stock_id.id,
                "quantity": 5.0,
            }
        )
        self.assertEqual(stock_request.qty_in_progress, 5.0)
        self.assertEqual(stock_request.qty_done, 0.0)
        delivery.with_user(self.stock_request_manager).action_assign()
        packout1 = delivery.move_line_ids[0]
        packout1.quantity = 5
        packout1.picked = True
        delivery.with_user(self.stock_request_manager)._action_done()
        self.assertEqual(order.state, "in_transit")
        self.assertEqual(
            self.env["stock.quant"]._get_available_quantity(
                self.product, transit_location
            ),
            transit_quantity_before + 5,
        )
        self.assertEqual(
            self.env["stock.quant"]._get_available_quantity(
                self.product, self.warehouse.lot_stock_id
            ),
            destination_quantity_before,
        )
        self.assertEqual(stock_request.qty_in_progress, 5.0)
        self.assertEqual(stock_request.qty_done, 0.0)
        receipt.with_user(self.stock_request_manager).action_assign()
        packin1 = receipt.move_line_ids[0]
        packin1.quantity = 5
        packin1.picked = True
        receipt.with_user(self.stock_request_manager)._action_done()
        self.assertEqual(stock_request.qty_in_progress, 0.0)
        self.assertEqual(stock_request.qty_done, stock_request.product_uom_qty)
        self.assertEqual(order.state, "done")
        self.assertEqual(stock_request.state, "done")

    def test_cancel_request(self):
        expected_date = fields.Datetime.now()
        vals = {
            "company_id": self.main_company.id,
            "source_warehouse_id": self.request_source_warehouse.id,
            "warehouse_id": self.warehouse.id,
            "location_id": self.warehouse.lot_stock_id.id,
            "expected_date": expected_date,
            "stock_request_ids": [
                Command.create(
                    {
                        "product_id": self.product.id,
                        "product_uom_id": self.product.uom_id.id,
                        "product_uom_qty": 5.0,
                        "company_id": self.main_company.id,
                        "warehouse_id": self.warehouse.id,
                        "location_id": self.warehouse.lot_stock_id.id,
                        "expected_date": expected_date,
                    },
                )
            ],
        }

        order = self.request_order.with_user(self.stock_request_user).create(vals)
        stock_request = order.stock_request_ids

        order.with_user(self.stock_request_user).action_cancel()

        self.assertEqual(order.state, "cancelled")
        self.assertEqual(stock_request.state, "cancel")
        self.assertFalse(order.picking_ids)
        with self.assertRaises(exceptions.UserError):
            order.with_user(self.stock_request_user).action_submit()

    def test_stock_request_constrains(self):
        vals = {
            "product_id": self.product.id,
            "product_uom_id": self.product.uom_id.id,
            "product_uom_qty": 5.0,
            "company_id": self.main_company.id,
            "warehouse_id": self.warehouse.id,
            "location_id": self.warehouse.lot_stock_id.id,
        }

        stock_request = (
            self.stock_request.with_user(self.stock_request_user).sudo().create(vals)
        )

        # Cannot assign a warehouse that belongs to another company
        with self.assertRaises(exceptions.ValidationError):
            stock_request.warehouse_id = self.wh2
        # Cannot assign a product that belongs to another company
        with self.assertRaises(exceptions.ValidationError):
            stock_request.product_id = self.product_company_2
        # Cannot assign a location that belongs to another company
        with self.assertRaises(exceptions.ValidationError):
            stock_request.location_id = self.wh2.lot_stock_id
        # Cannot assign a route that belongs to another company
        with self.assertRaises(exceptions.ValidationError):
            stock_request.route_id = self.route_2

    def test_stock_request_order_from_products(self):
        # Setup for Product Template and Attributes
        template_a = self.env["product.template"].create({"name": "ProductTemplate"})
        product_attribute = self._create_product_attribute("Attribute")

        product_att_value1 = self._create_product_attribute_value(
            "Name-1", product_attribute.id
        )
        product_att_value2 = self._create_product_attribute_value(
            "Name-2", product_attribute.id
        )
        product_att_value3 = self._create_product_attribute_value(
            "Name-3", product_attribute.id
        )

        product_tmpl_att_line = self._create_product_template_attribute_line(
            template_a.id,
            product_attribute.id,
            product_att_value1 | product_att_value2 | product_att_value3,
        )
        template_a.attribute_line_ids |= product_tmpl_att_line

        # Trigger the creation of all possible variants
        template_a._create_variant_ids()

        # Search for the the generated variants
        product_a1 = self.env["product.product"].search(
            [("product_template_variant_value_ids.name", "=", "Name-1")]
        )
        product_a2 = self.env["product.product"].search(
            [("product_template_variant_value_ids.name", "=", "Name-2")]
        )
        product_a3 = self.env["product.product"].search(
            [("product_template_variant_value_ids.name", "=", "Name-3")]
        )

        # Setup for Product B with one inactive variant
        product_b1 = self._create_product("CODEB1", "Product B1", self.main_company.id)
        template_b = product_b1.product_tmpl_id
        # One archived variant of B
        self._create_product(
            "CODEB2",
            "Product B2",
            self.main_company.id,
            product_tmpl_id=template_b.id,
            active=False,
        )

        order = self.request_order

        # Selecting some variants and creating an order
        preexisting = order.search([])
        wanted_products = product_a1 + product_a2 + product_b1
        action = order._create_from_product_multiselect(wanted_products)
        new_order = order.search([]) - preexisting
        self.assertEqual(len(new_order), 1)
        self.assertEqual(
            action["res_id"],
            new_order.id,
            msg="Returned action references the wrong record",
        )
        self.assertEqual(
            Counter(wanted_products),
            Counter(new_order.stock_request_ids.mapped("product_id")),
            msg="Not all wanted products were ordered",
        )

        # Selecting a template and creating an order
        preexisting = order.search([])
        action = order._create_from_product_multiselect(template_a)
        new_order = order.search([]) - preexisting
        self.assertEqual(len(new_order), 1)
        self.assertEqual(
            action["res_id"],
            new_order.id,
            msg="Returned action references the wrong record",
        )
        self.assertEqual(
            Counter(product_a1 + product_a2 + product_a3),
            Counter(new_order.stock_request_ids.mapped("product_id")),
            msg="Not all of the template's variants were ordered",
        )

        # Selecting a template
        preexisting = order.search([])
        action = order._create_from_product_multiselect(template_a + template_b)
        new_order = order.search([]) - preexisting
        self.assertEqual(len(new_order), 1)
        self.assertEqual(
            action["res_id"],
            new_order.id,
            msg="Returned action references the wrong record",
        )
        self.assertEqual(
            Counter(product_a1 + product_a2 + product_a3 + product_b1),
            Counter(new_order.stock_request_ids.mapped("product_id")),
            msg="Inactive variant was ordered though it shouldn't have been",
        )

        # If a user does not have stock request rights, they can still trigger
        # the action from the products, so test that they get a friendlier
        # error message.
        self.stock_request_user.group_ids -= self.stock_request_user_group
        with self.assertRaises(exceptions.AccessError):
            order.with_user(self.stock_request_user)._create_from_product_multiselect(
                template_a + template_b
            )

        # Empty recordsets should just return False
        self.assertFalse(
            order._create_from_product_multiselect(self.env["product.product"])
        )

        # Wrong model should just raise ValidationError
        with self.assertRaises(exceptions.ValidationError):
            order._create_from_product_multiselect(self.stock_request_user)

    def test_onchange_wh_no_effect_from_order(self):
        expected_date = fields.Datetime.now()
        vals = {
            "company_id": self.main_company.id,
            "warehouse_id": self.warehouse.id,
            "location_id": self.virtual_loc.id,
            "expected_date": expected_date,
            "stock_request_ids": [
                Command.create(
                    {
                        "product_id": self.product.id,
                        "product_uom_id": self.product.uom_id.id,
                        "product_uom_qty": 5.0,
                        "company_id": self.main_company.id,
                        "warehouse_id": self.warehouse.id,
                        "location_id": self.virtual_loc.id,
                        "expected_date": expected_date,
                    },
                )
            ],
        }
        order = self.request_order.with_user(self.stock_request_user).create(vals)
        order.stock_request_ids.onchange_warehouse_id()
        self.assertEqual(order.stock_request_ids[0].location_id, self.virtual_loc)

    def test_cancellation(self):
        product2 = self._create_product("SH2", "Shoes2", False)
        product3 = self._create_product("SH3", "Shoes3", False)
        vals = {
            "company_id": self.main_company.id,
            "source_warehouse_id": self.request_source_warehouse.id,
            "warehouse_id": self.warehouse.id,
            "stock_request_ids": [
                Command.create(
                    {
                        "product_id": self.product.id,
                        "product_uom_id": self.product.uom_id.id,
                        "product_uom_qty": 5.0,
                    },
                ),
                Command.create(
                    {
                        "product_id": product2.id,
                        "product_uom_id": self.product.uom_id.id,
                        "product_uom_qty": 5.0,
                    },
                ),
                Command.create(
                    {
                        "product_id": product3.id,
                        "product_uom_id": self.product.uom_id.id,
                        "product_uom_qty": 5.0,
                    },
                ),
            ],
        }
        order = self.request_order.create(vals)
        order.action_cancel()

        self.assertEqual(order.state, "cancelled")
        self.assertTrue(all(line.state == "cancel" for line in order.stock_request_ids))
        self.assertFalse(order.picking_ids)
        self.assertFalse(order.move_ids)

    def test_two_step_without_transit_workflow(self):
        settings = self.env["res.config.settings"].create(
            {"stock_request_transfer_flow": "two_step_no_transit"}
        )
        settings.set_values()
        self.assertEqual(
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("stock_request.transfer_flow"),
            "two_step_no_transit",
        )
        self.assertEqual(
            dict(
                self.request_order._fields["transfer_flow"]._description_selection(
                    self.env
                )
            )["two_step_no_transit"],
            "Two Step without Transit",
        )

        source_user = new_test_user(
            self.env,
            login="no_transit_source_responsible",
            groups="stock_request.group_stock_request_warehouse_responsible",
            company_ids=[Command.link(self.main_company.id)],
        )
        destination_user = new_test_user(
            self.env,
            login="no_transit_destination_responsible",
            groups="stock_request.group_stock_request_warehouse_responsible",
            company_ids=[Command.link(self.main_company.id)],
        )
        self.warehouse.stock_request_responsible_user_ids = [
            Command.set(source_user.ids)
        ]
        destination = self.env["stock.warehouse"].create(
            {
                "name": "No Transit Destination",
                "code": "NTD",
                "company_id": self.main_company.id,
                "stock_request_responsible_user_ids": [
                    Command.set(destination_user.ids)
                ],
            }
        )
        self.main_company.internal_transit_location_id = False
        self._create_stock_quant(
            self.product,
            self.warehouse.lot_stock_id,
            5.0,
        )
        order = self.request_order.with_user(self.stock_request_user).create(
            {
                "source_warehouse_id": self.warehouse.id,
                "warehouse_id": destination.id,
                "stock_request_ids": [
                    Command.create(
                        {
                            "product_id": self.product.id,
                            "product_uom_id": self.product.uom_id.id,
                            "product_uom_qty": 5.0,
                        }
                    )
                ],
            }
        )
        self.assertEqual(order.transfer_flow, "two_step_no_transit")
        self.assertFalse(order.transit_location_id)

        source_location = self.warehouse.lot_stock_id
        destination_location = destination.lot_stock_id
        quant_model = self.env["stock.quant"]
        source_before = quant_model._get_available_quantity(
            self.product,
            source_location,
        )
        destination_before = quant_model._get_available_quantity(
            self.product,
            destination_location,
        )

        order.with_user(self.stock_request_user).action_submit()
        order.with_user(source_user).action_confirm()
        self.assertEqual(order.state, "ready")
        self.assertFalse(order.stock_request_operation_picking_ids)
        self.assertFalse(order.stock_request_ids.operation_move_ids)
        self.assertEqual(
            quant_model._get_available_quantity(self.product, source_location),
            source_before,
        )

        order.with_user(source_user).action_deliver_products()
        self.assertEqual(order.state, "in_transit")
        self.assertTrue(order.has_pending_receipt)
        self.assertFalse(order.has_pending_delivery)
        self.assertFalse(order.stock_request_operation_picking_ids)
        self.assertFalse(order.stock_request_ids.operation_move_ids)
        self.assertEqual(order.stock_request_ids.in_transit_qty, 0.0)
        self.assertEqual(
            quant_model._get_available_quantity(self.product, source_location),
            source_before,
        )
        self.assertEqual(
            quant_model._get_available_quantity(self.product, destination_location),
            destination_before,
        )
        with self.assertRaises(exceptions.UserError):
            order.with_user(source_user).action_deliver_products()

        receive_domain = safe_eval(
            self.env.ref("stock_request.stock_request_to_receive_action").domain,
            {"uid": destination_user.id},
        )
        self.assertIn(
            order,
            self.request_order.with_user(destination_user).search(receive_domain),
        )
        with self.assertRaises(exceptions.AccessError):
            order.with_user(source_user).action_receive_products()

        wizard_action = order.with_user(destination_user).action_receive_products()
        wizard = self.env["stock.request.receive.wizard"].with_user(
            destination_user
        ).browse(wizard_action["res_id"])
        wizard.line_ids.quantity = 2.0
        wizard.action_confirm_receipt()

        order.invalidate_recordset()
        order.stock_request_ids.invalidate_recordset()
        receipts = order.stock_request_operation_picking_ids
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts.stock_request_operation, "receipt")
        self.assertEqual(receipts.state, "done")
        self.assertEqual(receipts.location_id, source_location)
        self.assertEqual(receipts.location_dest_id, destination_location)
        self.assertFalse(order.transit_location_id)
        self.assertEqual(order.stock_request_ids.received_qty, 2.0)
        self.assertEqual(order.stock_request_ids.remaining_receipt_qty, 3.0)
        self.assertEqual(order.state, "in_transit")
        self.assertIn(
            "Keep Remaining Pending",
            " ".join(order.message_ids.mapped("body")),
        )
        self.assertEqual(
            quant_model._get_available_quantity(self.product, source_location),
            source_before - 2.0,
        )
        self.assertEqual(
            quant_model._get_available_quantity(self.product, destination_location),
            destination_before + 2.0,
        )

        wizard.action_confirm_receipt()
        self.assertEqual(len(order.stock_request_operation_picking_ids), 1)
        self.assertEqual(order.stock_request_ids.received_qty, 2.0)

        final_action = order.with_user(destination_user).action_receive_products()
        final_wizard = self.env["stock.request.receive.wizard"].with_user(
            destination_user
        ).browse(final_action["res_id"])
        self.assertEqual(final_wizard.line_ids.quantity, 3.0)
        final_wizard.action_confirm_receipt()

        order.invalidate_recordset()
        order.stock_request_ids.invalidate_recordset()
        self.assertEqual(order.state, "done")
        self.assertFalse(order.has_pending_receipt)
        self.assertEqual(order.stock_request_ids.received_qty, 5.0)
        self.assertEqual(len(order.stock_request_operation_picking_ids), 2)
        self.assertEqual(
            quant_model._get_available_quantity(self.product, source_location),
            source_before - 5.0,
        )
        self.assertEqual(
            quant_model._get_available_quantity(self.product, destination_location),
            destination_before + 5.0,
        )
        self.assertTrue(
            all(
                move.location_id == source_location
                and move.location_dest_id == destination_location
                for move in order.stock_request_operation_picking_ids.move_ids
            )
        )
        with self.assertRaises(exceptions.UserError):
            order.with_user(destination_user).action_receive_products()

    def test_all_transfer_flows_allow_insufficient_source_stock(self):
        quant_model = self.env["stock.quant"]
        source_location = self.request_source_warehouse.lot_stock_id
        destination_location = self.warehouse.lot_stock_id
        transfer_quantity = 5.0

        def physical_quantity(product, location):
            return quant_model._get_available_quantity(
                product,
                location,
                allow_negative=True,
            )

        def create_order(flow, product):
            operation_type = "transfer" if flow == "one_step" else "request"
            return self.request_order.with_user(self.stock_request_manager).create(
                {
                    "operation_type": operation_type,
                    "source_warehouse_id": self.request_source_warehouse.id,
                    "warehouse_id": self.warehouse.id,
                    "stock_request_ids": [
                        Command.create(
                            {
                                "product_id": product.id,
                                "product_uom_id": product.uom_id.id,
                                "product_uom_qty": transfer_quantity,
                            }
                        )
                    ],
                }
            )

        def validate_two_step_picking(picking):
            picking = picking.with_user(self.stock_request_manager)
            picking.action_assign()
            for move in picking.move_ids.filtered(
                lambda move: move.state not in ("done", "cancel")
            ):
                move.quantity = move.product_uom_qty
                move.picked = True
            picking.button_validate()
            picking.invalidate_recordset(["state"])
            self.assertEqual(picking.state, "done")

        for flow in ("one_step", "two_step", "two_step_no_transit"):
            settings = self.env["res.config.settings"].create(
                {"stock_request_transfer_flow": flow}
            )
            settings.set_values()
            for initial_quantity in (2.0, 0.0, -2.0):
                case_name = f"{flow}_{initial_quantity:g}"
                with self.subTest(case=case_name):
                    product = self._create_product(
                        f"TASK2-{case_name}",
                        f"Task 2 {case_name}",
                        False,
                    )
                    if initial_quantity:
                        self._create_stock_quant(
                            product,
                            source_location,
                            initial_quantity,
                        )
                    order = create_order(flow, product)

                    if flow == "one_step":
                        order.with_user(
                            self.stock_request_manager
                        ).action_confirm()
                    else:
                        order.with_user(
                            self.stock_request_manager
                        ).action_submit()
                        order.with_user(
                            self.stock_request_manager
                        ).action_confirm()

                    if flow == "two_step":
                        delivery = order.stock_request_operation_picking_ids.filtered(
                            lambda picking: (
                                picking.stock_request_operation == "delivery"
                            )
                        )
                        receipt = order.stock_request_operation_picking_ids.filtered(
                            lambda picking: (
                                picking.stock_request_operation == "receipt"
                            )
                        )
                        validate_two_step_picking(delivery)
                        self.assertEqual(
                            physical_quantity(product, source_location),
                            initial_quantity - transfer_quantity,
                        )
                        self.assertEqual(
                            physical_quantity(product, order.transit_location_id),
                            transfer_quantity,
                        )
                        self.assertEqual(
                            physical_quantity(product, destination_location),
                            0.0,
                        )
                        validate_two_step_picking(receipt)
                    elif flow == "two_step_no_transit":
                        self.assertFalse(order.stock_request_operation_picking_ids)
                        order.with_user(
                            self.stock_request_manager
                        ).action_deliver_products()
                        self.assertFalse(order.stock_request_operation_picking_ids)
                        self.assertEqual(
                            physical_quantity(product, source_location),
                            initial_quantity,
                        )
                        self.assertEqual(
                            physical_quantity(product, destination_location),
                            0.0,
                        )
                        action = order.with_user(
                            self.stock_request_manager
                        ).action_receive_products()
                        wizard = self.env[
                            "stock.request.receive.wizard"
                        ].with_user(self.stock_request_manager).browse(
                            action["res_id"]
                        )
                        wizard.action_confirm_receipt()

                    order.invalidate_recordset()
                    self.assertEqual(order.state, "done")
                    self.assertEqual(
                        physical_quantity(product, source_location),
                        initial_quantity - transfer_quantity,
                    )
                    self.assertEqual(
                        physical_quantity(product, destination_location),
                        transfer_quantity,
                    )

    def test_close_partial_no_transit_receipt_cancels_only_remainder(self):
        self.env["res.config.settings"].create(
            {"stock_request_transfer_flow": "two_step_no_transit"}
        ).set_values()
        source_user = new_test_user(
            self.env,
            login="close_receipt_source_responsible",
            groups="stock_request.group_stock_request_warehouse_responsible",
            company_ids=[Command.link(self.main_company.id)],
        )
        destination_user = new_test_user(
            self.env,
            login="close_receipt_destination_responsible",
            groups="stock_request.group_stock_request_warehouse_responsible",
            company_ids=[Command.link(self.main_company.id)],
        )
        self.warehouse.stock_request_responsible_user_ids = [
            Command.set(source_user.ids)
        ]
        destination = self.env["stock.warehouse"].create(
            {
                "name": "Close Receipt Destination",
                "code": "CRD",
                "company_id": self.main_company.id,
                "stock_request_responsible_user_ids": [
                    Command.set(destination_user.ids)
                ],
            }
        )
        self._create_stock_quant(
            self.product,
            self.warehouse.lot_stock_id,
            5.0,
        )
        order = self.request_order.with_user(self.stock_request_user).create(
            {
                "source_warehouse_id": self.warehouse.id,
                "warehouse_id": destination.id,
                "stock_request_ids": [
                    Command.create(
                        {
                            "product_id": self.product.id,
                            "product_uom_id": self.product.uom_id.id,
                            "product_uom_qty": 5.0,
                        }
                    )
                ],
            }
        )
        source_location = self.warehouse.lot_stock_id
        destination_location = destination.lot_stock_id
        quant_model = self.env["stock.quant"]
        source_before = quant_model._get_available_quantity(
            self.product,
            source_location,
        )
        destination_before = quant_model._get_available_quantity(
            self.product,
            destination_location,
        )

        order.with_user(self.stock_request_user).action_submit()
        order.with_user(source_user).action_confirm()
        order.with_user(source_user).action_deliver_products()
        receipt_action = order.with_user(
            destination_user
        ).action_receive_products()
        receipt_wizard = self.env["stock.request.receive.wizard"].with_user(
            destination_user
        ).browse(receipt_action["res_id"])
        receipt_wizard.line_ids.quantity = 4.0
        receipt_wizard.action_confirm_receipt()

        order.invalidate_recordset()
        line = order.stock_request_ids
        line.invalidate_recordset()
        self.assertTrue(order.can_close_remaining_receipt)
        self.assertEqual(line.product_uom_qty, 5.0)
        self.assertEqual(line.delivered_qty, 5.0)
        self.assertEqual(line.received_qty, 4.0)
        self.assertEqual(line.remaining_receipt_qty, 1.0)
        picking_count = len(order.stock_request_operation_picking_ids)
        move_count = len(line.operation_move_ids)
        with self.assertRaises(exceptions.AccessError):
            line.with_user(destination_user).write({"closed_receipt_qty": 1.0})
        with self.assertRaises(exceptions.AccessError):
            order.with_user(source_user).action_close_remaining_receipt()

        close_action = order.with_user(
            destination_user
        ).action_close_remaining_receipt()
        close_wizard = self.env[
            "stock.request.close.receipt.wizard"
        ].with_user(destination_user).browse(close_action["res_id"])
        self.assertEqual(close_wizard.line_ids.received_quantity, 4.0)
        self.assertEqual(close_wizard.line_ids.remaining_quantity, 1.0)
        with self.assertRaises(exceptions.AccessError):
            close_wizard.with_user(source_user).action_confirm_close()
        close_wizard.action_confirm_close()

        order.invalidate_recordset()
        line.invalidate_recordset()
        self.assertEqual(line.product_uom_qty, 5.0)
        self.assertEqual(line.delivered_qty, 5.0)
        self.assertEqual(line.received_qty, 4.0)
        self.assertEqual(line.closed_receipt_qty, 1.0)
        self.assertEqual(line.cancelled_receipt_qty, 1.0)
        self.assertEqual(line.remaining_receipt_qty, 0.0)
        self.assertEqual(line.fulfillment_status, "partially_received")
        self.assertEqual(order.state, "done")
        self.assertFalse(order.has_pending_receipt)
        self.assertFalse(order.can_close_remaining_receipt)
        self.assertEqual(
            len(order.stock_request_operation_picking_ids), picking_count
        )
        self.assertEqual(len(line.operation_move_ids), move_count)
        self.assertEqual(
            quant_model._get_available_quantity(self.product, source_location),
            source_before - 4.0,
        )
        self.assertEqual(
            quant_model._get_available_quantity(
                self.product,
                destination_location,
            ),
            destination_before + 4.0,
        )
        chatter = " ".join(order.message_ids.mapped("body"))
        self.assertIn(destination_user.display_name, chatter)
        self.assertIn("cancelled remainder 1", chatter)

        close_wizard.action_confirm_close()
        line.invalidate_recordset()
        self.assertEqual(line.cancelled_receipt_qty, 1.0)
        self.assertEqual(len(order.stock_request_operation_picking_ids), 1)
        with self.assertRaises(exceptions.UserError):
            order.with_user(destination_user).action_receive_products()

    def test_source_review_can_revise_only_requested_quantity(self):
        self.env["res.config.settings"].create(
            {"stock_request_transfer_flow": "two_step"}
        ).set_values()
        source_user = new_test_user(
            self.env,
            login="review_quantity_source_responsible",
            groups="stock_request.group_stock_request_warehouse_responsible",
            company_ids=[Command.link(self.main_company.id)],
        )
        destination_user = new_test_user(
            self.env,
            login="review_quantity_destination_responsible",
            groups="stock_request.group_stock_request_warehouse_responsible",
            company_ids=[Command.link(self.main_company.id)],
        )
        self.warehouse.stock_request_responsible_user_ids = [
            Command.set(source_user.ids)
        ]
        destination = self.env["stock.warehouse"].create(
            {
                "name": "Review Quantity Destination",
                "code": "RQD",
                "company_id": self.main_company.id,
                "stock_request_responsible_user_ids": [
                    Command.set(destination_user.ids)
                ],
            }
        )
        order = self.request_order.with_user(self.stock_request_user).create(
            {
                "source_warehouse_id": self.warehouse.id,
                "warehouse_id": destination.id,
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
        line = order.stock_request_ids
        line.with_user(self.stock_request_user).write(
            {"product_uom_qty": 4.0}
        )
        line.with_user(self.stock_request_user).write(
            {"product_uom_qty": 3.0}
        )
        order.with_user(self.stock_request_user).action_submit()

        with self.assertRaises(exceptions.AccessError):
            line.with_user(self.stock_request_user).write(
                {"product_uom_qty": 2.0}
            )
        with self.assertRaises(exceptions.AccessError):
            line.with_user(destination_user).write({"product_uom_qty": 2.0})
        with self.assertRaises(exceptions.AccessError):
            line.with_user(source_user).write({"product_id": self.product.id})
        with self.assertRaises(exceptions.AccessError):
            line.with_user(self.stock_request_manager).write(
                {"product_id": self.product.id}
            )
        with self.assertRaises(exceptions.UserError):
            line.with_user(self.stock_request_manager).unlink()
        with self.assertRaises(exceptions.ValidationError):
            order.with_user(source_user).write(
                {
                    "stock_request_ids": [
                        Command.update(line.id, {"product_uom_qty": 0.0})
                    ]
                }
            )

        order.with_user(source_user).write(
            {
                "stock_request_ids": [
                    Command.update(line.id, {"product_uom_qty": 2.0})
                ]
            }
        )
        line.invalidate_recordset()
        self.assertEqual(line.product_uom_qty, 2.0)
        chatter = " ".join(order.message_ids.mapped("body"))
        self.assertIn(source_user.display_name, chatter)
        self.assertIn(self.product.display_name, chatter)
        self.assertIn("previous quantity 3", chatter)
        self.assertIn("revised quantity 2", chatter)

        quantity_change_messages = order.message_ids.filtered(
            lambda message: "revised the requested quantity" in str(message.body)
        )
        order.with_user(self.stock_request_manager).write(
            {
                "stock_request_ids": [
                    Command.update(line.id, {"product_uom_qty": 2.0})
                ]
            }
        )
        self.assertEqual(
            len(
                order.message_ids.filtered(
                    lambda message: "revised the requested quantity"
                    in str(message.body)
                )
            ),
            len(quantity_change_messages),
        )

        order.with_user(source_user).action_confirm()
        order.invalidate_recordset()
        line.invalidate_recordset()
        self.assertEqual(order.state, "ready")
        self.assertEqual(line.product_uom_qty, 2.0)
        operation_moves = order.stock_request_operation_picking_ids.move_ids
        self.assertEqual(len(operation_moves), 2)
        self.assertTrue(
            all(move.product_uom_qty == 2.0 for move in operation_moves)
        )
        self.assertFalse(order.stock_request_operation_picking_ids.backorder_ids)
        with self.assertRaises(exceptions.AccessError):
            line.with_user(source_user).write({"product_uom_qty": 1.0})
        with self.assertRaises(exceptions.AccessError):
            line.with_user(self.stock_request_manager).write(
                {"product_uom_qty": 1.0}
            )

        form_arch = self.env.ref(
            "stock_request.stock_request_order_form"
        ).get_combined_arch()
        line_list = form_arch.xpath(
            "//field[@name='stock_request_ids']/list"
        )[0]
        requested_quantity = line_list.xpath(
            "./field[@name='product_uom_qty']"
        )[0]
        self.assertIn(
            "parent.is_source_warehouse_responsible",
            requested_quantity.get("readonly"),
        )
        self.assertEqual(
            line_list.xpath("./field[@name='product_id']")[0].get("readonly"),
            "parent.state != 'draft'",
        )
        self.assertEqual(
            line_list.xpath("./control/create")[0].get("invisible"),
            "parent.state != 'draft'",
        )
        self.assertEqual(
            line_list.xpath("./control/delete")[0].get("invisible"),
            "parent.state != 'draft'",
        )


class TestStockRequestOrderState(TestStockRequest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product_a = cls._create_product(
            "CODEA",
            "Product A",
            cls.main_company.id,
        )
        cls.product_a.route_ids = [Command.set(cls.route.ids)]
        cls.product_b = cls._create_product(
            "CODEB",
            "Product B",
            cls.main_company.id,
        )
        cls.product_b.route_ids = [Command.set(cls.route.ids)]
        expected_date = fields.Datetime.now()
        vals = {
            "company_id": cls.main_company.id,
            "source_warehouse_id": cls.request_source_warehouse.id,
            "warehouse_id": cls.warehouse.id,
            "location_id": cls.warehouse.lot_stock_id.id,
            "expected_date": expected_date,
            "stock_request_ids": [
                Command.create(
                    {
                        "product_id": cls.product_a.id,
                        "product_uom_id": cls.product_a.uom_id.id,
                        "product_uom_qty": 1.0,
                        "company_id": cls.main_company.id,
                        "warehouse_id": cls.warehouse.id,
                        "location_id": cls.warehouse.lot_stock_id.id,
                        "expected_date": expected_date,
                    },
                ),
                Command.create(
                    {
                        "product_id": cls.product_b.id,
                        "product_uom_id": cls.product_b.uom_id.id,
                        "product_uom_qty": 1.0,
                        "company_id": cls.main_company.id,
                        "warehouse_id": cls.warehouse.id,
                        "location_id": cls.warehouse.lot_stock_id.id,
                        "expected_date": expected_date,
                    },
                ),
            ],
        }
        cls.order = cls.request_order.create(vals)
        cls.request_a = cls.order.stock_request_ids.filtered(
            lambda x: x.product_id == cls.product_a
        )
        cls.request_b = cls.order.stock_request_ids.filtered(
            lambda x: x.product_id == cls.product_b
        )

    def test_simple_workflow_state_selection(self):
        self.assertEqual(
            [value for value, label in self.order._fields["state"].selection],
            [
                "draft",
                "submitted",
                "ready",
                "in_transit",
                "done",
                "rejected",
                "cancelled",
            ],
        )

    def test_request_must_be_submitted_before_acceptance(self):
        with self.assertRaises(exceptions.UserError):
            self.order.with_user(self.stock_request_manager).action_confirm()

        self.order.action_submit()
        self.order.with_user(self.stock_request_manager).action_confirm()

        self.assertEqual(self.order.state, "ready")
        self.assertEqual(len(self.order.picking_ids), 2)

    def test_no_manual_done_button(self):
        order_arch = self.env.ref(
            "stock_request.stock_request_order_form"
        ).get_combined_arch()

        self.assertFalse(order_arch.xpath("//button[@name='action_done']"))

    def test_order_derives_technical_fields_from_warehouses(self):
        destination = self.env["stock.warehouse"].create(
            {
                "name": "Task 04 Destination",
                "code": "T04",
                "company_id": self.main_company.id,
                "resupply_wh_ids": [Command.set(self.warehouse.ids)],
            }
        )
        order = self.request_order.create(
            {
                "company_id": self.main_company.id,
                "source_warehouse_id": self.warehouse.id,
                "warehouse_id": destination.id,
                "reason": "Replenish the destination warehouse",
                "stock_request_ids": [
                    Command.create(
                        {
                            "product_id": self.product.id,
                            "product_uom_id": self.product.uom_id.id,
                            "product_uom_qty": 5.0,
                        }
                    )
                ],
            }
        )

        self.assertEqual(order.location_id, destination.lot_stock_id)
        self.assertEqual(order.source_location_id, self.warehouse.lot_stock_id)
        self.assertEqual(order.route_id.supplier_wh_id, self.warehouse)
        self.assertEqual(order.route_id.supplied_wh_id, destination)
        self.assertEqual(order.stock_request_ids.route_id, order.route_id)
        self.assertEqual(order.stock_request_ids.location_id, destination.lot_stock_id)
        self.assertEqual(
            order.stock_request_ids.source_location_id,
            self.warehouse.lot_stock_id,
        )
