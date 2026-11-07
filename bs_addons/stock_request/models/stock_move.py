# Copyright 2017-2020 ForgeFlow, S.L.
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl.html).

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare


class StockMove(models.Model):
    _inherit = "stock.move"

    stock_request_line_id = fields.Many2one(
        comodel_name="stock.request",
        string="Stock Request Line",
        index=True,
        check_company=True,
        ondelete="restrict",
    )
    allocation_ids = fields.One2many(
        comodel_name="stock.request.allocation",
        inverse_name="stock_move_id",
        string="Stock Request Allocation",
    )

    stock_request_ids = fields.One2many(
        comodel_name="stock.request",
        string="Stock Requests",
        compute="_compute_stock_request_ids",
    )

    @api.depends("allocation_ids")
    def _compute_stock_request_ids(self):
        for rec in self:
            rec.stock_request_ids = rec.allocation_ids.mapped("stock_request_id")

    def _merge_moves_fields(self):
        res = super()._merge_moves_fields()
        res["allocation_ids"] = [(4, m.id) for m in self.mapped("allocation_ids")]
        return res

    @api.constrains("company_id")
    def _check_company_stock_request(self):
        if any(
            self.env["stock.request.allocation"].search(
                [
                    ("company_id", "!=", rec.company_id.id),
                    ("stock_move_id", "=", rec.id),
                ],
                limit=1,
            )
            for rec in self
        ):
            raise ValidationError(
                _(
                    "The company of the stock request must match with "
                    "that of the location."
                )
            )

    def copy_data(self, default=None):
        if not default:
            default = {}
        if "allocation_ids" not in default:
            default["allocation_ids"] = []
        for alloc in self.allocation_ids:
            default["allocation_ids"].append(
                (
                    0,
                    0,
                    {
                        "stock_request_id": alloc.stock_request_id.id,
                        "requested_product_uom_qty": alloc.requested_product_uom_qty,
                    },
                )
            )
        return super().copy_data(default)

    def _action_cancel(self):
        """Apply sudo to prevent requests ACL errors if the user does not have
        permissions (example: productions)."""
        orders = self.mapped("picking_id.stock_request_order_id").sudo()
        res = super()._action_cancel()
        self.mapped("allocation_ids.stock_request_id").sudo().check_cancel()
        orders._sync_workflow_state_from_pickings()
        return res

    def _check_stock_request_receipt_quantities(self, cancel_backorder=False):
        """Only receive quantities already delivered by the linked request."""
        receipt_moves = self.filtered(
            lambda move: move.state not in ("done", "cancel")
            and move.picking_id.stock_request_operation == "receipt"
            and move.stock_request_line_id
        )
        moves_by_request = {}
        for move in receipt_moves:
            moves_by_request.setdefault(move.stock_request_line_id, self.browse())
            moves_by_request[move.stock_request_line_id] |= move

        for request, moves in moves_by_request.items():
            rounding = request.product_uom_id.rounding
            processed_qty = sum(
                move.product_uom._compute_quantity(
                    max(move.quantity, 0.0),
                    request.product_uom_id,
                    rounding_method="HALF-UP",
                )
                for move in moves
            )
            available_qty = request.remaining_receipt_qty
            if (
                float_compare(
                    processed_qty,
                    available_qty,
                    precision_rounding=rounding,
                )
                > 0
            ):
                raise UserError(
                    _(
                        "The received quantity for %(product)s exceeds the "
                        "quantity already delivered and still pending receipt "
                        "(%(quantity)s %(uom)s).",
                        product=request.product_id.display_name,
                        quantity=available_qty,
                        uom=request.product_uom_id.display_name,
                    )
                )

            if not cancel_backorder:
                continue

            planned_qty = sum(
                move.product_uom._compute_quantity(
                    move.product_uom_qty,
                    request.product_uom_id,
                    rounding_method="HALF-UP",
                )
                for move in moves
            )
            if (
                float_compare(
                    planned_qty,
                    processed_qty,
                    precision_rounding=rounding,
                )
                <= 0
            ):
                continue

            unresolved_sent_qty = max(available_qty - processed_qty, 0.0)
            if (
                float_compare(
                    unresolved_sent_qty,
                    0.0,
                    precision_rounding=rounding,
                )
                > 0
            ):
                raise UserError(
                    _(
                        "No Backorder cannot be used for %(product)s because "
                        "%(quantity)s %(uom)s has already been delivered and "
                        "is still pending receipt. Create a backorder and "
                        "process the remaining receipt, or use the existing "
                        "return/correction flow.",
                        product=request.product_id.display_name,
                        quantity=unresolved_sent_qty,
                        uom=request.product_uom_id.display_name,
                    )
                )
            if (
                float_compare(
                    request.remaining_delivery_qty,
                    0.0,
                    precision_rounding=rounding,
                )
                > 0
            ):
                raise UserError(
                    _(
                        "No Backorder cannot be used on this receipt while "
                        "%(quantity)s %(uom)s of %(product)s is still pending "
                        "delivery. Create a receipt backorder, or have the "
                        "source responsible user cancel the unshipped delivery "
                        "remainder first.",
                        quantity=request.remaining_delivery_qty,
                        uom=request.product_uom_id.display_name,
                        product=request.product_id.display_name,
                    )
                )
        return True

    def _action_done(self, cancel_backorder=False):
        """Apply sudo to prevent requests ACL errors if the user does not have
        permissions (example: productions)."""
        self._check_stock_request_receipt_quantities(
            cancel_backorder=cancel_backorder
        )
        res = super()._action_done(cancel_backorder=cancel_backorder)
        self.mapped("allocation_ids.stock_request_id").sudo().check_done()
        return res
