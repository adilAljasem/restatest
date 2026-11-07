# Copyright 2026 Mohamed Elmalky
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl.html).

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare, str2bool

from .res_config_settings import ALLOW_PURCHASE_LINE_WAREHOUSE_PARAMETER


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    stock_request_multi_warehouse_enabled = fields.Boolean(
        compute="_compute_stock_request_multi_warehouse_enabled",
        string="Purchase Line Warehouses Enabled",
    )

    @api.model
    def _stock_request_multi_warehouse_is_enabled(self):
        return str2bool(
            self.env["ir.config_parameter"]
            .sudo()
            .get_param(ALLOW_PURCHASE_LINE_WAREHOUSE_PARAMETER, "False")
        )

    def _compute_stock_request_multi_warehouse_enabled(self):
        enabled = self._stock_request_multi_warehouse_is_enabled()
        for order in self:
            order.stock_request_multi_warehouse_enabled = enabled

    def _stock_request_multi_warehouse_applies(self):
        self.ensure_one()
        return bool(
            self._stock_request_multi_warehouse_is_enabled()
            and self.picking_type_id.code == "incoming"
        )

    @api.onchange("picking_type_id")
    def _onchange_picking_type_id_stock_request_warehouse(self):
        for order in self.filtered(
            lambda rec: rec.picking_type_id.code == "incoming"
        ):
            if (
                order.requisition_id
                and order._stock_request_multi_warehouse_is_enabled()
            ):
                for line in order.order_line.filtered(
                    "stock_request_requisition_line_id"
                ):
                    line.warehouse_id = (
                        line.stock_request_requisition_line_id.warehouse_id
                    )

    def _stock_request_check_purchase_line_warehouses(self, lines):
        self.ensure_one()
        invalid_lines = lines.filtered(
            lambda line: line.warehouse_id
            and line.warehouse_id.company_id != self.company_id
        )
        if invalid_lines:
            raise ValidationError(
                _(
                    "The warehouse on every purchase order line must belong to "
                    "the purchase order company."
                )
            )
        missing_operation = lines.filtered(
            lambda line: not line._stock_request_get_receipt_picking_type()
            or not line._stock_request_get_receipt_picking_type().active
        )
        if missing_operation:
            raise UserError(
                _(
                    "Every purchase order line must use an active Receipts "
                    "operation type, either from its Warehouse or from the "
                    "purchase order's Deliver To field."
                )
            )

    def _stock_request_prepare_picking_for_type(self, picking_type):
        self.ensure_one()
        values = self._prepare_picking()
        values.update(
            {
                "picking_type_id": picking_type.id,
                "location_dest_id": picking_type.default_location_dest_id.id,
            }
        )
        return values

    def _stock_request_find_open_picking_for_type(self, picking_type):
        self.ensure_one()
        return self.picking_ids.filtered(
            lambda picking: picking.state not in ("done", "cancel")
            and picking.picking_type_id == picking_type
            and picking.location_dest_id == picking_type.default_location_dest_id
        )[:1]

    def _stock_request_get_or_create_picking_for_type(
        self, picking_type, use_superuser=False
    ):
        self.ensure_one()
        picking = self._stock_request_find_open_picking_for_type(picking_type)
        if picking:
            return picking, False
        picking_model = self.env["stock.picking"]
        if use_superuser:
            picking_model = picking_model.with_user(SUPERUSER_ID)
        picking = picking_model.create(
            self._stock_request_prepare_picking_for_type(picking_type)
        )
        return picking, True

    def _stock_request_create_warehouse_pickings(self):
        stock_lines = self.order_line.filtered(
            lambda line: not line.display_type
            and line.product_id.type == "consu"
        )
        self._stock_request_check_purchase_line_warehouses(stock_lines)
        lines_by_picking_type = {}
        for line in stock_lines:
            picking_type = line._stock_request_get_receipt_picking_type()
            picking_type_lines = lines_by_picking_type.setdefault(
                picking_type.id, self.env["purchase.order.line"]
            )
            lines_by_picking_type[picking_type.id] = picking_type_lines | line

        for picking_type_id, picking_type_lines in lines_by_picking_type.items():
            picking_type = self.env["stock.picking.type"].browse(picking_type_id)
            picking, created = (
                self._stock_request_get_or_create_picking_for_type(
                    picking_type, use_superuser=True
                )
            )
            sequence = max(picking.move_ids.mapped("sequence"), default=0)
            moves = picking_type_lines._create_stock_moves(picking)
            moves = moves.filtered(
                lambda move: move.state not in ("done", "cancel")
            )._action_confirm()
            for move in sorted(moves, key=lambda record: record.date):
                sequence += 5
                move.sequence = sequence
            moves._action_assign()
            forward_pickings = self.env["stock.picking"]._get_impacted_pickings(
                moves
            )
            (picking | forward_pickings).action_confirm()
            if created:
                picking.message_post_with_source(
                    "mail.message_origin_link",
                    render_values={"self": picking, "origin": self},
                    subtype_xmlid="mail.mt_note",
                )

    def _create_picking(self):
        for order in self:
            if (
                order.state != "purchase"
                or not order._stock_request_multi_warehouse_applies()
            ):
                super(PurchaseOrder, order)._create_picking()
                continue
            if any(
                product.type == "consu"
                for product in order.order_line.product_id
            ):
                order.with_company(
                    order.company_id
                )._stock_request_create_warehouse_pickings()
        return True


