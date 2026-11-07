# Copyright 2018 Creu Blanca
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl.html).

from markupsafe import Markup

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import float_compare, str2bool

from .res_config_settings import (
    SHOW_AVAILABLE_QTY_PARAMETER,
    TRANSFER_FLOW_PARAMETER,
)


class StockRequestOrder(models.Model):
    _name = "stock.request.order"
    _description = "Stock Request"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"

    @api.model
    def _get_configured_transfer_flow(self):
        return (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param(TRANSFER_FLOW_PARAMETER, "one_step")
            or "one_step"
        )

    @api.model
    def default_get(self, fields):
        res = super().default_get(fields)
        company = self.env["res.company"].browse(
            res.get("company_id") or self.env.company.id
        )
        warehouse = self.env["stock.warehouse"].browse(res.get("warehouse_id"))
        if not warehouse:
            warehouse = self._get_default_destination_warehouse(company)
        if warehouse:
            res["warehouse_id"] = warehouse.id
            res["location_id"] = warehouse.lot_stock_id.id
        if "source_warehouse_id" not in res:
            operation_type = (
                res.get("operation_type")
                or self.env.context.get("default_operation_type")
                or "request"
            )
            source_warehouse = self._get_configured_source_warehouse(
                warehouse, operation_type
            )
            if source_warehouse:
                res["source_warehouse_id"] = source_warehouse.id
        is_return = bool(
            res.get("is_return") or self.env.context.get("default_is_return")
        )
        original_transfer = self.env["stock.request.order"].browse(
            res.get("original_transfer_id")
            or self.env.context.get("default_original_transfer_id")
        )
        if is_return and original_transfer:
            res.update(
                {
                    "is_return": True,
                    "original_transfer_id": original_transfer.id,
                    "operation_type": "transfer",
                    "transfer_flow": "one_step",
                    "company_id": original_transfer.company_id.id,
                    "source_warehouse_id": original_transfer.warehouse_id.id,
                    "warehouse_id": original_transfer.source_warehouse_id.id,
                    "location_id": (
                        original_transfer.source_warehouse_id.lot_stock_id.id
                    ),
                    "priority": original_transfer.priority,
                }
            )
            if "stock_request_ids" in fields and not res.get("stock_request_ids"):
                res["stock_request_ids"] = (
                    original_transfer._prepare_return_line_commands()
                )
        return res

    @api.model
    def _get_default_destination_warehouse(self, company=None):
        company = company or self.env.company
        warehouse = self.env.user.default_stock_request_warehouse_id
        if warehouse.active and warehouse.company_id == company:
            return warehouse
        return self.env["stock.warehouse"]

    @api.model
    def _get_configured_source_warehouse(
        self, destination_warehouse, operation_type="request"
    ):
        source_warehouse = (
            destination_warehouse.default_stock_request_source_warehouse_id
        )
        if not source_warehouse or (
            source_warehouse.company_id != destination_warehouse.company_id
        ):
            return self.env["stock.warehouse"]
        if (
            operation_type == "transfer"
            and not self.env.su
            and not self.env.user.has_group(
                "stock_request.group_stock_request_manager"
            )
            and self.env.user
            not in source_warehouse.stock_request_responsible_user_ids
        ):
            return self.env["stock.warehouse"]
        return source_warehouse

    def _get_default_requested_by(self):
        return self.env["res.users"].browse(self.env.uid)

    name = fields.Char(
        string="Request Number",
        copy=False,
        required=True,
        readonly=True,
        default="/",
    )
    operation_type = fields.Selection(
        selection=[
            ("request", "Stock Request"),
            ("transfer", "Direct Transfer"),
        ],
        string="Operation Type",
        required=True,
        default="request",
        copy=False,
        index=True,
        readonly=True,
    )
    transfer_flow = fields.Selection(
        selection=[
            ("one_step", "One Step"),
            ("two_step", "Two Step"),
            ("two_step_no_transit", "Two Step without Transit"),
        ],
        string="Transfer Flow",
        required=True,
        default=lambda self: self._get_configured_transfer_flow(),
        readonly=True,
        copy=False,
        index=True,
        help=(
            "Workflow selected when the operation was created. One Step moves "
            "products directly between warehouses; Two Step uses a transit "
            "location with separate delivery and receipt transfers; Two Step "
            "without Transit creates a direct transfer when receipt is confirmed."
        ),
    )
    state = fields.Selection(
        selection=[
            ("draft", "Draft"),
            ("submitted", "Waiting for Source Warehouse"),
            ("ready", "Ready to Deliver"),
            ("in_transit", "In Transit"),
            ("done", "Received"),
            ("rejected", "Rejected"),
            ("cancelled", "Cancelled"),
        ],
        string="Status",
        copy=False,
        default="draft",
        required=True,
        index=True,
        readonly=True,
        tracking=True,
    )
    one_step_transfer_state = fields.Selection(
        selection=[
            ("draft", "Draft"),
            ("transferred", "Transferred"),
            ("cancelled", "Cancelled"),
        ],
        string="Transfer Status",
        compute="_compute_one_step_transfer_state",
    )
    is_return = fields.Boolean(
        string="Is Return",
        default=False,
        readonly=True,
        copy=False,
        index=True,
    )
    original_transfer_id = fields.Many2one(
        comodel_name="stock.request.order",
        string="Original Transfer",
        check_company=True,
        ondelete="restrict",
        copy=False,
        index=True,
        domain=(
            "[('is_return', '=', False), "
            "('operation_type', '=', 'transfer'), "
            "('transfer_flow', '=', 'one_step'), "
            "('state', '=', 'done'), ('company_id', '=', company_id)]"
        ),
    )
    return_ids = fields.One2many(
        comodel_name="stock.request.order",
        inverse_name="original_transfer_id",
        string="Returns",
        readonly=True,
    )
    return_count = fields.Integer(
        string="Return Count",
        compute="_compute_return_count",
    )
    one_step_return_state = fields.Selection(
        selection=[
            ("draft", "Draft"),
            ("returned", "Returned"),
            ("cancelled", "Cancelled"),
        ],
        string="Return Status",
        compute="_compute_one_step_return_state",
    )
    show_available_qty = fields.Boolean(
        string="Show Available Quantity",
        compute="_compute_show_available_qty",
    )
    requested_by = fields.Many2one(
        "res.users",
        required=True,
        tracking=True,
        default=lambda s: s._get_default_requested_by(),
    )
    request_date = fields.Date(
        string="Request Date",
        required=True,
        copy=False,
        default=fields.Date.context_today,
        index=True,
        tracking=True,
    )
    priority = fields.Selection(
        [("0", "Normal"), ("1", "Urgent")],
        default="0",
        required=True,
        index=True,
        tracking=True,
    )
    source_request_id = fields.Many2one(
        comodel_name="stock.request.order",
        string="Source Document",
        domain="[('operation_type', '=', 'request'), ('company_id', '=', company_id)]",
        check_company=True,
        ondelete="set null",
        index=True,
        tracking=True,
        help="Optional Stock Request that this Direct Transfer originates from.",
    )
    direct_transfer_ids = fields.One2many(
        comodel_name="stock.request.order",
        inverse_name="source_request_id",
        string="Linked Direct Transfers",
        readonly=True,
    )
    direct_transfer_count = fields.Integer(
        string="Direct Transfers",
        compute="_compute_direct_transfer_count",
    )
    reason = fields.Text(string="Request Reason / Notes", tracking=True)
    rejection_reason = fields.Text(
        string="Rejection Reason",
        copy=False,
        tracking=True,
    )
    next_action = fields.Selection(
        selection=[
            ("submit_request", "Submit Request"),
            ("review_request", "Review Request"),
            ("transfer_products", "Transfer Products"),
            ("transfer_remaining", "Transfer Remaining Quantity"),
            ("deliver_products", "Deliver Products"),
            ("deliver_remaining", "Deliver Remaining Quantity"),
            ("receive_products", "Receive Products"),
            ("receive_remaining", "Receive Remaining Quantity"),
            ("no_action", "No Action Required"),
        ],
        string="Next Action",
        compute="_compute_next_action",
        store=True,
        readonly=True,
        index=True,
    )
    submitted_date = fields.Datetime(
        string="Submitted Date",
        readonly=True,
        copy=False,
        tracking=True,
    )
    ready_date = fields.Datetime(
        string="Ready Date",
        readonly=True,
        copy=False,
        tracking=True,
    )
    first_delivery_date = fields.Datetime(
        string="First Delivery Date",
        readonly=True,
        copy=False,
        tracking=True,
    )
    rejected_date = fields.Datetime(
        string="Rejected Date",
        readonly=True,
        copy=False,
        tracking=True,
    )
    cancelled_date = fields.Datetime(
        string="Cancelled Date",
        readonly=True,
        copy=False,
        tracking=True,
    )
    source_warehouse_id = fields.Many2one(
        comodel_name="stock.warehouse",
        string="Source Warehouse",
        check_company=True,
        ondelete="restrict",
    )
    transit_location_id = fields.Many2one(
        comodel_name="stock.location",
        string="Transit Location",
        readonly=True,
        copy=False,
        check_company=True,
        ondelete="restrict",
    )
    source_location_id = fields.Many2one(
        comodel_name="stock.location",
        string="Source Location",
        compute="_compute_source_location_id",
        store=True,
        readonly=True,
        check_company=True,
    )
    template_id = fields.Many2one(
        comodel_name="stock.request.template",
        string="Stock Request Template",
        check_company=True,
        copy=False,
        domain="[('active', '=', True), ('company_id', '=', company_id)]",
        ondelete="set null",
    )
    source_responsible_user_ids = fields.Many2many(
        comodel_name="res.users",
        string="Source Warehouse Responsible Users",
        related="source_warehouse_id.stock_request_responsible_user_ids",
        readonly=True,
        copy=False,
    )
    destination_responsible_user_ids = fields.Many2many(
        comodel_name="res.users",
        string="Destination Warehouse Responsible Users",
        related="warehouse_id.stock_request_responsible_user_ids",
        readonly=True,
        copy=False,
    )
    is_source_warehouse_responsible = fields.Boolean(
        compute="_compute_warehouse_responsibility",
        string="Is Source Warehouse Responsible",
    )
    is_destination_warehouse_responsible = fields.Boolean(
        compute="_compute_warehouse_responsibility",
        string="Is Destination Warehouse Responsible",
    )
    is_requester = fields.Boolean(
        compute="_compute_warehouse_responsibility",
        string="Is Requester",
    )
    warehouse_id = fields.Many2one(
        comodel_name="stock.warehouse",
        string="Destination Warehouse",
        check_company=True,
        ondelete="cascade",
        required=True,
    )
    location_id = fields.Many2one(
        comodel_name="stock.location",
        string="Location",
        domain="[('usage', 'in', ['internal', 'transit'])]",
        ondelete="cascade",
        required=True,
    )
    procurement_group_id = fields.Many2one(
        "stock.reference",
        "Procurement Group",
        help="Moves created through this stock request will be put in this "
        "procurement group. If none is given, the moves generated by "
        "procurement rules will be grouped into one big picking.",
    )
    company_id = fields.Many2one(
        "res.company",
        "Company",
        required=True,
        default=lambda self: self.env.company,
    )
    expected_date = fields.Datetime(
        default=fields.Datetime.now,
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
    picking_count = fields.Integer(
        string="Delivery Orders", compute="_compute_picking_ids", readonly=True
    )
    stock_request_operation_picking_ids = fields.One2many(
        comodel_name="stock.picking",
        inverse_name="stock_request_order_id",
        string="Operational Transfers",
        readonly=True,
        copy=False,
    )
    has_pending_delivery = fields.Boolean(
        string="Has Products to Deliver",
        compute="_compute_pending_operations",
        store=True,
        index=True,
    )
    has_pending_receipt = fields.Boolean(
        string="Has Products to Receive",
        compute="_compute_pending_operations",
        store=True,
        index=True,
    )
    can_close_remaining_receipt = fields.Boolean(
        string="Can Close Remaining Receipt",
        compute="_compute_pending_operations",
        store=True,
    )
    delivery_picking_count = fields.Integer(
        string="Delivery",
        compute="_compute_operation_picking_counts",
    )
    receipt_picking_count = fields.Integer(
        string="Receipt",
        compute="_compute_operation_picking_counts",
    )
    transfer_picking_count = fields.Integer(
        string="Transfer",
        compute="_compute_operation_picking_counts",
    )
    accepted_by_id = fields.Many2one(
        comodel_name="res.users",
        string="Accepted By",
        readonly=True,
        copy=False,
        tracking=True,
    )
    delivered_by_id = fields.Many2one(
        comodel_name="res.users",
        string="Delivered By",
        readonly=True,
        copy=False,
        tracking=True,
    )
    delivery_date = fields.Datetime(
        string="Delivery Date",
        readonly=True,
        copy=False,
        tracking=True,
    )
    received_by_id = fields.Many2one(
        comodel_name="res.users",
        string="Received By",
        readonly=True,
        copy=False,
        tracking=True,
    )
    receipt_date = fields.Datetime(
        string="Receipt Date",
        readonly=True,
        copy=False,
        tracking=True,
    )
    requests_count = fields.Integer(
        string="Requests Count",
        compute="_compute_analysis_counts",
        store=True,
        aggregator="sum",
        readonly=True,
    )
    completed_requests_count = fields.Integer(
        string="Completed Requests Count",
        compute="_compute_analysis_counts",
        store=True,
        aggregator="sum",
        readonly=True,
    )
    rejected_requests_count = fields.Integer(
        string="Rejected Requests Count",
        compute="_compute_analysis_counts",
        store=True,
        aggregator="sum",
        readonly=True,
    )
    cancelled_requests_count = fields.Integer(
        string="Cancelled Requests Count",
        compute="_compute_analysis_counts",
        store=True,
        aggregator="sum",
        readonly=True,
    )
    review_time = fields.Float(
        string="Average Review Time",
        aggregator="avg",
        readonly=True,
        copy=False,
        help="Review time in days, from submission until acceptance.",
    )
    preparation_time = fields.Float(
        string="Average Preparation Time",
        aggregator="avg",
        readonly=True,
        copy=False,
        help="Preparation time in days, from acceptance until first delivery.",
    )
    transit_time = fields.Float(
        string="Average Transit Time",
        aggregator="avg",
        readonly=True,
        copy=False,
        help="Transit time in days, from first delivery until complete receipt.",
    )
    total_cycle_time = fields.Float(
        string="Average Total Cycle Time",
        aggregator="avg",
        readonly=True,
        copy=False,
        help="Total cycle time in days, from request date until complete receipt.",
    )
    stock_request_ids = fields.One2many(
        "stock.request", inverse_name="order_id", copy=True
    )
    route_ids = fields.Many2many(
        "stock.route",
        string="Routes",
        compute="_compute_route_ids",
        readonly=True,
        store=True,
    )

    route_id = fields.Many2one(
        "stock.route",
        compute="_compute_route_id",
        readonly=True,
        store=True,
        help="The route related to a stock request",
    )

    _name_uniq = models.Constraint(
        "unique(name, company_id)",
        "Stock Request name must be unique",
    )

    @api.depends("source_warehouse_id")
    def _compute_source_location_id(self):
        for order in self:
            order.source_location_id = order.source_warehouse_id.lot_stock_id

    @api.depends(
        "requested_by",
        "source_responsible_user_ids",
        "destination_responsible_user_ids",
    )
    @api.depends_context("uid")
    def _compute_warehouse_responsibility(self):
        is_manager = self.env.su or self.env.user.has_group(
            "stock_request.group_stock_request_manager"
        )
        for order in self:
            order.is_source_warehouse_responsible = (
                is_manager or self.env.user in order.source_responsible_user_ids
            )
            order.is_destination_warehouse_responsible = (
                is_manager
                or self.env.user in order.destination_responsible_user_ids
            )
            order.is_requester = is_manager or order.requested_by == self.env.user

    @api.depends("state")
    def _compute_one_step_transfer_state(self):
        for order in self:
            if order.state == "done":
                order.one_step_transfer_state = "transferred"
            elif order.state == "cancelled":
                order.one_step_transfer_state = "cancelled"
            else:
                order.one_step_transfer_state = "draft"

    @api.depends("return_ids")
    def _compute_return_count(self):
        grouped_returns = self.env["stock.request.order"]._read_group(
            [("original_transfer_id", "in", self.ids)],
            ["original_transfer_id"],
            ["__count"],
        )
        counts = {original.id: count for original, count in grouped_returns}
        for order in self:
            order.return_count = counts.get(order.id, 0)

    @api.depends("direct_transfer_ids")
    def _compute_direct_transfer_count(self):
        grouped_transfers = self.env["stock.request.order"]._read_group(
            [("source_request_id", "in", self.ids)],
            ["source_request_id"],
            ["__count"],
        )
        counts = {
            source_request.id: count
            for source_request, count in grouped_transfers
        }
        for order in self:
            order.direct_transfer_count = counts.get(order.id, 0)

    @api.constrains("source_request_id", "operation_type")
    def _check_source_request_id(self):
        invalid_orders = self.filtered(
            lambda order: order.source_request_id
            and (
                order.operation_type != "transfer"
                or order.source_request_id.operation_type != "request"
            )
        )
        if invalid_orders:
            raise ValidationError(
                _(
                    "The Source Document can only link a Direct Transfer to a "
                    "Stock Request."
                )
            )

    @api.depends("state")
    def _compute_one_step_return_state(self):
        for order in self:
            if order.state == "done":
                order.one_step_return_state = "returned"
            elif order.state == "cancelled":
                order.one_step_return_state = "cancelled"
            else:
                order.one_step_return_state = "draft"

    def _compute_show_available_qty(self):
        show_available_qty = str2bool(
            self.env["ir.config_parameter"]
            .sudo()
            .get_param(SHOW_AVAILABLE_QTY_PARAMETER, "False")
        )
        for order in self:
            order.show_available_qty = show_available_qty

    @api.depends(
        "state",
        "transfer_flow",
        "stock_request_ids.received_qty",
        "stock_request_ids.remaining_delivery_qty",
        "stock_request_ids.remaining_receipt_qty",
    )
    def _compute_pending_operations(self):
        for order in self:
            order.has_pending_delivery = (
                order.state in ("submitted", "ready", "in_transit")
                and any(
                    float_compare(
                        line.remaining_delivery_qty,
                        0.0,
                        precision_rounding=line.product_uom_id.rounding,
                    )
                    > 0
                    for line in order.stock_request_ids
                )
            )
            order.has_pending_receipt = (
                order.transfer_flow in ("two_step", "two_step_no_transit")
                and order.state == "in_transit"
                and any(
                    float_compare(
                        line.remaining_receipt_qty,
                        0.0,
                        precision_rounding=line.product_uom_id.rounding,
                    )
                    > 0
                    for line in order.stock_request_ids
                )
            )
            order.can_close_remaining_receipt = (
                order.has_pending_receipt
                and any(
                    float_compare(
                        line.received_qty,
                        0.0,
                        precision_rounding=line.product_uom_id.rounding,
                    )
                    > 0
                    for line in order.stock_request_ids
                )
            )

    @api.depends(
        "state",
        "operation_type",
        "transfer_flow",
        "stock_request_ids.delivered_qty",
        "stock_request_ids.received_qty",
        "stock_request_ids.remaining_delivery_qty",
        "stock_request_ids.remaining_receipt_qty",
    )
    def _compute_next_action(self):
        for order in self:
            if order.state == "draft":
                if order.operation_type == "request":
                    order.next_action = "submit_request"
                elif order.transfer_flow == "one_step":
                    order.next_action = "transfer_products"
                else:
                    order.next_action = "deliver_products"
                continue
            if order.state == "submitted":
                order.next_action = "review_request"
                continue
            if order.state not in ("ready", "in_transit"):
                order.next_action = "no_action"
                continue

            has_delivered = any(
                float_compare(
                    line.delivered_qty,
                    0.0,
                    precision_rounding=line.product_uom_id.rounding,
                )
                > 0
                for line in order.stock_request_ids
            )
            has_received = any(
                float_compare(
                    line.received_qty,
                    0.0,
                    precision_rounding=line.product_uom_id.rounding,
                )
                > 0
                for line in order.stock_request_ids
            )
            has_delivery_remaining = any(
                float_compare(
                    line.remaining_delivery_qty,
                    0.0,
                    precision_rounding=line.product_uom_id.rounding,
                )
                > 0
                for line in order.stock_request_ids
            )
            has_receipt_remaining = any(
                float_compare(
                    line.remaining_receipt_qty,
                    0.0,
                    precision_rounding=line.product_uom_id.rounding,
                )
                > 0
                for line in order.stock_request_ids
            )
            if has_delivery_remaining:
                if order.transfer_flow == "one_step":
                    order.next_action = (
                        "transfer_remaining" if has_delivered else "transfer_products"
                    )
                else:
                    order.next_action = (
                        "deliver_remaining" if has_delivered else "deliver_products"
                    )
            elif has_receipt_remaining:
                order.next_action = (
                    "receive_remaining" if has_received else "receive_products"
                )
            else:
                order.next_action = "no_action"

    @api.depends(
        "stock_request_operation_picking_ids",
        "stock_request_operation_picking_ids.stock_request_operation",
    )
    def _compute_operation_picking_counts(self):
        for order in self:
            order.delivery_picking_count = len(
                order.stock_request_operation_picking_ids.filtered(
                    lambda picking: picking.stock_request_operation == "delivery"
                )
            )
            order.receipt_picking_count = len(
                order.stock_request_operation_picking_ids.filtered(
                    lambda picking: picking.stock_request_operation == "receipt"
                )
            )
            order.transfer_picking_count = len(
                order.stock_request_operation_picking_ids.filtered(
                    lambda picking: picking.stock_request_operation == "transfer"
                )
            )

    @api.depends("state")
    def _compute_analysis_counts(self):
        for order in self:
            order.requests_count = 1
            order.completed_requests_count = int(order.state == "done")
            order.rejected_requests_count = int(order.state == "rejected")
            order.cancelled_requests_count = int(order.state == "cancelled")

    @staticmethod
    def _analysis_duration_days(start_date, end_date):
        if not start_date or not end_date:
            return False
        return max((end_date - start_date).total_seconds() / 86400.0, 0.0)

    def _prepare_completion_analysis_values(self):
        self.ensure_one()
        values = {}
        if self.first_delivery_date and self.receipt_date:
            values["transit_time"] = self._analysis_duration_days(
                self.first_delivery_date,
                self.receipt_date,
            )
        if self.request_date and self.receipt_date:
            values["total_cycle_time"] = self._analysis_duration_days(
                fields.Datetime.to_datetime(self.request_date),
                self.receipt_date,
            )
        return values

    def _is_fully_received(self):
        self.ensure_one()
        lines = self.stock_request_ids
        if not lines:
            return False
        has_delivered_quantity = any(
            float_compare(
                line.delivered_qty,
                0.0,
                precision_rounding=line.product_uom_id.rounding,
            )
            > 0
            for line in lines
        )
        all_delivered_quantities_received = all(
            float_compare(
                line.remaining_delivery_qty,
                0.0,
                precision_rounding=line.product_uom_id.rounding,
            )
            <= 0
            and float_compare(
                line.received_qty,
                line.delivered_qty,
                precision_rounding=line.product_uom_id.rounding,
            )
            >= 0
            for line in lines
        )
        return has_delivered_quantity and all_delivered_quantities_received

    def _record_transfer_validation(
        self, picking, validating_user, audit_data=None
    ):
        """Store the real validator and keep every validation in the chatter."""
        self.ensure_one()
        validation_date = picking.date_done or fields.Datetime.now()
        if picking.stock_request_operation == "delivery":
            values = {
                "delivered_by_id": validating_user.id,
                "delivery_date": validation_date,
            }
            if not self.first_delivery_date:
                values["first_delivery_date"] = validation_date
                if self.ready_date:
                    values["preparation_time"] = self._analysis_duration_days(
                        self.ready_date,
                        validation_date,
                    )
            operation_label = _("Delivery")
        elif picking.stock_request_operation == "receipt":
            values = {}
            if self._is_fully_received():
                values.update(
                    {
                        "received_by_id": validating_user.id,
                        "receipt_date": validation_date,
                    }
                )
            operation_label = _("Receipt")
        elif picking.stock_request_operation == "transfer":
            values = {
                "delivered_by_id": validating_user.id,
                "delivery_date": validation_date,
                "received_by_id": validating_user.id,
                "receipt_date": validation_date,
            }
            if not self.first_delivery_date:
                values["first_delivery_date"] = validation_date
                if self.ready_date:
                    values["preparation_time"] = self._analysis_duration_days(
                        self.ready_date,
                        validation_date,
                    )
            operation_label = _("Transfer")
        else:
            return

        # Workflow audit values are system-managed, so bypass normal order edit
        # rules while preserving the actual validator as the chatter author.
        if values:
            self.sudo().write(values)
        message = Markup("<p>%s</p>") % _(
            "%(user)s validated %(operation)s %(picking)s on %(date)s.",
            user=validating_user.display_name,
            operation=operation_label,
            picking=picking.display_name,
            date=fields.Datetime.to_string(validation_date),
        )
        audit_data = audit_data or {}
        if audit_data.get("decision"):
            decision_label = {
                "create_backorder": _("Create Backorder"),
                "cancel_remaining": _("No Backorder / Cancel Remaining"),
                "keep_pending": _("Keep Remaining Pending"),
            }[audit_data["decision"]]
            message += Markup("<p>%s</p>") % _(
                "Partial quantity decision: %(decision)s.",
                decision=decision_label,
            )
            details = Markup()
            for line in audit_data.get("lines", []):
                details += Markup("<li>%s</li>") % _(
                    "%(product)s: processed %(processed)s %(uom)s; "
                    "remaining %(remaining)s %(uom)s.",
                    product=line["product"],
                    processed=line["processed"],
                    remaining=line["remaining"],
                    uom=line["uom"],
                )
            if details:
                message += Markup("<ul>%s</ul>") % details
        self.sudo().message_post(
            body=message,
            author_id=validating_user.partner_id.id,
            subtype_xmlid="mail.mt_note",
        )

    def _schedule_activities_for_users(self, activity_type_xmlid, users, summary):
        """Schedule one open activity of this type per active responsible user."""
        for order in self:
            for user in users.filtered("active"):
                existing = order.sudo().activity_search(
                    [activity_type_xmlid], user_id=user.id
                )
                if not existing:
                    order.sudo().activity_schedule(
                        activity_type_xmlid,
                        summary=summary,
                        user_id=user.id,
                    )

    def _schedule_review_activities(self):
        for order in self:
            order._schedule_activities_for_users(
                "stock_request.mail_activity_type_stock_request_review",
                order.source_responsible_user_ids,
                _("New stock request requires review."),
            )

    def _schedule_receipt_activities(self):
        for order in self:
            order._schedule_activities_for_users(
                "stock_request.mail_activity_type_stock_request_receipt",
                order.destination_responsible_user_ids,
                _("Stock transfer is ready to receive."),
            )

    def _complete_activities(self, activity_type_xmlid, feedback):
        for order in self:
            order.sudo().activity_feedback(
                [activity_type_xmlid], feedback=feedback
            )

    def _notify_requester(self, message, author_user=None):
        author_user = author_user or self.env.user
        for order in self:
            order.sudo().message_post(
                body=Markup("<p>%s</p>") % message(order),
                author_id=author_user.partner_id.id,
                partner_ids=order.requested_by.partner_id.ids,
                subtype_xmlid="mail.mt_comment",
            )

    def _notify_received(self, author_user):
        received_orders = self.filtered(lambda order: order.state == "done")
        if not received_orders:
            return
        received_orders._complete_activities(
            "stock_request.mail_activity_type_stock_request_receipt",
            _("Stock transfer received."),
        )
        received_orders._notify_requester(
            lambda order: _(
                "Stock Request %(request)s has been received.",
                request=order.display_name,
            ),
            author_user=author_user,
        )

    def _notify_transferred(self, author_user):
        transferred_orders = self.filtered(lambda order: order.state == "done")
        if not transferred_orders:
            return
        transferred_orders._notify_requester(
            lambda order: _(
                "Stock Transfer %(request)s has been completed.",
                request=order.display_name,
            ),
            author_user=author_user,
        )

    @api.depends(
        "source_warehouse_id",
        "warehouse_id",
        "location_id",
        "stock_request_ids",
    )
    def _compute_route_ids(self):
        route_obj = self.env["stock.route"]
        routes = route_obj.search(
            [
                ("warehouse_ids", "in", self.mapped("warehouse_id").ids),
                ("is_request_stock", "=", True),
            ]
        )
        routes_by_warehouse = {}
        for route in routes:
            for warehouse in route.warehouse_ids:
                routes_by_warehouse.setdefault(warehouse.id, self.env["stock.route"])
                routes_by_warehouse[warehouse.id] |= route
        for record in self:
            if (
                record.source_warehouse_id
                and record.warehouse_id
                and record.source_warehouse_id != record.warehouse_id
            ):
                transfer_routes = route_obj.search(
                    [
                        ("supplier_wh_id", "=", record.source_warehouse_id.id),
                        ("supplied_wh_id", "=", record.warehouse_id.id),
                        ("active", "=", True),
                    ]
                ).filtered(lambda route: route._is_stock_request_route_allowed())
                record.route_ids = [Command.set(transfer_routes.ids)]
                continue
            routes = route_obj
            if record.warehouse_id and routes_by_warehouse.get(record.warehouse_id.id):
                routes |= routes_by_warehouse[record.warehouse_id.id]
            parents = record.get_parents().ids
            valid_routes = []
            for route in routes:
                if route._is_stock_request_route_allowed() and any(
                    p.location_dest_id.id in parents for p in route.rule_ids
                ):
                    valid_routes.append(route)
            filtered_routes = self.env["stock.route"].browse(
                [route.id for route in valid_routes]
            )
            if record.stock_request_ids:
                all_routes = record.stock_request_ids.mapped("route_ids")
                common_routes = all_routes
                for line in record.stock_request_ids:
                    common_routes &= line.route_ids
                final_routes = filtered_routes | common_routes
                record.route_ids = [(6, 0, final_routes.ids)]
            else:
                record.route_ids = [(6, 0, filtered_routes.ids)]

    def get_parents(self):
        location = self.location_id
        result = location
        while location.location_id:
            location = location.location_id
            result |= location
        return result

    @api.depends("route_ids")
    def _compute_route_id(self):
        for order in self:
            order.route_id = order.route_ids[:1]

    @api.depends("stock_request_ids.allocation_ids")
    def _compute_picking_ids(self):
        for record in self:
            record.picking_ids = record.stock_request_ids.mapped("picking_ids")
            record.picking_count = len(record.picking_ids)

    @api.depends("stock_request_ids")
    def _compute_move_ids(self):
        for record in self:
            record.move_ids = record.stock_request_ids.mapped("move_ids")

    @api.onchange("requested_by")
    def onchange_requested_by(self):
        self.change_childs()

    @api.onchange("expected_date")
    def onchange_expected_date(self):
        self.change_childs()

    @api.onchange("location_id")
    def onchange_location_id(self):
        if self.location_id:
            loc_wh = self.location_id.warehouse_id
            if loc_wh and self.warehouse_id != loc_wh:
                self.warehouse_id = loc_wh
                self.with_context(no_change_childs=True).onchange_warehouse_id()
        self.change_childs()

    @api.onchange("warehouse_id")
    def onchange_warehouse_id(self):
        if self.warehouse_id:
            # search with sudo because the user may not have permissions
            loc_wh = self.location_id.warehouse_id
            if self.warehouse_id != loc_wh:
                self.location_id = self.warehouse_id.lot_stock_id
                self.with_context(no_change_childs=True).onchange_location_id()
            if self.warehouse_id.company_id != self.company_id:
                self.company_id = self.warehouse_id.company_id
                self.with_context(no_change_childs=True).onchange_company_id()
            configured_source = self._get_configured_source_warehouse(
                self.warehouse_id, self.operation_type
            )
            self.source_warehouse_id = configured_source
        self.change_childs()

    @api.onchange("source_warehouse_id")
    def onchange_source_warehouse_id(self):
        self.change_childs()

    def _prepare_template_request_line_values(self, template_line):
        self.ensure_one()
        return {
            "product_id": template_line.product_id.id,
            "product_uom_id": template_line.product_uom_id.id,
            "product_uom_qty": template_line.product_uom_qty,
            "warehouse_id": self.warehouse_id.id,
            "location_id": self.location_id.id,
            "source_location_id": self.source_location_id.id,
            "company_id": self.company_id.id,
            "expected_date": self.expected_date,
            "requested_by": self.requested_by.id,
            "procurement_group_id": self.procurement_group_id.id,
        }

    def action_load_template(self):
        self.ensure_one()
        if self.state != "draft":
            raise UserError(_("Templates can only be loaded into draft operations."))
        template = self.template_id
        if not template or not template.active:
            raise UserError(_("Select an active Stock Request Template first."))
        if template.company_id != self.company_id:
            raise UserError(
                _("The Stock Request Template must belong to the request company.")
            )

        existing_by_product = {}
        for request_line in self.stock_request_ids:
            existing_by_product.setdefault(request_line.product_id.id, []).append(
                request_line
            )
        template_by_product = {
            line.product_id.id: line for line in template.line_ids
        }
        line_commands = []
        for product_id, request_lines in existing_by_product.items():
            keeper = request_lines[0]
            template_line = template_by_product.pop(product_id, False)
            if template_line:
                line_values = {
                    "product_uom_id": template_line.product_uom_id.id,
                    "product_uom_qty": template_line.product_uom_qty,
                }
            else:
                product_uom = keeper.product_id.uom_id
                quantity = sum(
                    line.product_uom_id._compute_quantity(
                        line.product_uom_qty, product_uom
                    )
                    for line in request_lines
                )
                line_values = {
                    "product_uom_id": product_uom.id,
                    "product_uom_qty": quantity,
                }
            line_commands.append(Command.update(keeper.id, line_values))
            line_commands.extend(
                Command.delete(duplicate.id) for duplicate in request_lines[1:]
            )
        line_commands.extend(
            Command.create(self._prepare_template_request_line_values(line))
            for line in template_by_product.values()
        )

        self.write(
            {
                "priority": template.priority,
                "reason": template.notes,
                "stock_request_ids": line_commands,
            }
        )
        return {"type": "ir.actions.client", "tag": "reload"}

    @api.onchange("procurement_group_id")
    def onchange_procurement_group_id(self):
        self.change_childs()

    @api.onchange("company_id")
    def onchange_company_id(self):
        if self.company_id and (
            not self.warehouse_id or self.warehouse_id.company_id != self.company_id
        ):
            self.warehouse_id = self.env["stock.warehouse"].search(
                [("company_id", "=", self.company_id.id)], limit=1
            )
            self.with_context(no_change_childs=True).onchange_warehouse_id()
        if self.company_id and (
            not self.source_warehouse_id
            or self.source_warehouse_id.company_id != self.company_id
        ):
            self.source_warehouse_id = False
        self.change_childs()

    def change_childs(self):
        if self.env.context.get("no_change_childs", False):
            return
        for order in self:
            source_location = order.source_warehouse_id.lot_stock_id
            order.source_location_id = source_location
            for line in order.stock_request_ids:
                line.warehouse_id = order.warehouse_id
                line.location_id = order.location_id
                line.source_location_id = source_location
                line.company_id = order.company_id
                line.expected_date = order.expected_date
                line.requested_by = order.requested_by
                line.procurement_group_id = order.procurement_group_id
                line.route_id = order.route_id

    def _check_source_responsible_access(self):
        is_manager = self.env.su or self.env.user.has_group(
            "stock_request.group_stock_request_manager"
        )
        is_responsible_user = self.env.user.has_group(
            "stock_request.group_stock_request_warehouse_responsible"
        )
        if not is_manager and (
            not is_responsible_user
            or self.filtered(
                lambda order: self.env.user
                not in order.source_responsible_user_ids
            )
        ):
            raise AccessError(
                _(
                    "Only a responsible user of the source warehouse can "
                    "perform this action."
                )
            )

    def _check_destination_responsible_access(self):
        is_manager = self.env.su or self.env.user.has_group(
            "stock_request.group_stock_request_manager"
        )
        is_responsible_user = self.env.user.has_group(
            "stock_request.group_stock_request_warehouse_responsible"
        )
        if not is_manager and (
            not is_responsible_user
            or self.filtered(
                lambda order: self.env.user
                not in order.destination_responsible_user_ids
            )
        ):
            raise AccessError(
                _(
                    "Only a responsible user of the destination warehouse can "
                    "receive this Stock Request."
                )
            )

    def _check_requester_access(self):
        if self.filtered(lambda order: not order.is_requester):
            raise AccessError(
                _(
                    "Only the requesting user can submit or cancel this Stock "
                    "Request."
                )
            )

    def _check_operation_create_access(self, vals_list):
        if self.env.su:
            return
        is_manager = self.env.user.has_group(
            "stock_request.group_stock_request_manager"
        )
        is_request_user = self.env.user.has_group(
            "stock_request.group_stock_request_user"
        )
        is_warehouse_responsible = self.env.user.has_group(
            "stock_request.group_stock_request_warehouse_responsible"
        )
        for vals in vals_list:
            operation_type = vals.get("operation_type", "request")
            if (
                vals.get("requested_by", self.env.uid) != self.env.uid
                and not is_manager
            ):
                raise AccessError(
                    _("Users can only create Stock Requests for themselves.")
                )
            if vals.get("state", "draft") != "draft" and not is_manager:
                raise AccessError(_("New Stock Requests must start in Draft."))
            if operation_type == "request" and not (is_request_user or is_manager):
                raise AccessError(
                    _("Only Stock Request Users can create Stock Requests.")
                )
            if operation_type != "transfer" or is_manager:
                continue
            if not is_warehouse_responsible:
                raise AccessError(
                    _(
                        "Only a Stock Request Warehouse Responsible can create "
                        "a Direct Transfer."
                    )
                )
            source_warehouse = self.env["stock.warehouse"].browse(
                vals.get("source_warehouse_id")
            )
            if self.env.user not in source_warehouse.stock_request_responsible_user_ids:
                raise AccessError(
                    _(
                        "Only a responsible user of the source warehouse can "
                        "create a Direct Transfer."
                    )
                )

    def action_submit(self):
        invalid = self.filtered(
            lambda order: order.operation_type != "request" or order.state != "draft"
        )
        if invalid:
            raise UserError(_("Only draft Stock Requests can be submitted."))
        self._check_requester_access()
        self._check_stock_request_operation_configuration()
        self.sudo().write(
            {"state": "submitted", "submitted_date": fields.Datetime.now()}
        )
        self._schedule_review_activities()
        return True

    def _check_confirmation_state(self):
        invalid_orders = self.filtered(
            lambda order: (
                order.operation_type == "request" and order.state != "submitted"
            )
            or (
                order.operation_type == "transfer" and order.state != "draft"
            )
        )
        if invalid_orders:
            raise UserError(
                _(
                    "Stock Requests must be waiting for the source warehouse, "
                    "and Direct Transfers must be in draft before confirmation."
                )
            )
        return True

    def action_reject(self):
        invalid = self.filtered(
            lambda order: order.operation_type != "request"
            or order.state != "submitted"
        )
        if invalid:
            raise UserError(_("Only submitted Stock Requests can be rejected."))
        self._check_source_responsible_access()
        if self.filtered(lambda order: not (order.rejection_reason or "").strip()):
            raise UserError(_("Enter a rejection reason before rejecting the request."))
        rejecting_user = self.env.user
        self.sudo().write(
            {"state": "rejected", "rejected_date": fields.Datetime.now()}
        )
        self._complete_activities(
            "stock_request.mail_activity_type_stock_request_review",
            _("Stock request rejected."),
        )
        self._notify_requester(
            lambda order: _(
                "Stock Request %(request)s was rejected. Reason: %(reason)s",
                request=order.display_name,
                reason=order.rejection_reason,
            ),
            author_user=rejecting_user,
        )
        return True

    def _resolve_receiving_transit_location(self):
        self.ensure_one()
        return self.warehouse_id.sudo()._get_stock_request_transit_location()

    def _get_effective_transit_location(self):
        """Keep old operations tied to their existing picking locations."""
        self.ensure_one()
        if self.transit_location_id:
            return self.transit_location_id
        delivery = self.stock_request_operation_picking_ids.filtered(
            lambda picking: picking.stock_request_operation == "delivery"
        )[:1]
        if delivery:
            return delivery.location_dest_id
        receipt = self.stock_request_operation_picking_ids.filtered(
            lambda picking: picking.stock_request_operation == "receipt"
        )[:1]
        if receipt:
            return receipt.location_id
        return self._resolve_receiving_transit_location()

    def _check_transit_location(self, transit_location):
        self.ensure_one()
        if not transit_location:
            raise UserError(
                _(
                    "No transit location is configured for the receiving "
                    "warehouse, and the company does not have an Odoo default "
                    "internal transit location."
                )
            )
        if not transit_location.active:
            raise UserError(_("The transit location must be active."))
        if transit_location.usage != "transit":
            raise UserError(_("The transit location must be a Transit Location."))
        if transit_location.company_id != self.company_id:
            raise UserError(
                _("The transit location must belong to the request company.")
            )
        if transit_location in (
            self.source_warehouse_id.lot_stock_id
            | self.warehouse_id.lot_stock_id
        ):
            raise UserError(
                _(
                    "The transit location cannot be the source or destination "
                    "warehouse stock location."
                )
            )
        return True

    def _check_stock_request_operation_configuration(self):
        for order in self:
            source_warehouse = order.source_warehouse_id.sudo()
            destination_warehouse = order.warehouse_id.sudo()
            if not source_warehouse:
                raise UserError(_("Source Warehouse is required."))
            if not destination_warehouse:
                raise UserError(_("Destination Warehouse is required."))
            if source_warehouse == destination_warehouse:
                raise UserError(
                    _(
                        "Source Warehouse and Destination Warehouse must be "
                        "different."
                    )
                )
            if (
                source_warehouse.company_id != destination_warehouse.company_id
                or source_warehouse.company_id != order.company_id
            ):
                raise UserError(
                    _(
                        "The source and destination warehouses must belong to "
                        "the same company as the Stock Request."
                    )
                )
            if not order.stock_request_ids:
                raise UserError(
                    _("Add at least one product before submitting the request.")
                )
            invalid_quantity_lines = order.stock_request_ids.filtered(
                lambda line: float_compare(
                    line.product_uom_qty,
                    0.0,
                    precision_rounding=line.product_uom_id.rounding,
                )
                <= 0
            )
            if invalid_quantity_lines:
                raise UserError(
                    _(
                        "Requested quantities must be greater than zero for "
                        "all products."
                    )
                )
            for warehouse in source_warehouse | destination_warehouse:
                if not warehouse.stock_request_responsible_user_ids.filtered(
                    "active"
                ):
                    raise UserError(
                        _(
                            "No responsible users are configured for %(warehouse)s.",
                            warehouse=warehouse.display_name,
                        )
                    )
            if order.transfer_flow == "two_step":
                transit_location = order._get_effective_transit_location()
                order._check_transit_location(transit_location)
            for warehouse in source_warehouse | destination_warehouse:
                picking_type = warehouse.int_type_id
                if (
                    not picking_type
                    or not picking_type.active
                    or picking_type.code != "internal"
                    or picking_type.company_id != order.company_id
                ):
                    raise UserError(
                        _(
                            "Warehouse %(warehouse)s does not have a suitable "
                            "Internal Picking Type.",
                            warehouse=warehouse.display_name,
                        )
                    )
        return True

    def _lock_transit_location(self):
        for order in self:
            if order.stock_request_operation_picking_ids:
                continue
            transit_location = order._get_effective_transit_location()
            order._check_transit_location(transit_location)
            if not order.transit_location_id:
                order.sudo().write({"transit_location_id": transit_location.id})
        return True

    def _check_two_step_internal_transfer_configuration(self):
        self._check_stock_request_operation_configuration()
        for order in self:
            if not order.transit_location_id and not (
                order.stock_request_operation_picking_ids
            ):
                raise UserError(
                    _(
                        "The transit location must be locked on the Stock Request "
                        "before transfers are created."
                    )
                )
        return True

    def _prepare_two_step_picking_values(self, operation):
        self.ensure_one()
        transit_location = self._get_effective_transit_location()
        if operation == "delivery":
            picking_type = self.source_warehouse_id.int_type_id
            source_location = self.source_warehouse_id.lot_stock_id
            destination_location = transit_location
        else:
            picking_type = self.warehouse_id.int_type_id
            source_location = transit_location
            destination_location = self.warehouse_id.lot_stock_id
        return {
            "picking_type_id": picking_type.id,
            "location_id": source_location.id,
            "location_dest_id": destination_location.id,
            "scheduled_date": self.expected_date,
            "origin": self.name,
            "priority": self.priority,
            "company_id": self.company_id.id,
            "stock_request_order_id": self.id,
            "stock_request_operation": operation,
        }

    def _prepare_two_step_move_values(
        self,
        request,
        picking,
        source_location,
        destination_location,
        delivery_move=None,
    ):
        self.ensure_one()
        warehouse = (
            self.warehouse_id if delivery_move else self.source_warehouse_id
        )
        values = {
            "origin": self.name,
            "product_id": request.product_id.id,
            "product_uom_qty": request.product_uom_qty,
            "product_uom": request.product_uom_id.id,
            "location_id": source_location.id,
            "location_dest_id": destination_location.id,
            "location_final_id": self.warehouse_id.lot_stock_id.id,
            "picking_id": picking.id,
            "company_id": self.company_id.id,
            "warehouse_id": warehouse.id,
            "date": self.expected_date,
            "propagate_cancel": True,
            "stock_request_line_id": request.id,
        }
        if delivery_move:
            values.update(
                {
                    "procure_method": "make_to_order",
                    "move_orig_ids": [Command.link(delivery_move.id)],
                    "allocation_ids": [
                        Command.create(
                            {
                                "stock_request_id": request.id,
                                "requested_product_uom_qty": (
                                    request.product_uom_qty
                                ),
                            }
                        )
                    ],
                }
            )
        return values

    def _prepare_one_step_picking_values(self):
        self.ensure_one()
        return {
            "picking_type_id": self.source_warehouse_id.int_type_id.id,
            "location_id": self.source_warehouse_id.lot_stock_id.id,
            "location_dest_id": self.warehouse_id.lot_stock_id.id,
            "scheduled_date": self.expected_date,
            "origin": self.name,
            "priority": self.priority,
            "company_id": self.company_id.id,
            "stock_request_order_id": self.id,
            "stock_request_operation": "transfer",
        }

    def _prepare_one_step_move_values(self, request, picking):
        self.ensure_one()
        return {
            "origin": self.name,
            "product_id": request.product_id.id,
            "product_uom_qty": request.product_uom_qty,
            "product_uom": request.product_uom_id.id,
            "location_id": self.source_warehouse_id.lot_stock_id.id,
            "location_dest_id": self.warehouse_id.lot_stock_id.id,
            "location_final_id": self.warehouse_id.lot_stock_id.id,
            "picking_id": picking.id,
            "company_id": self.company_id.id,
            "warehouse_id": self.source_warehouse_id.id,
            "date": self.expected_date,
            "propagate_cancel": True,
            "stock_request_line_id": request.id,
            "allocation_ids": [
                Command.create(
                    {
                        "stock_request_id": request.id,
                        "requested_product_uom_qty": request.product_uom_qty,
                    }
                )
            ],
        }

    def _prepare_no_transit_receipt_move_values(self, request, picking, quantity):
        self.ensure_one()
        return {
            "origin": self.name,
            "product_id": request.product_id.id,
            "product_uom_qty": quantity,
            "product_uom": request.product_uom_id.id,
            "location_id": self.source_warehouse_id.lot_stock_id.id,
            "location_dest_id": self.warehouse_id.lot_stock_id.id,
            "location_final_id": self.warehouse_id.lot_stock_id.id,
            "picking_id": picking.id,
            "company_id": self.company_id.id,
            "warehouse_id": self.source_warehouse_id.id,
            "date": self.expected_date,
            "propagate_cancel": True,
            "stock_request_line_id": request.id,
            "allocation_ids": [
                Command.create(
                    {
                        "stock_request_id": request.id,
                        "requested_product_uom_qty": quantity,
                    }
                )
            ],
        }

    def _create_one_step_internal_transfers(self, requests=None):
        created_pickings = self.env["stock.picking"]
        for order in self:
            order_requests = (
                requests.filtered(lambda request: request.order_id == order)
                if requests is not None
                else order.stock_request_ids
            )
            order_requests = order_requests.filtered(
                lambda request: request.state == "draft"
            )
            if not order_requests:
                continue
            order._check_stock_request_operation_configuration()
            picking = self.env["stock.picking"].create(
                order._prepare_one_step_picking_values()
            )
            moves = self.env["stock.move"].create(
                [
                    order._prepare_one_step_move_values(request, picking)
                    for request in order_requests
                ]
            )
            moves._action_confirm(merge=False)
            picking.action_assign()
            created_pickings |= picking
        return created_pickings

    def _create_two_step_internal_transfers(self, requests=None):
        created_pickings = self.env["stock.picking"]
        for order in self:
            order_requests = (
                requests.filtered(lambda request: request.order_id == order)
                if requests is not None
                else order.stock_request_ids
            )
            order_requests = order_requests.filtered(
                lambda request: request.state == "draft"
            )
            if not order_requests:
                continue
            order._lock_transit_location()
            order._check_two_step_internal_transfer_configuration()
            transit_location = order._get_effective_transit_location()
            delivery_picking, receipt_picking = self.env["stock.picking"].create(
                [
                    order._prepare_two_step_picking_values("delivery"),
                    order._prepare_two_step_picking_values("receipt"),
                ]
            )
            delivery_moves = self.env["stock.move"].create(
                [
                    order._prepare_two_step_move_values(
                        request,
                        delivery_picking,
                        order.source_warehouse_id.lot_stock_id,
                        transit_location,
                    )
                    for request in order_requests
                ]
            )
            receipt_moves = self.env["stock.move"].create(
                [
                    order._prepare_two_step_move_values(
                        request,
                        receipt_picking,
                        transit_location,
                        order.warehouse_id.lot_stock_id,
                        delivery_move,
                    )
                    for request, delivery_move in zip(
                        order_requests, delivery_moves
                    )
                ]
            )
            (delivery_moves | receipt_moves)._action_confirm(merge=False)
            delivery_picking.action_assign()
            created_pickings |= delivery_picking | receipt_picking
        return created_pickings

    def _create_internal_transfers(self, requests=None):
        one_step_orders = self.filtered(
            lambda order: order.transfer_flow == "one_step"
        )
        two_step_orders = self.filtered(
            lambda order: order.transfer_flow == "two_step"
        )
        return (
            one_step_orders._create_one_step_internal_transfers(requests)
            | two_step_orders._create_two_step_internal_transfers(requests)
        )

    def _record_no_transit_delivery(self):
        self.ensure_one()
        self._check_source_responsible_access()
        self.lock_for_update(allow_referencing=True)
        self.invalidate_recordset(["state", "has_pending_delivery"])
        self.stock_request_ids.invalidate_recordset(
            ["delivered_qty", "remaining_delivery_qty"]
        )
        if (
            self.transfer_flow != "two_step_no_transit"
            or self.state not in ("ready", "in_transit")
            or not self.has_pending_delivery
        ):
            raise UserError(
                _("There are no remaining products ready to deliver.")
            )
        if self.stock_request_operation_picking_ids:
            raise UserError(
                _(
                    "Two Step without Transit cannot be delivered when stock "
                    "transfers already exist."
                )
            )

        delivery_date = fields.Datetime.now()
        values = {
            "state": "in_transit",
            "delivered_by_id": self.env.user.id,
            "delivery_date": delivery_date,
        }
        if not self.first_delivery_date:
            values["first_delivery_date"] = delivery_date
            if self.ready_date:
                values["preparation_time"] = self._analysis_duration_days(
                    self.ready_date,
                    delivery_date,
                )
        self.sudo().write(values)
        self.sudo().message_post(
            body=Markup("<p>%s</p>")
            % _(
                "%(user)s recorded the administrative delivery on %(date)s.",
                user=self.env.user.display_name,
                date=fields.Datetime.to_string(delivery_date),
            ),
            author_id=self.env.user.partner_id.id,
            subtype_xmlid="mail.mt_note",
        )
        self._schedule_receipt_activities()
        return True

    def _open_no_transit_receipt_wizard(self):
        self.ensure_one()
        wizard = self.env["stock.request.receive.wizard"].create(
            {
                "order_id": self.id,
                "line_ids": [
                    Command.create(
                        {
                            "stock_request_id": request.id,
                            "remaining_quantity": request.remaining_receipt_qty,
                            "quantity": request.remaining_receipt_qty,
                        }
                    )
                    for request in self.stock_request_ids
                    if float_compare(
                        request.remaining_receipt_qty,
                        0.0,
                        precision_rounding=request.product_uom_id.rounding,
                    )
                    > 0
                ],
            }
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Receive Products"),
            "res_model": "stock.request.receive.wizard",
            "view_mode": "form",
            "views": [
                (
                    self.env.ref(
                        "stock_request.stock_request_receive_wizard_form"
                    ).id,
                    "form",
                )
            ],
            "res_id": wizard.id,
            "target": "new",
        }

    def _create_no_transit_receipt(self, quantities):
        self.ensure_one()
        self._check_destination_responsible_access()
        self.lock_for_update(allow_referencing=True)
        self.invalidate_recordset(["state", "has_pending_receipt"])
        self.stock_request_ids.invalidate_recordset(
            ["received_qty", "remaining_receipt_qty"]
        )
        if (
            self.transfer_flow != "two_step_no_transit"
            or self.state != "in_transit"
            or not self.has_pending_receipt
        ):
            raise UserError(
                _("There are no delivered products ready to receive.")
            )
        pending_pickings = self.stock_request_operation_picking_ids.filtered(
            lambda picking: picking.state not in ("done", "cancel")
        )
        if pending_pickings:
            raise UserError(
                _("Another receipt for this Stock Request is still pending.")
            )

        requests_by_id = {
            request.id: request for request in self.stock_request_ids
        }
        selected_quantities = []
        seen_request_ids = set()
        for request_id, quantity, expected_remaining in quantities:
            request = requests_by_id.get(request_id)
            if not request or request_id in seen_request_ids:
                raise UserError(_("The receipt lines are invalid."))
            seen_request_ids.add(request_id)
            rounding = request.product_uom_id.rounding
            if (
                float_compare(
                    request.remaining_receipt_qty,
                    expected_remaining,
                    precision_rounding=rounding,
                )
                != 0
            ):
                raise UserError(
                    _(
                        "The remaining quantities changed. Close this window "
                        "and open Receive Products again."
                    )
                )
            if float_compare(quantity, 0.0, precision_rounding=rounding) < 0:
                raise UserError(
                    _("Received quantities cannot be negative.")
                )
            if float_compare(quantity, 0.0, precision_rounding=rounding) == 0:
                continue
            if (
                float_compare(
                    quantity,
                    request.remaining_receipt_qty,
                    precision_rounding=rounding,
                )
                > 0
            ):
                raise UserError(
                    _(
                        "The received quantity for %(product)s exceeds the "
                        "remaining quantity.",
                        product=request.product_id.display_name,
                    )
                )
            selected_quantities.append((request, quantity))
        if not selected_quantities:
            raise UserError(_("Enter a received quantity for at least one product."))

        self._check_stock_request_operation_configuration()
        picking = self.env["stock.picking"].create(
            {
                **self._prepare_one_step_picking_values(),
                "stock_request_operation": "receipt",
            }
        )
        moves = self.env["stock.move"].create(
            [
                self._prepare_no_transit_receipt_move_values(
                    request,
                    picking,
                    quantity,
                )
                for request, quantity in selected_quantities
            ]
        )
        moves._action_confirm(merge=False)
        picking.action_assign()
        for move in moves.filtered(
            lambda move: move.state not in ("done", "cancel")
            and float_compare(
                move.quantity,
                move.product_uom_qty,
                precision_rounding=move.product_uom.rounding,
            )
            < 0
        ):
            move.quantity = move.product_uom_qty
        picking.move_line_ids.write({"picked": True})
        picking._action_done()
        return picking

    def _complete_one_step_transfers(self):
        """Reserve and complete direct warehouse transfers in the same action."""
        for order in self:
            if (
                order.operation_type != "transfer"
                or order.transfer_flow != "one_step"
            ):
                raise UserError(
                    _("Only One Step transfers can be completed directly.")
                )
            pickings = order.stock_request_operation_picking_ids.filtered(
                lambda picking: picking.stock_request_operation == "transfer"
                and picking.state not in ("done", "cancel")
            )
            if not pickings:
                raise UserError(_("No direct transfer is ready to complete."))
            pickings.action_assign()
            for move in pickings.move_ids.filtered(
                lambda move: move.state not in ("done", "cancel")
                and float_compare(
                    move.quantity,
                    move.product_uom_qty,
                    precision_rounding=move.product_uom.rounding,
                )
                < 0
            ):
                move.quantity = move.product_uom_qty
            pickings.move_line_ids.write({"picked": True})
            pickings._action_done()
        return True

    def _get_returned_quantities_by_product(self):
        """Return completed return quantities in each product's reference UOM."""
        self.ensure_one()
        quantities = {}
        completed_returns = self.return_ids.filtered(
            lambda order: order.state == "done"
        )
        for line in completed_returns.stock_request_ids:
            quantities[line.product_id.id] = quantities.get(line.product_id.id, 0.0) + (
                line.product_uom_id._compute_quantity(
                    line.product_uom_qty,
                    line.product_id.uom_id,
                    rounding_method="HALF-UP",
                )
            )
        return quantities

    def _prepare_return_line_commands(self):
        self.ensure_one()
        returned_quantities = self._get_returned_quantities_by_product()
        commands = []
        for line in self.stock_request_ids:
            returned_qty = returned_quantities.get(line.product_id.id, 0.0)
            original_qty = line.product_uom_id._compute_quantity(
                line.product_uom_qty,
                line.product_id.uom_id,
                rounding_method="HALF-UP",
            )
            return_qty = max(original_qty - returned_qty, 0.0)
            returned_quantities[line.product_id.id] = max(
                returned_qty - original_qty, 0.0
            )
            if float_compare(
                return_qty,
                0.0,
                precision_rounding=line.product_id.uom_id.rounding,
            ) <= 0:
                continue
            commands.append(
                Command.create(
                    {
                        "product_id": line.product_id.id,
                        "product_uom_id": line.product_uom_id.id,
                        "product_uom_qty": line.product_id.uom_id._compute_quantity(
                            return_qty,
                            line.product_uom_id,
                            rounding_method="HALF-UP",
                        ),
                    }
                )
            )
        return commands

    def _check_return_quantities(self):
        for order in self.filtered("is_return"):
            original = order.original_transfer_id
            original_quantities = {}
            for line in original.stock_request_ids:
                original_quantities[line.product_id.id] = original_quantities.get(
                    line.product_id.id, 0.0
                ) + line.product_uom_id._compute_quantity(
                    line.product_uom_qty,
                    line.product_id.uom_id,
                    rounding_method="HALF-UP",
                )
            returned_quantities = original._get_returned_quantities_by_product()
            current_quantities = {}
            for line in order.stock_request_ids:
                product = line.product_id
                if product.id not in original_quantities:
                    raise UserError(
                        _(
                            "Product %(product)s is not part of original "
                            "transfer %(transfer)s.",
                            product=product.display_name,
                            transfer=original.display_name,
                        )
                    )
                current_quantities[product.id] = current_quantities.get(
                    product.id, 0.0
                ) + line.product_uom_id._compute_quantity(
                    line.product_uom_qty,
                    product.uom_id,
                    rounding_method="HALF-UP",
                )
            for product_id, current_qty in current_quantities.items():
                product = self.env["product.product"].browse(product_id)
                if float_compare(
                    returned_quantities.get(product_id, 0.0) + current_qty,
                    original_quantities[product_id],
                    precision_rounding=product.uom_id.rounding,
                ) > 0:
                    raise UserError(
                        _(
                            "The returned quantity for %(product)s exceeds "
                            "the quantity in original transfer %(transfer)s.",
                            product=product.display_name,
                            transfer=original.display_name,
                        )
                    )
        return True

    def action_confirm(self):
        self._check_source_responsible_access()
        self._check_confirmation_state()
        self.filtered("is_return")._check_return_quantities()
        accepting_user = self.env.user
        self._check_stock_request_operation_configuration()
        self.filtered(
            lambda order: order.transfer_flow == "two_step"
        )._lock_transit_location()
        self._sync_request_lines()
        self.mapped("stock_request_ids").action_confirm()
        ready_date = fields.Datetime.now()
        for order in self:
            values = {
                "state": "ready",
                "ready_date": ready_date,
                "accepted_by_id": accepting_user.id,
                "rejection_reason": False,
            }
            if order.operation_type == "request" and order.submitted_date:
                values["review_time"] = self._analysis_duration_days(
                    order.submitted_date,
                    ready_date,
                )
            order.sudo().write(values)
        accepted_requests = self.filtered(
            lambda order: order.operation_type == "request"
        )
        accepted_requests._complete_activities(
            "stock_request.mail_activity_type_stock_request_review",
            _("Stock request accepted."),
        )
        accepted_requests._notify_requester(
            lambda order: _(
                "Stock Request %(request)s has been accepted.",
                request=order.display_name,
            ),
            author_user=accepting_user,
        )
        self.filtered(
            lambda order: order.operation_type == "transfer"
            and order.transfer_flow == "one_step"
        )._complete_one_step_transfers()
        return True

    def action_confirm_one_step_transfer(self):
        self._check_source_responsible_access()
        invalid_orders = self.filtered(
            lambda order: order.operation_type != "transfer"
            or order.transfer_flow != "one_step"
            or order.is_return
            or order.state not in ("draft", "ready")
        )
        if invalid_orders:
            raise UserError(
                _("Only Draft One Step transfers can be confirmed.")
            )
        draft_orders = self.filtered(lambda order: order.state == "draft")
        ready_orders = self - draft_orders
        if draft_orders:
            draft_orders.action_confirm()
        if ready_orders:
            ready_orders._complete_one_step_transfers()
        return True

    def action_confirm_one_step_return(self):
        self._check_source_responsible_access()
        invalid_orders = self.filtered(
            lambda order: not order.is_return
            or order.operation_type != "transfer"
            or order.transfer_flow != "one_step"
            or order.state not in ("draft", "ready")
        )
        if invalid_orders:
            raise UserError(_("Only Draft One Step returns can be confirmed."))
        self._check_return_quantities()
        draft_orders = self.filtered(lambda order: order.state == "draft")
        ready_orders = self - draft_orders
        if draft_orders:
            draft_orders.action_confirm()
        if ready_orders:
            ready_orders._complete_one_step_transfers()
        return True

    def action_create_return(self):
        self.ensure_one()
        if self.is_return or self.operation_type != "transfer" or self.state != "done":
            raise UserError(_("Only a completed One Step transfer can be returned."))
        if (
            self.transfer_flow != "one_step"
            or self._get_configured_transfer_flow() != "one_step"
        ):
            raise UserError(
                _("Returns are available only while One Step flow is enabled.")
            )
        line_commands = self._prepare_return_line_commands()
        if not line_commands:
            raise UserError(
                _("All products in this transfer have already been returned.")
            )
        return_order = self.with_context(default_is_return=True).create(
            {
                "is_return": True,
                "original_transfer_id": self.id,
                "operation_type": "transfer",
                "company_id": self.company_id.id,
                "source_warehouse_id": self.warehouse_id.id,
                "warehouse_id": self.source_warehouse_id.id,
                "priority": self.priority,
                "stock_request_ids": line_commands,
            }
        )
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "stock_request.stock_request_one_step_return_action"
        )
        action.update(
            {
                "views": [
                    (
                        self.env.ref(
                            "stock_request.stock_request_one_step_return_form"
                        ).id,
                        "form",
                    )
                ],
                "view_mode": "form",
                "res_id": return_order.id,
                "target": "current",
            }
        )
        return action

    def action_view_returns(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "stock_request.stock_request_one_step_return_action"
        )
        action["domain"] = [("original_transfer_id", "=", self.id)]
        action["context"] = {
            "default_is_return": True,
            "default_operation_type": "transfer",
            "default_original_transfer_id": self.id,
        }
        return action

    def action_view_original_transfer(self):
        self.ensure_one()
        if not self.original_transfer_id:
            raise UserError(_("No original transfer is linked to this return."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Original Transfer"),
            "res_model": "stock.request.order",
            "res_id": self.original_transfer_id.id,
            "view_mode": "form",
            "views": [
                (
                    self.env.ref(
                        "stock_request.stock_request_one_step_transfer_form"
                    ).id,
                    "form",
                )
            ],
            "target": "current",
        }

    def action_view_source_request(self):
        self.ensure_one()
        if not self.source_request_id:
            raise UserError(_("No Source Request is linked to this Direct Transfer."))
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "stock_request.stock_request_order_action"
        )
        action.update(
            {
                "name": _("Source Request"),
                "views": [
                    (
                        self.env.ref(
                            "stock_request.stock_request_order_form"
                        ).id,
                        "form",
                    )
                ],
                "view_mode": "form",
                "res_id": self.source_request_id.id,
                "domain": [],
                "target": "current",
            }
        )
        return action

    def action_view_direct_transfers(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "stock_request.stock_request_direct_transfer_action"
        )
        action["domain"] = [("source_request_id", "=", self.id)]
        action["context"] = {
            "default_operation_type": "transfer",
            "default_source_request_id": self.id,
            "default_company_id": self.company_id.id,
        }
        transfers = self.direct_transfer_ids
        if len(transfers) == 1:
            form_view = (
                self.env.ref(
                    "stock_request.stock_request_one_step_transfer_form"
                )
                if transfers.transfer_flow == "one_step" and not transfers.is_return
                else self.env.ref("stock_request.stock_request_order_form")
            )
            action.update(
                {
                    "views": [(form_view.id, "form")],
                    "view_mode": "form",
                    "res_id": transfers.id,
                }
            )
        return action

    def action_cancel(self):
        is_manager = self.env.su or self.env.user.has_group(
            "stock_request.group_stock_request_manager"
        )
        if is_manager:
            if self.filtered(
                lambda order: order.state in ("done", "rejected", "cancelled")
            ):
                raise UserError(
                    _("Received, rejected, or cancelled requests cannot be cancelled.")
                )
        else:
            if self.filtered(lambda order: order.state != "draft"):
                raise UserError(_("Only draft requests can be cancelled."))
            self._check_requester_access()
        self.mapped("stock_request_ids").action_cancel()
        self.sudo().write(
            {"state": "cancelled", "cancelled_date": fields.Datetime.now()}
        )
        self._complete_activities(
            "stock_request.mail_activity_type_stock_request_review",
            _("Stock request cancelled."),
        )
        self._complete_activities(
            "stock_request.mail_activity_type_stock_request_receipt",
            _("Stock request cancelled."),
        )
        return True

    def _get_operation_pickings(self, operation):
        self.ensure_one()
        pickings = self.env["stock.picking"].search(
            [
                ("stock_request_order_id", "=", self.id),
                ("stock_request_operation", "=", operation),
                ("state", "!=", "cancel"),
            ]
        )
        pending_pickings = pickings.filtered(lambda picking: picking.state != "done")
        return pending_pickings or pickings

    def _action_open_operation_picking(self, operation):
        self.ensure_one()
        pickings = self._get_operation_pickings(operation)
        if not pickings:
            raise UserError(_("No related transfer was found."))
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "stock.action_picking_tree_all"
        )
        if len(pickings) > 1:
            action["domain"] = [("id", "in", pickings.ids)]
        else:
            action["views"] = [
                (self.env.ref("stock.view_picking_form").id, "form")
            ]
            action["res_id"] = pickings.id
        return action

    def _action_view_operation_pickings(self, operation):
        self.ensure_one()
        pickings = self.stock_request_operation_picking_ids.filtered(
            lambda picking: picking.stock_request_operation == operation
        )
        if not pickings:
            raise UserError(_("No related transfer was found."))
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "stock.action_picking_tree_all"
        )
        action["domain"] = [("id", "in", pickings.ids)]
        if len(pickings) == 1:
            action["views"] = [
                (self.env.ref("stock.view_picking_form").id, "form")
            ]
            action["res_id"] = pickings.id
        return action

    def action_view_deliveries(self):
        return self._action_view_operation_pickings("delivery")

    def action_view_receipts(self):
        return self._action_view_operation_pickings("receipt")

    def action_view_transfers(self):
        return self._action_view_operation_pickings("transfer")

    def _check_print_stock_request_access(self):
        """Allow printing only to users related to the operation."""
        if self.env.su or self.env.user.has_group(
            "stock_request.group_stock_request_manager"
        ):
            return True
        unauthorized = self.filtered(
            lambda order: self.env.user
            not in (
                order.requested_by
                | order.source_responsible_user_ids
                | order.destination_responsible_user_ids
            )
        )
        if unauthorized:
            raise AccessError(
                _(
                    "Only the requester, a responsible user of the source or "
                    "destination warehouse, or a Stock Request Manager can "
                    "print this document."
                )
            )
        return True

    def action_print_stock_request(self):
        self._check_print_stock_request_access()
        return self.env.ref(
            "stock_request.action_report_stock_request_order"
        ).report_action(self)

    def action_deliver_products(self):
        self.ensure_one()
        if self.state not in ("ready", "in_transit") or not self.has_pending_delivery:
            raise UserError(
                _("There are no remaining products ready to deliver.")
            )
        self._check_source_responsible_access()
        if self.transfer_flow == "two_step_no_transit":
            return self._record_no_transit_delivery()
        return self._action_open_operation_picking("delivery")

    def action_transfer_products(self):
        self.ensure_one()
        if (
            self.transfer_flow != "one_step"
            or self.state not in ("ready", "in_transit")
            or not self.has_pending_delivery
        ):
            raise UserError(
                _("There are no remaining products ready to transfer.")
            )
        self._check_source_responsible_access()
        return self._action_open_operation_picking("transfer")

    def action_receive_products(self):
        self.ensure_one()
        if self.state != "in_transit" or not self.has_pending_receipt:
            raise UserError(
                _("There are no delivered products ready to receive.")
            )
        self._check_destination_responsible_access()
        if self.transfer_flow == "two_step_no_transit":
            return self._open_no_transit_receipt_wizard()
        return self._action_open_operation_picking("receipt")

    def action_close_remaining_receipt(self):
        self.ensure_one()
        self._check_destination_responsible_access()
        if not self.can_close_remaining_receipt:
            raise UserError(
                _(
                    "The remaining receipt can only be closed after a partial "
                    "receipt while a quantity is still pending."
                )
            )
        wizard = self.env["stock.request.close.receipt.wizard"].create(
            {
                "order_id": self.id,
                "line_ids": [
                    Command.create(
                        {
                            "stock_request_id": request.id,
                            "received_quantity": request.received_qty,
                            "remaining_quantity": request.remaining_receipt_qty,
                        }
                    )
                    for request in self.stock_request_ids
                    if float_compare(
                        request.remaining_receipt_qty,
                        0.0,
                        precision_rounding=request.product_uom_id.rounding,
                    )
                    > 0
                ],
            }
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Close Remaining Receipt"),
            "res_model": "stock.request.close.receipt.wizard",
            "view_mode": "form",
            "views": [
                (
                    self.env.ref(
                        "stock_request.stock_request_close_receipt_wizard_form"
                    ).id,
                    "form",
                )
            ],
            "res_id": wizard.id,
            "target": "new",
        }

    def _close_remaining_receipt(self, quantities):
        self.ensure_one()
        self._check_destination_responsible_access()
        self.lock_for_update(allow_referencing=True)
        self.invalidate_recordset(
            [
                "state",
                "has_pending_delivery",
                "has_pending_receipt",
                "can_close_remaining_receipt",
            ]
        )
        self.stock_request_ids.invalidate_recordset(
            [
                "delivered_qty",
                "received_qty",
                "cancelled_receipt_qty",
                "remaining_delivery_qty",
                "remaining_receipt_qty",
                "in_transit_qty",
            ]
        )
        if (
            self.transfer_flow not in ("two_step", "two_step_no_transit")
            or self.state != "in_transit"
            or not self.can_close_remaining_receipt
        ):
            raise UserError(
                _(
                    "The remaining receipt can only be closed after a partial "
                    "receipt while a quantity is still pending."
                )
            )

        requests_by_id = {
            request.id: request for request in self.stock_request_ids
        }
        closure_lines = []
        seen_request_ids = set()
        for request_id, expected_received, expected_remaining in quantities:
            request = requests_by_id.get(request_id)
            if not request or request_id in seen_request_ids:
                raise UserError(_("The receipt closure lines are invalid."))
            seen_request_ids.add(request_id)
            rounding = request.product_uom_id.rounding
            if (
                float_compare(
                    request.received_qty,
                    expected_received,
                    precision_rounding=rounding,
                )
                or float_compare(
                    request.remaining_receipt_qty,
                    expected_remaining,
                    precision_rounding=rounding,
                )
            ):
                raise UserError(
                    _(
                        "The receipt quantities changed. Close this window "
                        "and open Close Remaining Receipt again."
                    )
                )
            if (
                float_compare(
                    request.remaining_receipt_qty,
                    0.0,
                    precision_rounding=rounding,
                )
                <= 0
            ):
                raise UserError(_("The receipt closure lines are invalid."))
            closure_lines.append(
                (request, request.received_qty, request.remaining_receipt_qty)
            )

        if not closure_lines or seen_request_ids != {
            request.id
            for request in self.stock_request_ids
            if float_compare(
                request.remaining_receipt_qty,
                0.0,
                precision_rounding=request.product_uom_id.rounding,
            )
            > 0
        }:
            raise UserError(_("The receipt closure lines are invalid."))

        pending_delivery_lines = self.stock_request_ids.filtered(
            lambda request: float_compare(
                request.remaining_delivery_qty,
                0.0,
                precision_rounding=request.product_uom_id.rounding,
            )
            > 0
        )
        if pending_delivery_lines:
            details = ", ".join(
                _(
                    "%(product)s: %(quantity)s %(uom)s",
                    product=request.product_id.display_name,
                    quantity=request.remaining_delivery_qty,
                    uom=request.product_uom_id.display_name,
                )
                for request in pending_delivery_lines
            )
            raise UserError(
                _(
                    "The remaining receipt cannot be closed while source "
                    "delivery work is still pending: %(details)s.",
                    details=details,
                )
            )
        pending_source_operations = (
            self.stock_request_operation_picking_ids.filtered(
                lambda picking: picking.stock_request_operation
                in ("delivery", "transfer")
                and picking.state not in ("done", "cancel")
            )
        )
        if pending_source_operations:
            raise UserError(
                _(
                    "A source delivery operation is still pending. Resolve it "
                    "before closing the remaining receipt."
                )
            )

        if self.transfer_flow == "two_step":
            unresolved_lines = self.stock_request_ids.filtered(
                lambda request: float_compare(
                    request.in_transit_qty,
                    0.0,
                    precision_rounding=request.product_uom_id.rounding,
                )
                > 0
            )
            if unresolved_lines:
                details = ", ".join(
                    _(
                        "%(product)s: %(quantity)s %(uom)s",
                        product=request.product_id.display_name,
                        quantity=request.in_transit_qty,
                        uom=request.product_uom_id.display_name,
                    )
                    for request in unresolved_lines
                )
                raise UserError(
                    _(
                        "The remaining receipt cannot be closed because "
                        "delivered stock is still unresolved in the transit "
                        "location: %(details)s. Complete the existing receipt "
                        "or resolve it through the supported return/correction "
                        "procedure first.",
                        details=details,
                    )
                )

        closing_requests = self.env["stock.request"].browse(
            [request.id for request, _received, _remaining in closure_lines]
        )
        pending_receipt_pickings = self.stock_request_operation_picking_ids.filtered(
            lambda picking: picking.stock_request_operation == "receipt"
            and picking.state not in ("done", "cancel")
        )
        open_receipt_moves = pending_receipt_pickings.move_ids.filtered(
            lambda move: move.state not in ("done", "cancel")
        )
        pending_receipt_moves = open_receipt_moves.filtered(
            lambda move: move.stock_request_line_id in closing_requests
        )
        unrelated_receipt_moves = open_receipt_moves - pending_receipt_moves
        empty_pending_pickings = pending_receipt_pickings.filtered(
            lambda picking: not picking.move_ids.filtered(
                lambda move: move.state not in ("done", "cancel")
            )
        )
        if unrelated_receipt_moves or empty_pending_pickings:
            raise UserError(
                _(
                    "Another pending receipt operation is not part of this "
                    "remainder. Resolve it before closing the request."
                )
            )
        if pending_receipt_moves:
            pending_receipt_moves._action_cancel()

        closing_requests.invalidate_recordset(
            ["cancelled_receipt_qty", "remaining_receipt_qty"]
        )
        for request in closing_requests:
            remaining_qty = request.remaining_receipt_qty
            if (
                float_compare(
                    remaining_qty,
                    0.0,
                    precision_rounding=request.product_uom_id.rounding,
                )
                > 0
            ):
                request.sudo().write(
                    {
                        "closed_receipt_qty": (
                            request.closed_receipt_qty + remaining_qty
                        )
                    }
                )

        self.stock_request_ids.invalidate_recordset(
            ["cancelled_receipt_qty", "remaining_receipt_qty"]
        )
        self._sync_workflow_state_from_pickings()
        self.invalidate_recordset(
            ["state", "has_pending_receipt", "can_close_remaining_receipt"]
        )
        if self.state != "done" or self.has_pending_receipt:
            raise UserError(
                _(
                    "The request still has delivery or receipt work and "
                    "cannot be closed."
                )
            )

        details = Markup()
        for request, received_qty, remaining_qty in closure_lines:
            details += Markup("<li>%s</li>") % _(
                "%(product)s: actually received %(received)s %(uom)s; "
                "cancelled remainder %(cancelled)s %(uom)s.",
                product=request.product_id.display_name,
                received=received_qty,
                cancelled=remaining_qty,
                uom=request.product_uom_id.display_name,
            )
        self.sudo().message_post(
            body=(
                Markup("<p>%s</p>")
                % _(
                    "%(user)s closed the remaining receipt on %(date)s. The "
                    "cancelled quantities were not received.",
                    user=self.env.user.display_name,
                    date=fields.Datetime.to_string(fields.Datetime.now()),
                )
                + Markup("<ul>%s</ul>") % details
            ),
            author_id=self.env.user.partner_id.id,
            subtype_xmlid="mail.mt_note",
        )
        self._complete_activities(
            "stock_request.mail_activity_type_stock_request_receipt",
            _("Remaining receipt quantity cancelled."),
        )
        return True

    def _sync_workflow_state_from_pickings(self):
        picking_model = self.env["stock.picking"].sudo()
        for order in self.filtered(
            lambda item: item.state not in ("rejected", "cancelled")
        ):
            pickings = picking_model.search(
                [("stock_request_order_id", "=", order.id)]
            )
            delivery_pickings = pickings.filtered(
                lambda picking: picking.stock_request_operation == "delivery"
            )
            lines = order.stock_request_ids
            has_delivered_qty = any(
                float_compare(
                    line.delivered_qty,
                    0.0,
                    precision_rounding=line.product_uom_id.rounding,
                )
                > 0
                for line in lines
            )
            has_remaining_delivery = any(
                float_compare(
                    line.remaining_delivery_qty,
                    0.0,
                    precision_rounding=line.product_uom_id.rounding,
                )
                > 0
                for line in lines
            )
            has_remaining_receipt = any(
                float_compare(
                    line.remaining_receipt_qty,
                    0.0,
                    precision_rounding=line.product_uom_id.rounding,
                )
                > 0
                for line in lines
            )
            all_delivery_cancelled = lines and not has_delivered_qty and all(
                float_compare(
                    line.cancelled_delivery_qty,
                    line.product_uom_qty,
                    precision_rounding=line.product_uom_id.rounding,
                )
                >= 0
                for line in lines
            )
            if all_delivery_cancelled:
                values = {"state": "cancelled"}
                if not order.cancelled_date:
                    values["cancelled_date"] = fields.Datetime.now()
                order.sudo().write(values)
            elif has_delivered_qty and not (
                has_remaining_delivery or has_remaining_receipt
            ):
                values = {"state": "done"}
                values.update(order._prepare_completion_analysis_values())
                order.sudo().write(values)
            elif has_delivered_qty:
                order.sudo().state = (
                    "ready" if order.transfer_flow == "one_step" else "in_transit"
                )
            elif delivery_pickings:
                order.sudo().state = "ready"
        return True

    @api.model_create_multi
    def create(self, vals_list):
        vals_list_upd = []
        for vals in vals_list:
            upd_vals = self._prepare_order_values(vals)
            if upd_vals.get("name", "/") == "/":
                upd_vals["name"] = self.env["ir.sequence"].next_by_code(
                    "stock.request.order.return"
                    if upd_vals.get("is_return")
                    else "stock.request.order"
                )
            vals_list_upd.append(upd_vals)
        self._check_operation_create_access(vals_list_upd)
        orders = super().create(vals_list_upd)
        orders._sync_request_lines()
        return orders

    can_requester_edit_no_transit_lines = fields.Boolean(
        compute="_compute_requester_edit_no_transit_lines",
    )

    def _can_requester_edit_no_transit_lines(self):
        self.ensure_one()
        return (
            self.operation_type == "request"
            and self.transfer_flow == "two_step_no_transit"
            and self.state in ("draft", "submitted", "ready", "in_transit")
            and self.company_id in self.env.companies
            and (
                self.requested_by == self.env.user
                or self.env.user.has_group("stock_request.group_stock_request_manager")
                or (
                    self.env.user.has_group("stock_request.group_stock_request_warehouse_responsible")
                    and (
                        self.env.user in self.source_warehouse_id.stock_request_responsible_user_ids
                        or self.env.user in self.warehouse_id.stock_request_responsible_user_ids
                    )
                )
            )
        )

    @api.depends("operation_type", "transfer_flow", "state", "requested_by", "company_id",
                 "source_warehouse_id.stock_request_responsible_user_ids",
                 "warehouse_id.stock_request_responsible_user_ids")
    @api.depends_context("uid", "allowed_company_ids")
    def _compute_requester_edit_no_transit_lines(self):
        for order in self:
            order.can_requester_edit_no_transit_lines = order._can_requester_edit_no_transit_lines()

    def _is_requester_item_command(self, command):
        if not isinstance(command, (list, tuple)) or len(command) < 2:
            return False
        if command[0] == Command.CREATE:
            return self._is_review_quantity_command(command)
        if command[0] == Command.DELETE:
            return command[1] in self.stock_request_ids.ids
        if command[0] == Command.UPDATE and len(command) == 3:
            return command[1] in self.stock_request_ids.ids and isinstance(command[2], dict) and set(command[2]) <= {
                "product_id", "product_uom_id", "product_uom_qty",
            }
        return False

    def _is_review_quantity_command(self, command):
        if (
            isinstance(command, (list, tuple))
            and len(command) == 3
            and command[0] == Command.CREATE
            and isinstance(command[2], dict)
        ):
            return bool(command[2].get("product_id")) and set(command[2]) <= {
                "product_id", "product_uom_id", "product_uom_qty", "name",
                "expected_date", "warehouse_id", "location_id", "source_location_id",
                "route_id", "procurement_group_id", "company_id", "requested_by",
            }
        return (
            isinstance(command, (list, tuple))
            and len(command) == 3
            and command[0] == Command.UPDATE
            and command[1] in self.stock_request_ids.ids
            and isinstance(command[2], dict)
            and set(command[2]) == {"product_uom_qty"}
        )

    def _check_user_write_access(self, vals):
        if self.env.su:
            return
        if set(vals) == {"stock_request_ids"} and all(
            order._can_requester_edit_no_transit_lines() for order in self
        ) and all(self._is_requester_item_command(command) for command in vals["stock_request_ids"]):
            return
        if "transfer_flow" in vals:
            raise AccessError(
                _(
                    "The transfer flow is selected automatically from Stock "
                    "Transfer Settings when the operation is created."
                )
            )
        if "transit_location_id" in vals:
            raise AccessError(
                _(
                    "The transit location is selected automatically when the "
                    "operation is confirmed."
                )
            )
        if set(vals) & {
            "submitted_date",
            "ready_date",
            "first_delivery_date",
            "rejected_date",
            "cancelled_date",
            "accepted_by_id",
            "delivered_by_id",
            "delivery_date",
            "received_by_id",
            "receipt_date",
            "requests_count",
            "completed_requests_count",
            "rejected_requests_count",
            "cancelled_requests_count",
            "review_time",
            "preparation_time",
            "transit_time",
            "total_cycle_time",
        }:
            raise AccessError(
                _(
                    "Workflow dates, audit fields, and analysis metrics are "
                    "filled automatically."
                )
            )
        if "stock_request_ids" in vals and self.filtered(
            lambda order: order.state == "submitted"
        ):
            review_orders = self.filtered(
                lambda order: order.operation_type == "request"
                and order.state == "submitted"
            )
            commands = vals["stock_request_ids"]
            if (
                review_orders != self
                or set(vals) != {"stock_request_ids"}
                or not commands
                or not all(
                    review_orders._is_review_quantity_command(command)
                    for command in commands
                )
            ):
                raise AccessError(
                    _(
                        "Only Requested Quantity changes and new product lines are allowed "
                        "while a Stock Request is waiting for source warehouse "
                        "review."
                    )
                )
            review_orders._check_source_responsible_access()
            return
        if "rejection_reason" in vals:
            invalid = self.filtered(
                lambda order: order.operation_type != "request"
                or order.state != "submitted"
            )
            if invalid:
                raise AccessError(
                    _(
                        "The rejection reason can only be entered while a Stock "
                        "Request is waiting for source warehouse review."
                    )
                )
            self._check_source_responsible_access()
            if set(vals) == {"rejection_reason"}:
                return
        if "state" in vals:
            raise AccessError(
                _("The Stock Request status can only be changed by workflow actions.")
            )
        if self.env.user.has_group("stock_request.group_stock_request_manager"):
            return
        invalid = self.filtered(
            lambda order: order.requested_by != self.env.user
            or order.state != "draft"
        )
        if invalid:
            raise AccessError(
                _("You can only modify your own Stock Requests while they are Draft.")
            )
        if vals.get("requested_by") and vals["requested_by"] != self.env.uid:
            raise AccessError(
                _("Users cannot assign their Stock Requests to another requester.")
            )

    def write(self, vals):
        vals = vals.copy()
        self._check_user_write_access(vals)
        if "operation_type" in vals and any(
            order.operation_type != vals["operation_type"] for order in self
        ):
            raise UserError(_("The operation type cannot be changed after creation."))
        if (
            "source_warehouse_id" in vals
            and not self.env.su
            and not self.env.user.has_group(
                "stock_request.group_stock_request_manager"
            )
        ):
            source_warehouse = self.env["stock.warehouse"].browse(
                vals["source_warehouse_id"]
            )
            if self.filtered(
                lambda order: order.operation_type == "transfer"
                and self.env.user
                not in source_warehouse.stock_request_responsible_user_ids
            ):
                raise AccessError(
                    _(
                        "Only a responsible user of the source warehouse can "
                        "use it for a Direct Transfer."
                    )
                )
        if vals.get("warehouse_id"):
            warehouse = self.env["stock.warehouse"].browse(vals["warehouse_id"])
            vals.update(
                {
                    "company_id": warehouse.company_id.id,
                    "location_id": warehouse.lot_stock_id.id,
                }
            )
            if "source_warehouse_id" not in vals and all(
                order.state == "draft" for order in self
            ):
                operation_types = set(self.mapped("operation_type"))
                operation_type = (
                    operation_types.pop() if len(operation_types) == 1 else "request"
                )
                configured_source = self._get_configured_source_warehouse(
                    warehouse, operation_type
                )
                vals["source_warehouse_id"] = configured_source.id or False
        result = super().write(vals)
        if set(vals) & {
            "warehouse_id",
            "source_warehouse_id",
            "company_id",
            "expected_date",
            "requested_by",
            "procurement_group_id",
            "stock_request_ids",
        }:
            self._sync_request_lines()
        return result

    @api.model
    def _prepare_order_values(self, vals):
        vals = vals.copy()
        if not self.env.su:
            for protected_field in (
                "submitted_date",
                "ready_date",
                "first_delivery_date",
                "rejected_date",
                "cancelled_date",
                "accepted_by_id",
                "delivered_by_id",
                "delivery_date",
                "received_by_id",
                "receipt_date",
                "requests_count",
                "completed_requests_count",
                "rejected_requests_count",
                "cancelled_requests_count",
                "review_time",
                "preparation_time",
                "transit_time",
                "total_cycle_time",
                "rejection_reason",
                "transfer_flow",
                "transit_location_id",
                "is_return",
            ):
                if protected_field != "is_return" or not self.env.context.get(
                    "default_is_return"
                ):
                    vals.pop(protected_field, None)
        is_return = bool(
            vals.get("is_return") or self.env.context.get("default_is_return")
        )
        original_transfer = self.env["stock.request.order"].browse(
            vals.get("original_transfer_id")
            or self.env.context.get("default_original_transfer_id")
        )
        if is_return:
            if self._get_configured_transfer_flow() != "one_step":
                raise UserError(
                    _(
                        "Returns can be created only while One Step flow is "
                        "enabled."
                    )
                )
            if (
                not original_transfer
                or original_transfer.is_return
                or original_transfer.operation_type != "transfer"
                or original_transfer.transfer_flow != "one_step"
                or original_transfer.state != "done"
            ):
                raise UserError(_("Select a completed One Step transfer to return."))
            vals.update(
                {
                    "is_return": True,
                    "original_transfer_id": original_transfer.id,
                    "operation_type": "transfer",
                    "source_warehouse_id": original_transfer.warehouse_id.id,
                    "warehouse_id": original_transfer.source_warehouse_id.id,
                    "company_id": original_transfer.company_id.id,
                    "transfer_flow": "one_step",
                }
            )
        operation_type = (
            vals.get("operation_type")
            or self.env.context.get("default_operation_type")
            or "request"
        )
        vals["operation_type"] = operation_type
        warehouse = self.env["stock.warehouse"].browse(vals.get("warehouse_id"))
        company = self.env["res.company"].browse(vals.get("company_id"))
        if warehouse:
            company = warehouse.company_id
        elif not company:
            company = self.env.company
        if not warehouse:
            warehouse = self._get_default_destination_warehouse(company)
        source_warehouse = self.env["stock.warehouse"].browse(
            vals.get("source_warehouse_id")
        )
        if not source_warehouse or source_warehouse.company_id != company:
            source_warehouse = self._get_configured_source_warehouse(
                warehouse, operation_type
            )
        vals.update(
            {
                "company_id": company.id,
                "warehouse_id": warehouse.id,
                "location_id": warehouse.lot_stock_id.id,
                "source_warehouse_id": source_warehouse.id or False,
                "transfer_flow": (
                    "one_step" if is_return else self._get_configured_transfer_flow()
                ),
            }
        )
        if vals.get("stock_request_ids"):
            line_defaults = {
                "warehouse_id": warehouse.id,
                "location_id": warehouse.lot_stock_id.id,
                "source_location_id": source_warehouse.lot_stock_id.id or False,
                "company_id": company.id,
                "expected_date": vals.get("expected_date")
                or fields.Datetime.now(),
                "requested_by": vals.get("requested_by") or self.env.user.id,
                "procurement_group_id": vals.get("procurement_group_id") or False,
            }
            vals["stock_request_ids"] = [
                Command.create({**command[2], **line_defaults})
                if command[0] == Command.CREATE
                else command
                for command in vals["stock_request_ids"]
            ]
        return vals

    @api.onchange("original_transfer_id")
    def _onchange_original_transfer_id(self):
        for order in self.filtered("is_return"):
            original = order.original_transfer_id
            if not original:
                order.stock_request_ids = [Command.clear()]
                continue
            order.source_warehouse_id = original.warehouse_id
            order.warehouse_id = original.source_warehouse_id
            order.company_id = original.company_id
            order.priority = original.priority
            order.stock_request_ids = [
                Command.clear()
            ] + original._prepare_return_line_commands()

    @api.constrains(
        "is_return",
        "original_transfer_id",
        "source_warehouse_id",
        "warehouse_id",
        "company_id",
        "transfer_flow",
    )
    def _check_return_link(self):
        for order in self.filtered("is_return"):
            original = order.original_transfer_id
            if (
                not original
                or original.is_return
                or original.operation_type != "transfer"
                or original.transfer_flow != "one_step"
                or original.state != "done"
            ):
                raise ValidationError(
                    _("A return must be linked to a completed One Step transfer.")
                )
            if (
                order.transfer_flow != "one_step"
                or order.company_id != original.company_id
                or order.source_warehouse_id != original.warehouse_id
                or order.warehouse_id != original.source_warehouse_id
            ):
                raise ValidationError(
                    _("Return warehouses must be the reverse of the original transfer.")
                )

    def _sync_request_lines(self):
        for order in self:
            order.stock_request_ids.sudo().write(
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

    def unlink(self):
        if self.filtered(lambda r: r.state != "draft"):
            raise UserError(_("Only requests in draft state can be deleted"))
        if not self.env.su and not self.env.user.has_group(
            "stock_request.group_stock_request_manager"
        ):
            raise AccessError(_("Stock Requests must be cancelled instead of deleted."))
        return super().unlink()

    @api.constrains("warehouse_id", "source_warehouse_id", "company_id")
    def _check_warehouse_company(self):
        if any(
            request.warehouse_id.company_id != request.company_id
            or (
                request.source_warehouse_id
                and request.source_warehouse_id.company_id != request.company_id
            )
            for request in self
        ):
            raise ValidationError(
                _(
                    "The company of the stock request must match with "
                    "that of the warehouse."
                )
            )

    @api.constrains("location_id", "company_id")
    def _check_location_company(self):
        if any(
            request.location_id.company_id
            and request.location_id.company_id != request.company_id
            for request in self
        ):
            raise ValidationError(
                _(
                    "The company of the stock request must match with "
                    "that of the location."
                )
            )

    @api.constrains("source_location_id", "company_id")
    def _check_source_location_company(self):
        if any(
            request.source_location_id.company_id
            and request.source_location_id.company_id != request.company_id
            for request in self
        ):
            raise ValidationError(
                _(
                    "The company of the stock request must match with that of "
                    "the source location."
                )
            )

    @api.model
    def _create_from_product_multiselect(self, products):
        if not products:
            return False
        if products._name not in ("product.product", "product.template"):
            raise ValidationError(
                _("This action only works in the context of products")
            )
        if products._name == "product.template":
            # search instead of mapped so we don't include archived variants
            products = self.env["product.product"].search(
                [("product_tmpl_id", "in", products.ids)]
            )
        expected = self.default_get(["expected_date"])["expected_date"]
        order = self.env["stock.request.order"].create(
            dict(
                expected_date=expected,
                stock_request_ids=[
                    (
                        0,
                        0,
                        dict(
                            product_id=product.id,
                            product_uom_id=product.uom_id.id,
                            product_uom_qty=1.0,
                            expected_date=expected,
                        ),
                    )
                    for product in products
                ],
            )
        )
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "stock_request.stock_request_order_action"
        )
        action["views"] = [
            (self.env.ref("stock_request.stock_request_order_form").id, "form")
        ]
        action["res_id"] = order.id
        return action
