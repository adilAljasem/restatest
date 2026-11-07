# Copyright 2017-2020 ForgeFlow, S.L.
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl.html).

from markupsafe import Markup
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import float_compare


class StockRequest(models.Model):
    _name = "stock.request"
    _description = "Stock Request"
    _inherit = "stock.request.abstract"
    _order = "id desc"

    def _get_default_requested_by(self):
        return self.env["res.users"].browse(self.env.uid)

    @staticmethod
    def _get_expected_date():
        return fields.Datetime.now()

    name = fields.Char()
    state = fields.Selection(
        selection=[
            ("draft", "Draft"),
            ("open", "In progress"),
            ("done", "Done"),
            ("cancel", "Cancelled"),
        ],
        string="Status",
        copy=False,
        default="draft",
        index=True,
        readonly=True,
        tracking=True,
    )
    requested_by = fields.Many2one(
        "res.users",
        required=True,
        tracking=True,
        default=lambda s: s._get_default_requested_by(),
    )
    expected_date = fields.Datetime(
        index=True,
        required=True,
        help="Date when you expect to receive the goods.",
    )
    move_ids = fields.One2many(
        comodel_name="stock.move",
        compute="_compute_move_ids",
        string="Stock Moves",
        readonly=True,
    )
    picking_ids = fields.One2many(
        "stock.picking",
        compute="_compute_picking_ids",
        string="Pickings",
        readonly=True,
    )
    qty_in_progress = fields.Float(
        digits="Product Unit of Measure",
        readonly=True,
        compute="_compute_qty",
        store=True,
        help="Quantity in progress.",
    )
    qty_done = fields.Float(
        digits="Product Unit of Measure",
        readonly=True,
        compute="_compute_qty",
        store=True,
        help="Quantity completed",
    )
    qty_cancelled = fields.Float(
        digits="Product Unit of Measure",
        readonly=True,
        compute="_compute_qty",
        store=True,
        help="Quantity cancelled",
    )
    picking_count = fields.Integer(
        string="Delivery Orders",
        compute="_compute_picking_ids",
        readonly=True,
    )
    operation_move_ids = fields.One2many(
        comodel_name="stock.move",
        inverse_name="stock_request_line_id",
        string="Operational Stock Moves",
        readonly=True,
        copy=False,
    )
    delivered_qty = fields.Float(
        string="Delivered Quantity",
        digits="Product Unit of Measure",
        compute="_compute_transfer_quantities",
        store=True,
        readonly=True,
    )
    received_qty = fields.Float(
        string="Received Quantity",
        digits="Product Unit of Measure",
        compute="_compute_transfer_quantities",
        store=True,
        readonly=True,
    )
    cancelled_delivery_qty = fields.Float(
        string="Cancelled Delivery Quantity",
        digits="Product Unit of Measure",
        compute="_compute_transfer_quantities",
        store=True,
        readonly=True,
    )
    cancelled_receipt_qty = fields.Float(
        string="Cancelled Receipt Quantity",
        digits="Product Unit of Measure",
        compute="_compute_transfer_quantities",
        store=True,
        readonly=True,
    )
    closed_receipt_qty = fields.Float(
        string="Closed Receipt Quantity",
        digits="Product Unit of Measure",
        default=0.0,
        readonly=True,
        copy=False,
        help="Receipt quantity explicitly cancelled when the remaining receipt is closed.",
    )
    remaining_delivery_qty = fields.Float(
        string="Remaining to Deliver",
        digits="Product Unit of Measure",
        compute="_compute_transfer_quantities",
        store=True,
        readonly=True,
    )
    remaining_receipt_qty = fields.Float(
        string="Remaining to Receive",
        digits="Product Unit of Measure",
        compute="_compute_transfer_quantities",
        store=True,
        readonly=True,
    )
    fulfillment_status = fields.Selection(
        selection=[
            ("pending", "Pending"),
            ("fully_received", "Fully Received"),
            ("partially_received", "Partially Received"),
            ("not_fulfilled", "Not Fulfilled"),
        ],
        string="Fulfillment Status",
        compute="_compute_fulfillment_status",
        store=True,
        readonly=True,
        index=True,
    )
    operation_type = fields.Selection(
        related="order_id.operation_type",
        string="Operation Type",
        store=True,
        readonly=True,
    )
    request_date = fields.Date(
        related="order_id.request_date",
        string="Request Date",
        store=True,
        index=True,
        readonly=True,
    )
    source_warehouse_id = fields.Many2one(
        related="order_id.source_warehouse_id",
        string="Source Warehouse",
        store=True,
        readonly=True,
    )
    transit_location_id = fields.Many2one(
        related="order_id.transit_location_id",
        string="Transit Location",
        store=True,
        readonly=True,
    )
    delivery_date = fields.Datetime(
        related="order_id.delivery_date",
        string="Delivery Date",
        store=True,
        readonly=True,
    )
    delivered_by_id = fields.Many2one(
        related="order_id.delivered_by_id",
        string="Delivered By",
        store=True,
        readonly=True,
    )
    operation_state = fields.Selection(
        related="order_id.state",
        string="Operation Status",
        store=True,
        readonly=True,
    )
    product_category_id = fields.Many2one(
        related="product_id.categ_id",
        string="Product Category",
        store=True,
        readonly=True,
    )
    in_transit_qty = fields.Float(
        string="In Transit Quantity",
        digits="Product Unit of Measure",
        compute="_compute_in_transit_qty",
        store=True,
        index=True,
        readonly=True,
    )
    days_in_transit = fields.Integer(
        string="Days in Transit",
        compute="_compute_in_transit_report_timing",
        readonly=True,
    )
    overdue = fields.Boolean(
        string="Overdue",
        compute="_compute_in_transit_report_timing",
        search="_search_overdue",
        readonly=True,
    )
    allocation_ids = fields.One2many(
        comodel_name="stock.request.allocation",
        inverse_name="stock_request_id",
        string="Stock Request Allocation",
    )
    order_id = fields.Many2one("stock.request.order", readonly=True)
    warehouse_id = fields.Many2one()
    location_id = fields.Many2one()
    product_id = fields.Many2one(
        domain="[('type', 'in', ['product', 'consu']), ('stock_request_is_kit', '=', False)]",
        context={"stock_request_hide_kits": True},
    )
    product_uom_id = fields.Many2one()
    product_uom_qty = fields.Float(default=1.0)
    available_qty = fields.Float(
        string="Available Qty",
        digits="Product Unit of Measure",
        compute="_compute_available_qty",
        readonly=True,
        help="Unreserved quantity currently available in the source warehouse, "
        "expressed in the line UOM.",
    )
    procurement_group_id = fields.Many2one()
    company_id = fields.Many2one()
    route_id = fields.Many2one()
    _name_uniq = models.Constraint(
        "unique(name, company_id)",
        "Stock Request name must be unique",
    )

    @api.depends("product_id", "product_uom_id", "source_location_id")
    def _compute_available_qty(self):
        self.available_qty = 0.0
        lines = self.filtered(
            lambda line: line.product_id and line.source_location_id
        )
        lines_by_location = {}
        for line in lines:
            location_lines = lines_by_location.setdefault(
                line.source_location_id.id, self.env["stock.request"]
            )
            lines_by_location[line.source_location_id.id] = location_lines | line

        for location_id, location_lines in lines_by_location.items():
            products = location_lines.mapped("product_id").with_context(
                location=location_id
            )
            free_qty_by_product = dict(zip(products.ids, products.mapped("free_qty")))
            for line in location_lines:
                available_qty = free_qty_by_product.get(line.product_id.id, 0.0)
                if line.product_uom_id:
                    available_qty = line.product_id.uom_id._compute_quantity(
                        available_qty,
                        line.product_uom_id,
                    )
                line.available_qty = available_qty

    @api.constrains("state", "product_qty")
    def _check_qty(self):
        for rec in self:
            if rec.state == "draft" and rec.product_qty <= 0:
                raise ValidationError(
                    _("Stock Request product quantity has to be strictly positive.")
                )
            elif rec.state != "draft" and rec.product_qty < 0:
                raise ValidationError(
                    _("Stock Request product quantity cannot be negative.")
                )

    def _get_all_origin_moves(self, move):
        # Move origins form a graph: returns/custom routes can contain cycles.
        # Visit each move once without using Python's recursion stack.
        all_moves = move.browse()
        pending = move
        while pending:
            all_moves |= pending
            pending = pending.mapped("move_orig_ids") - all_moves
        return all_moves

    @api.depends("allocation_ids", "allocation_ids.stock_move_id")
    def _compute_move_ids(self):
        for request in self:
            move_ids = request.allocation_ids.mapped("stock_move_id")
            all_moves = self.env["stock.move"]
            for move in move_ids:
                all_moves |= self._get_all_origin_moves(move)
            request.move_ids = all_moves

    @api.depends(
        "allocation_ids",
        "allocation_ids.stock_move_id",
        "allocation_ids.stock_move_id.picking_id",
    )
    def _compute_picking_ids(self):
        for request in self:
            request.picking_count = 0
            request.picking_ids = self.env["stock.picking"]
            request.picking_ids = request.move_ids.filtered(
                lambda m: m.state != "cancel"
            ).mapped("picking_id")
            request.picking_count = len(request.picking_ids)

    @api.depends(
        "allocation_ids",
        "allocation_ids.stock_move_id.state",
        "allocation_ids.stock_move_id.move_line_ids",
        "allocation_ids.stock_move_id.move_line_ids.quantity",
    )
    def _compute_qty(self):
        for request in self:
            incoming_qty = 0.0
            other_qty = 0.0
            for allocation in request.allocation_ids:
                if allocation.stock_move_id.picking_code == "incoming":
                    incoming_qty += allocation.allocated_product_qty
                else:
                    other_qty += allocation.allocated_product_qty
            done_qty = abs(other_qty - incoming_qty)
            open_qty = sum(request.allocation_ids.mapped("open_product_qty"))
            uom = request.product_id.uom_id
            request.qty_done = uom._compute_quantity(
                done_qty,
                request.product_uom_id,
                rounding_method="HALF-UP",
            )
            request.qty_in_progress = uom._compute_quantity(
                open_qty,
                request.product_uom_id,
                rounding_method="HALF-UP",
            )
            request.qty_cancelled = (
                max(
                    0,
                    uom._compute_quantity(
                        request.product_qty - done_qty - open_qty,
                        request.product_uom_id,
                        rounding_method="HALF-UP",
                    ),
                )
                if request.allocation_ids
                else 0
            )

    @api.depends(
        "product_uom_qty",
        "product_uom_id",
        "closed_receipt_qty",
        "order_id.state",
        "order_id.transfer_flow",
        "operation_move_ids.state",
        "operation_move_ids.quantity",
        "operation_move_ids.product_uom_qty",
        "operation_move_ids.product_uom",
        "operation_move_ids.picking_id.stock_request_operation",
    )
    def _compute_transfer_quantities(self):
        for request in self:
            delivered_qty = (
                request.product_uom_qty
                if request.order_id.transfer_flow == "two_step_no_transit"
                and request.order_id.state in ("in_transit", "done")
                else 0.0
            )
            received_qty = 0.0
            cancelled_delivery_qty = 0.0
            cancelled_receipt_qty = request.closed_receipt_qty
            for move in request.operation_move_ids:
                operation = move.picking_id.stock_request_operation
                if operation not in ("delivery", "receipt", "transfer"):
                    continue
                done_qty = 0.0
                cancelled_qty = 0.0
                if move.state == "done":
                    done_qty = move.product_uom._compute_quantity(
                        move.quantity,
                        request.product_uom_id,
                        rounding_method="HALF-UP",
                    )
                    unprocessed_qty = max(
                        move.product_uom_qty - move.quantity,
                        0.0,
                    )
                    cancelled_qty = move.product_uom._compute_quantity(
                        unprocessed_qty,
                        request.product_uom_id,
                        rounding_method="HALF-UP",
                    )
                elif move.state == "cancel":
                    cancelled_qty = move.product_uom._compute_quantity(
                        move.product_uom_qty,
                        request.product_uom_id,
                        rounding_method="HALF-UP",
                    )
                if operation in ("delivery", "transfer"):
                    delivered_qty += done_qty
                    cancelled_delivery_qty += cancelled_qty
                if operation in ("receipt", "transfer"):
                    received_qty += done_qty
                    cancelled_receipt_qty += cancelled_qty

            request.delivered_qty = delivered_qty
            request.received_qty = received_qty
            request.cancelled_delivery_qty = cancelled_delivery_qty
            request.cancelled_receipt_qty = cancelled_receipt_qty
            request.remaining_delivery_qty = max(
                request.product_uom_qty
                - delivered_qty
                - cancelled_delivery_qty,
                0.0,
            )
            request.remaining_receipt_qty = max(
                delivered_qty - received_qty - cancelled_receipt_qty,
                0.0,
            )

    @api.depends(
        "product_uom_qty",
        "product_uom_id",
        "received_qty",
        "cancelled_delivery_qty",
        "cancelled_receipt_qty",
        "remaining_delivery_qty",
        "remaining_receipt_qty",
        "order_id.state",
    )
    def _compute_fulfillment_status(self):
        for request in self:
            rounding = request.product_uom_id.rounding or 0.01
            received_comparison = float_compare(
                request.received_qty,
                request.product_uom_qty,
                precision_rounding=rounding,
            )
            if received_comparison >= 0:
                request.fulfillment_status = "fully_received"
            elif (
                float_compare(
                    request.received_qty,
                    0.0,
                    precision_rounding=rounding,
                )
                > 0
            ):
                request.fulfillment_status = "partially_received"
            elif request.order_id.state in ("done", "rejected", "cancelled") or (
                (
                    request.cancelled_delivery_qty > 0
                    or request.cancelled_receipt_qty > 0
                )
                and float_compare(
                    request.remaining_delivery_qty,
                    0.0,
                    precision_rounding=rounding,
                )
                <= 0
                and float_compare(
                    request.remaining_receipt_qty,
                    0.0,
                    precision_rounding=rounding,
                )
                <= 0
            ):
                request.fulfillment_status = "not_fulfilled"
            else:
                request.fulfillment_status = "pending"

    @api.depends(
        "delivered_qty",
        "received_qty",
        "order_id.transfer_flow",
    )
    def _compute_in_transit_qty(self):
        for request in self:
            request.in_transit_qty = (
                0.0
                if request.order_id.transfer_flow == "two_step_no_transit"
                else max(
                    request.delivered_qty - request.received_qty,
                    0.0,
                )
            )

    @api.depends("in_transit_qty", "delivery_date", "expected_date")
    @api.depends_context("tz")
    def _compute_in_transit_report_timing(self):
        for request in self:
            today = fields.Date.context_today(request)
            delivery_day = (
                fields.Datetime.context_timestamp(request, request.delivery_date).date()
                if request.delivery_date
                else False
            )
            request.days_in_transit = (
                max((today - delivery_day).days, 0)
                if delivery_day and request.in_transit_qty > 0
                else 0
            )
            expected_day = (
                fields.Datetime.context_timestamp(request, request.expected_date).date()
                if request.expected_date
                else False
            )
            request.overdue = bool(
                expected_day
                and expected_day < today
                and request.in_transit_qty > 0
            )

    def _search_overdue(self, operator, value):
        if operator not in ("=", "!="):
            raise NotImplementedError(
                _("Overdue can only be searched with equals or not equals.")
            )
        overdue_requested = bool(value)
        if operator == "!=":
            overdue_requested = not overdue_requested
        today_start = fields.Datetime.to_datetime(fields.Date.context_today(self))
        if overdue_requested:
            return [
                ("expected_date", "<", today_start),
                ("in_transit_qty", ">", 0),
            ]
        return [
            "|",
            ("expected_date", ">=", today_start),
            ("in_transit_qty", "<=", 0),
        ]

    def action_open_stock_request_order(self):
        self.ensure_one()
        if not self.order_id:
            raise UserError(_("No related stock request order was found."))
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "stock_request.stock_request_order_action"
        )
        action["views"] = [
            (self.env.ref("stock_request.stock_request_order_form").id, "form")
        ]
        action["res_id"] = self.order_id.id
        return action

    def _action_open_report_pickings(self, operation):
        self.ensure_one()
        pickings = self.order_id.stock_request_operation_picking_ids.filtered(
            lambda picking: picking.stock_request_operation == operation
        )
        if not pickings:
            raise UserError(_("No related transfer was found."))
        if self.env.user.has_group("stock.group_stock_user"):
            return self.order_id._action_view_operation_pickings(operation)
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "stock_request.stock_request_in_transit_picking_action"
        )
        action["domain"] = [("id", "in", pickings.ids)]
        if len(pickings) == 1:
            action["views"] = [
                (
                    self.env.ref(
                        "stock_request.stock_request_in_transit_picking_form"
                    ).id,
                    "form",
                )
            ]
            action["res_id"] = pickings.id
        return action

    def action_open_delivery_transfers(self):
        return self._action_open_report_pickings("delivery")

    def action_open_receipt_transfers(self):
        return self._action_open_report_pickings("receipt")

    @api.constrains("order_id", "requested_by")
    def check_order_requested_by(self):
        for stock_request in self:
            if (
                stock_request.order_id
                and stock_request.order_id.requested_by != stock_request.requested_by
            ):
                raise ValidationError(_("Requested by must be equal to the order"))

    @api.constrains("order_id", "warehouse_id")
    def check_order_warehouse_id(self):
        for stock_request in self:
            if (
                stock_request.order_id
                and stock_request.order_id.warehouse_id != stock_request.warehouse_id
            ):
                raise ValidationError(_("Warehouse must be equal to the order"))

    @api.constrains("order_id", "location_id")
    def check_order_location(self):
        for stock_request in self:
            if (
                stock_request.order_id
                and stock_request.order_id.location_id != stock_request.location_id
            ):
                raise ValidationError(_("Location must be equal to the order"))

    @api.constrains("order_id", "source_location_id")
    def check_order_source_location(self):
        for stock_request in self:
            if (
                stock_request.order_id
                and stock_request.order_id.source_location_id
                != stock_request.source_location_id
            ):
                raise ValidationError(
                    _("Source location must be equal to the order")
                )

    @api.constrains("order_id", "procurement_group_id")
    def check_order_procurement_group(self):
        for stock_request in self:
            if (
                stock_request.order_id
                and stock_request.order_id.procurement_group_id
                != stock_request.procurement_group_id
            ):
                raise ValidationError(_("Procurement group must be equal to the order"))

    @api.constrains("order_id", "company_id")
    def check_order_company(self):
        for stock_request in self:
            if (
                stock_request.order_id
                and stock_request.order_id.company_id != stock_request.company_id
            ):
                raise ValidationError(_("Company must be equal to the order"))

    @api.constrains("order_id", "expected_date")
    def check_order_expected_date(self):
        for stock_request in self:
            if (
                stock_request.order_id
                and stock_request.order_id.expected_date != stock_request.expected_date
            ):
                raise ValidationError(_("Expected date must be equal to the order"))

    def _action_confirm(self):
        order_requests = self.filtered("order_id")
        if self - order_requests:
            raise UserError(
                _(
                    "Standalone Stock Request lines cannot be confirmed. "
                    "Use the main Stock Request screen."
                )
            )
        for order in order_requests.mapped("order_id"):
            order._create_internal_transfers(order_requests)
        self.filtered(lambda x: x.state != "done").sudo().write({"state": "open"})

    def action_confirm(self):
        orders = self.mapped("order_id")
        orders._check_source_responsible_access()
        orders._check_confirmation_state()
        self._action_confirm()
        return True

    def action_draft(self):
        if self.filtered("order_id") and not self.env.su:
            raise AccessError(
                _("Stock Request lines cannot be reset manually.")
            )
        self.sudo().write({"state": "draft"})
        return True

    def action_cancel(self):
        order_lines = self.filtered("order_id")
        if order_lines and not (
            self.env.su
            or self.env.user.has_group("stock_request.group_stock_request_manager")
        ):
            invalid = order_lines.filtered(
                lambda request: request.order_id.state != "draft"
                or request.order_id.requested_by != self.env.user
            )
            if invalid:
                raise AccessError(
                    _("Only the requester can cancel their own Draft request.")
                )
        pending_moves = self.sudo().mapped("move_ids").filtered(
            lambda move: move.state not in ("done", "cancel")
        )
        pending_moves._action_cancel()
        self.filtered(lambda request: request.state != "done").sudo().write(
            {"state": "cancel"}
        )
        return True

    def action_done(self):
        if self.filtered("order_id") and not self.env.su:
            raise AccessError(
                _("Stock Request lines are completed by stock movements only.")
            )
        self.sudo().write({"state": "done"})
        return True

    def check_cancel(self):
        for request in self:
            if request._check_cancel_allocation():
                request.write({"state": "cancel"})

    def check_done(self):
        precision = self.env["decimal.precision"].precision_get(
            "Product Unit of Measure"
        )
        for request in self:
            allocated_qty = sum(request.allocation_ids.mapped("allocated_product_qty"))
            qty_done = request.product_id.uom_id._compute_quantity(
                allocated_qty, request.product_uom_id
            )
            if (
                float_compare(
                    qty_done, request.product_uom_qty, precision_digits=precision
                )
                >= 0
            ):
                request.action_done()
            elif request._check_cancel_allocation():
                # If qty_done=0 and qty_cancelled>0 it's cancelled
                request.write({"state": "cancel"})
        return True

    def _check_cancel_allocation(self):
        precision = self.env["decimal.precision"].precision_get(
            "Product Unit of Measure"
        )
        self.ensure_one()
        return (
            self.allocation_ids
            and float_compare(self.qty_cancelled, 0, precision_digits=precision) > 0
        )

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and any(
            "closed_receipt_qty" in vals for vals in vals_list
        ):
            raise AccessError(
                _(
                    "Closed receipt quantities can only be changed by "
                    "the receipt closure action."
                )
            )
        if not self.env.su and any(not vals.get("order_id") for vals in vals_list):
            raise UserError(
                _(
                    "Stock request lines can only be created from the main Stock "
                    "Request screen."
                )
            )
        vals_list_upd = []
        for vals in vals_list:
            upd_vals = vals.copy()
            if upd_vals.get("name", "/") == "/":
                upd_vals["name"] = self.env["ir.sequence"].next_by_code("stock.request")
            if "order_id" in upd_vals:
                order = self.env["stock.request.order"].browse(
                    upd_vals["order_id"]
                )
                if order.transfer_flow == "two_step_no_transit" and order.state == "in_transit":
                    order.lock_for_update(allow_referencing=True)
                    order.invalidate_recordset(["state"])
                reviewing = order.operation_type == "request" and order.state == "submitted"
                requester_edit = order._can_requester_edit_no_transit_lines()
                if reviewing:
                    upd_vals["state"] = "draft"
                if requester_edit and order.state in ("ready", "in_transit"):
                    upd_vals["state"] = "open"
                if not self.env.su and reviewing and not requester_edit:
                    order._check_source_responsible_access()
                if not self.env.su and order.state != "draft" and not reviewing and not requester_edit:
                    raise AccessError(
                        _(
                            "Products can only be added while the Stock "
                            "Request is Draft."
                        )
                    )
                if not self.env.su and not reviewing and not requester_edit and not self.env.user.has_group(
                    "stock_request.group_stock_request_manager"
                ) and (
                    order.requested_by != self.env.user
                    or order.state != "draft"
                ):
                    raise AccessError(
                        _(
                            "You can only add products to your own Stock "
                            "Request while it is Draft."
                        )
                    )
                upd_vals.update(
                    {
                        "warehouse_id": order.warehouse_id.id,
                        "location_id": order.location_id.id,
                        "source_location_id": order.source_location_id.id,
                        "company_id": order.company_id.id,
                        "expected_date": order.expected_date,
                        "requested_by": order.requested_by.id,
                        "procurement_group_id": order.procurement_group_id.id,
                        "route_id": order.route_id.id,
                    }
                )
            else:
                upd_vals["expected_date"] = self._get_expected_date()
            vals_list_upd.append(upd_vals)
        lines = super().create(vals_list_upd)
        # Check resolved values as well as explicit input (including defaults
        # supplied by imports, templates or RPC context).
        for line in lines:
            self._check_new_kit_product(line.product_id.id, line.company_id.id)
        return lines

    @api.model
    def _check_new_kit_product(self, product_id, company_id):
        if not product_id:
            return
        product = self.env["product.product"].with_context(
            stock_request_hide_kits=False,
            stock_request_company_id=company_id or self.env.company.id
        ).browse(product_id)
        if product.filtered_domain(product._stock_request_kit_domain()):
            raise ValidationError(_("Kit products cannot be added to stock requests or transfers. Add their components instead."))

    def _check_in_transit_item_change(self, vals=None, deleting=False):
        """Preserve stock receipts when revising an administrative delivery."""
        vals = vals or {}
        lines = self.filtered(lambda line: line.order_id.transfer_flow == "two_step_no_transit"
                              and line.order_id.state == "in_transit")
        if not lines:
            return
        # Receipt creation locks the same order, so an edit cannot race it.
        orders = lines.mapped("order_id")
        orders.lock_for_update(allow_referencing=True)
        orders.invalidate_recordset(["state"])
        lines.invalidate_recordset(["operation_move_ids", "received_qty",
                                    "cancelled_receipt_qty", "closed_receipt_qty"])
        for line in lines:
            active_moves = line.operation_move_ids.filtered(lambda move: move.state != "cancel")
            if active_moves.filtered(lambda move: move.state != "done"):
                raise UserError(_("Finish or cancel the pending receipt before changing this line."))
            identity_changed = (
                "product_id" in vals and vals["product_id"] != line.product_id.id
            ) or (
                "product_uom_id" in vals and vals["product_uom_id"] != line.product_uom_id.id
            )
            if (deleting or identity_changed) and (active_moves or line.closed_receipt_qty):
                raise UserError(_("A line with a recorded receipt or receipt closure cannot be deleted or have its product or unit changed."))
            if "product_uom_qty" in vals and float_compare(
                vals["product_uom_qty"],
                line.received_qty + line.cancelled_receipt_qty,
                precision_rounding=line.product_uom_id.rounding,
            ) < 0:
                raise UserError(_("Requested Quantity cannot be less than the received and closed receipt quantities."))

    def write(self, vals):
        if set(vals) & {"product_id", "product_uom_id", "product_uom_qty"}:
            self._check_in_transit_item_change(vals)
        # Existing Kit lines remain valid; only a newly selected product is checked.
        if vals.get("order_id"):
            for line in self.filtered(lambda line: line.order_id.id != vals["order_id"]):
                order = self.env["stock.request.order"].browse(vals["order_id"])
                self._check_new_kit_product(vals.get("product_id", line.product_id.id), order.company_id.id)
        if "product_id" in vals:
            for line in self.filtered(lambda line: line.product_id.id != vals["product_id"]):
                self._check_new_kit_product(vals["product_id"], vals.get("company_id", line.company_id.id))
        review_quantity_edit = False
        review_quantity_values = []
        requester_item_edit = bool(self) and set(vals) <= {"product_id", "product_uom_id", "product_uom_qty"} and all(
            line.order_id and line.order_id._can_requester_edit_no_transit_lines() for line in self
        )
        if not self.env.su and not requester_item_edit:
            if "closed_receipt_qty" in vals:
                raise AccessError(
                    _(
                        "Closed receipt quantities can only be changed by "
                        "the receipt closure action."
                    )
                )
            if "state" in vals:
                raise AccessError(
                    _(
                        "Stock Request line status can only be changed by "
                        "workflow actions."
                    )
                )
            submitted_lines = self.filtered(
                lambda request: request.order_id
                and request.order_id.state == "submitted"
            )
            if submitted_lines:
                if submitted_lines != self or set(vals) != {"product_uom_qty"}:
                    raise AccessError(
                        _(
                            "Only the Requested Quantity can be changed while "
                            "a Stock Request is waiting for source warehouse "
                            "review."
                        )
                    )
                submitted_lines.mapped(
                    "order_id"
                )._check_source_responsible_access()
                review_quantity_edit = True
                review_quantity_values = [
                    (request, request.product_uom_qty)
                    for request in submitted_lines
                ]
            elif "product_uom_qty" in vals and self.filtered(
                lambda request: request.order_id
                and request.order_id.state != "draft"
            ):
                raise AccessError(
                    _(
                        "Requested Quantity cannot be changed after the Stock "
                        "Request has been accepted or otherwise processed."
                    )
                )
            if not self.env.user.has_group(
                "stock_request.group_stock_request_manager"
            ) and not review_quantity_edit:
                invalid = self.filtered(
                    lambda request: not request.order_id
                    or request.order_id.requested_by != self.env.user
                    or request.order_id.state != "draft"
                )
                if invalid:
                    raise AccessError(
                        _(
                            "You can only modify products on your own Stock "
                            "Request while it is Draft."
                        )
                    )
        result = super().write(vals)
        if review_quantity_edit:
            changes_by_order = {}
            for request, previous_quantity in review_quantity_values:
                if (
                    float_compare(
                        request.product_uom_qty,
                        previous_quantity,
                        precision_rounding=request.product_uom_id.rounding,
                    )
                    == 0
                ):
                    continue
                changes_by_order.setdefault(request.order_id, []).append(
                    (request, previous_quantity, request.product_uom_qty)
                )
            for order, changes in changes_by_order.items():
                details = Markup()
                for request, previous_quantity, revised_quantity in changes:
                    details += Markup("<li>%s</li>") % _(
                        "%(product)s: previous quantity %(previous)s %(uom)s; "
                        "revised quantity %(revised)s %(uom)s.",
                        product=request.product_id.display_name,
                        previous=previous_quantity,
                        revised=revised_quantity,
                        uom=request.product_uom_id.display_name,
                    )
                order.sudo().message_post(
                    body=(
                        Markup("<p>%s</p>")
                        % _(
                            "%(user)s revised the requested quantity during "
                            "source warehouse review.",
                            user=self.env.user.display_name,
                        )
                        + Markup("<ul>%s</ul>") % details
                    ),
                    author_id=self.env.user.partner_id.id,
                    subtype_xmlid="mail.mt_note",
                )
        return result

    def unlink(self):
        self._check_in_transit_item_change(deleting=True)
        requester_lines = self.filtered(
            lambda line: line.order_id and line.order_id._can_requester_edit_no_transit_lines()
        )
        if requester_lines:
            # Keep ORM ACLs and company/warehouse record rules in force.
            other_lines = self - requester_lines
            if other_lines:
                other_lines.unlink()
            return super(StockRequest, requester_lines).unlink()
        if self.filtered(
            lambda request: request.state != "draft"
            or (request.order_id and request.order_id.state != "draft")
        ):
            raise UserError(_("Only requests on draft state can be unlinked"))
        if not self.env.su and not self.env.user.has_group(
            "stock_request.group_stock_request_manager"
        ):
            invalid = self.filtered(
                lambda request: not request.order_id
                or request.order_id.requested_by != self.env.user
                or request.order_id.state != "draft"
            )
            if invalid:
                raise AccessError(
                    _(
                        "You can only remove products from your own Stock "
                        "Request while it is Draft."
                    )
                )
        return super().unlink()