class PurchaseOrderLine(models.Model):
    _inherit = "purchase.order.line"

    warehouse_id = fields.Many2one(
        comodel_name="stock.warehouse",
        string="Warehouse",
        check_company=True,
        ondelete="restrict",
        domain="[('company_id', '=', company_id)]",
        help=(
            "Optional warehouse that will receive this purchase line. If empty, "
            "the purchase order's Deliver To operation type is used."
        ),
    )
    stock_request_requisition_line_id = fields.Many2one(
        comodel_name="purchase.requisition.line",
        string="Purchase Agreement Line",
        copy=False,
        index="btree_not_null",
        ondelete="set null",
    )

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            requisition_line_id = values.get(
                "stock_request_requisition_line_id"
            )
            if requisition_line_id and "warehouse_id" not in values:
                requisition_line = self.env["purchase.requisition.line"].browse(
                    requisition_line_id
                )
                values["warehouse_id"] = requisition_line.warehouse_id.id or False
        return super().create(vals_list)

    def write(self, values):
        if "warehouse_id" in values:
            new_warehouse_id = values.get("warehouse_id") or False
            protected_lines = self.filtered(
                lambda line: line.order_id.state in ("purchase", "done", "cancel")
                and line.warehouse_id.id != new_warehouse_id
            )
            if protected_lines:
                raise UserError(
                    _(
                        "You cannot change the warehouse of a purchase order "
                        "line after the order has been confirmed."
                    )
                )
        return super().write(values)

    @api.onchange("product_id")
    def _onchange_product_id_stock_request_warehouse(self):
        for line in self.filtered(
            lambda rec: rec.product_id and not rec.display_type
        ):
            if (
                line.order_id.requisition_id
                and line.order_id._stock_request_multi_warehouse_is_enabled()
            ):
                if line.stock_request_requisition_line_id:
                    line.warehouse_id = (
                        line.stock_request_requisition_line_id.warehouse_id
                    )

    @api.constrains("warehouse_id", "company_id")
    def _check_stock_request_purchase_warehouse_company(self):
        for line in self.filtered("warehouse_id"):
            if line.warehouse_id.company_id != line.company_id:
                raise ValidationError(
                    _(
                        "The purchase line warehouse must belong to the purchase "
                        "order company."
                    )
                )

    def _stock_request_multi_warehouse_applies(self):
        self.ensure_one()
        return self.order_id._stock_request_multi_warehouse_applies()

    def _stock_request_get_receipt_picking_type(self):
        self.ensure_one()

        if self.warehouse_id:
            return self.warehouse_id.in_type_id
        return self.order_id.picking_type_id

    def _prepare_stock_move_vals(
        self, picking, price_unit, product_uom_qty, product_uom
    ):
        values = super()._prepare_stock_move_vals(
            picking, price_unit, product_uom_qty, product_uom
        )
        if (
            not self._stock_request_multi_warehouse_applies()
            or not self.warehouse_id
        ):
            return values
        warehouse = self.warehouse_id
        location_dest = picking.location_dest_id
        location_final = self.location_final_id or warehouse.lot_stock_id
        if location_final and location_final._child_of(location_dest):
            location_dest = location_final
        values.update(
            {
                "location_dest_id": location_dest.id,
                "location_final_id": location_final.id,
                "picking_type_id": picking.picking_type_id.id,
                "warehouse_id": warehouse.id,
            }
        )
        return values

    def _stock_request_find_line_picking(self):
        self.ensure_one()
        picking_type = self._stock_request_get_receipt_picking_type()
        return self.move_ids.picking_id.filtered(
            lambda picking: picking.state not in ("done", "cancel")
            and picking.picking_type_id == picking_type
            and picking.location_dest_id == picking_type.default_location_dest_id
        )[:1]

    def _stock_request_create_or_update_warehouse_picking(self):
        self.ensure_one()
        line = self
        if line.product_id and line.product_id.type == "consu":
            line.order_id._stock_request_check_purchase_line_warehouses(line)
            picking_type = line._stock_request_get_receipt_picking_type()
            rounding = line.product_uom_id.rounding
            if (
                float_compare(
                    line.product_qty,
                    line.qty_invoiced,
                    precision_rounding=rounding,
                )
                < 0
                and line.invoice_lines
            ):
                line.invoice_lines[0].move_id.activity_schedule(
                    "mail.mail_activity_data_warning",
                    note=_(
                        "The quantities on your purchase order indicate less "
                        "than billed. You should ask for a refund."
                    ),
                    user_id=self.env.uid,
                )

            moves_to_assign = line.order_id.picking_ids.move_ids.filtered(
                lambda move: not move.purchase_line_id
                and line.product_id == move.product_id
                and move.picking_id.picking_type_id == picking_type
            )
            moves_to_assign.purchase_line_id = line.id
            picking = line._stock_request_find_line_picking()
            if not picking:
                picking = line.order_id._stock_request_find_open_picking_for_type(
                    picking_type
                )
            if not picking:
                if not line.product_qty > line.qty_received:
                    return
                picking, _created = (
                    line.order_id._stock_request_get_or_create_picking_for_type(
                        picking_type
                    )
                )
            moves = line._create_stock_moves(picking)
            moves._action_confirm()._action_assign()

    def _create_or_update_picking(self):
        multi_warehouse_lines = self.filtered(
            lambda line: line._stock_request_multi_warehouse_applies()
        )
        standard_lines = self - multi_warehouse_lines
        if standard_lines:
            super(PurchaseOrderLine, standard_lines)._create_or_update_picking()
        for line in multi_warehouse_lines:
            line._stock_request_create_or_update_warehouse_picking()
