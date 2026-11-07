# Copyright 2017-2020 ForgeFlow, S.L.
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl.html).

from odoo import _, api, fields, models
from odoo.exceptions import AccessError
from odoo.tools import float_compare


class StockPicking(models.Model):
    _inherit = "stock.picking"

    stock_request_order_id = fields.Many2one(
        comodel_name="stock.request.order",
        string="Stock Request",
        index=True,
        check_company=True,
        ondelete="restrict",
    )
    stock_request_operation = fields.Selection(
        selection=[
            ("transfer", "Transfer"),
            ("delivery", "Delivery"),
            ("receipt", "Receipt"),
        ],
        string="Stock Request Operation",
        index=True,
    )
    stock_request_validated_by_id = fields.Many2one(
        comodel_name="res.users",
        string="Validated By",
        readonly=True,
        copy=False,
    )
    stock_request_validation_date = fields.Datetime(
        string="Validation Date",
        readonly=True,
        copy=False,
    )

    stock_request_ids = fields.One2many(
        comodel_name="stock.request",
        string="Stock Requests",
        compute="_compute_stock_request_ids",
    )
    stock_request_count = fields.Integer(
        "Stock Request #", compute="_compute_stock_request_ids"
    )

    @api.depends("move_ids", "stock_request_order_id")
    def _compute_stock_request_ids(self):
        for rec in self:
            rec.stock_request_ids = (
                rec.stock_request_order_id.stock_request_ids
                or rec.move_ids.mapped("stock_request_ids")
            )
            rec.stock_request_count = len(rec.stock_request_ids.mapped("order_id"))

    def _check_stock_request_receipt_chain(self):
        self.move_ids._check_stock_request_receipt_quantities(
            cancel_backorder=bool(self.env.context.get("cancel_backorder"))
        )

    def _stock_request_validation_audit_data(self):
        self.ensure_one()
        operation_moves = self.move_ids.filtered(
            lambda move: move.state not in ("done", "cancel")
            and move.stock_request_line_id
        )
        quantities_by_request = {}
        for move in operation_moves:
            request = move.stock_request_line_id
            quantities = quantities_by_request.setdefault(
                request,
                {"planned": 0.0, "processed": 0.0},
            )
            quantities["planned"] += move.product_uom._compute_quantity(
                move.product_uom_qty,
                request.product_uom_id,
                rounding_method="HALF-UP",
            )
            quantities["processed"] += move.product_uom._compute_quantity(
                max(move.quantity, 0.0),
                request.product_uom_id,
                rounding_method="HALF-UP",
            )

        lines = []
        is_partial = False
        for request, quantities in quantities_by_request.items():
            planned_qty = quantities["planned"]
            if (
                self.stock_request_operation == "receipt"
                and self.stock_request_order_id.transfer_flow
                == "two_step_no_transit"
            ):
                planned_qty = request.remaining_receipt_qty
            remaining_qty = max(planned_qty - quantities["processed"], 0.0)
            partial_line = (
                float_compare(
                    remaining_qty,
                    0.0,
                    precision_rounding=request.product_uom_id.rounding,
                )
                > 0
            )
            is_partial = is_partial or partial_line
            lines.append(
                {
                    "product": request.product_id.display_name,
                    "processed": quantities["processed"],
                    "remaining": remaining_qty,
                    "uom": request.product_uom_id.display_name,
                }
            )

        decision = False
        if is_partial:
            if (
                self.stock_request_operation == "receipt"
                and self.stock_request_order_id.transfer_flow
                == "two_step_no_transit"
            ):
                decision = "keep_pending"
            elif self.env.context.get("cancel_backorder"):
                decision = "cancel_remaining"
            else:
                decision = "create_backorder"
        return {"decision": decision, "lines": lines}

    def _check_stock_request_operation_access(self):
        for picking in self.filtered("stock_request_order_id"):
            order = picking.stock_request_order_id
            if picking.stock_request_operation in ("delivery", "transfer"):
                order._check_source_responsible_access()
            elif picking.stock_request_operation == "receipt":
                order._check_destination_responsible_access()

    def button_validate(self):
        self._check_stock_request_operation_access()
        self._check_stock_request_receipt_chain()
        return super().button_validate()

    def write(self, vals):
        if (
            not self.env.su
            and set(vals)
            & {
                "stock_request_validated_by_id",
                "stock_request_validation_date",
            }
        ):
            raise AccessError(
                _(
                    "Stock Request validation audit fields are filled "
                    "automatically."
                )
            )
        return super().write(vals)

    def _action_done(self):
        self._check_stock_request_operation_access()
        self._check_stock_request_receipt_chain()
        validating_user = self.env.user
        pending_audit_pickings = self.filtered(
            lambda picking: picking.stock_request_order_id
            and picking.stock_request_operation in (
                "delivery",
                "receipt",
                "transfer",
            )
            and picking.state != "done"
        )
        audit_data_by_picking = {
            picking.id: picking._stock_request_validation_audit_data()
            for picking in pending_audit_pickings
        }
        result = super()._action_done()
        for picking in pending_audit_pickings.filtered(
            lambda candidate: candidate.state == "done"
        ):
            validation_date = picking.date_done or fields.Datetime.now()
            picking.sudo().write(
                {
                    "stock_request_validated_by_id": validating_user.id,
                    "stock_request_validation_date": validation_date,
                }
            )
            picking.stock_request_order_id._record_transfer_validation(
                picking,
                validating_user,
                audit_data=audit_data_by_picking.get(picking.id),
            )
        completed_delivery_orders = pending_audit_pickings.filtered(
            lambda picking: picking.state == "done"
            and picking.stock_request_operation == "delivery"
        ).mapped("stock_request_order_id")
        completed_receipt_orders = pending_audit_pickings.filtered(
            lambda picking: picking.state == "done"
            and picking.stock_request_operation == "receipt"
        ).mapped("stock_request_order_id")
        completed_transfer_orders = pending_audit_pickings.filtered(
            lambda picking: picking.state == "done"
            and picking.stock_request_operation == "transfer"
        ).mapped("stock_request_order_id")
        orders = self.mapped("stock_request_order_id").sudo()
        orders._sync_workflow_state_from_pickings()
        completed_delivery_orders._schedule_receipt_activities()
        completed_receipt_orders.invalidate_recordset(["state"])
        completed_receipt_orders._notify_received(validating_user)
        completed_transfer_orders.invalidate_recordset(["state"])
        completed_transfer_orders._notify_transferred(validating_user)
        return result

    def action_view_stock_request(self):
        """
        :return dict: dictionary value for created view
        """
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "stock_request.stock_request_order_action"
        )

        requests = self.mapped("stock_request_ids.order_id")
        if len(requests) > 1:
            action["domain"] = [("id", "in", requests.ids)]
        elif requests:
            action["views"] = [
                (
                    self.env.ref("stock_request.stock_request_order_form").id,
                    "form",
                )
            ]
            action["res_id"] = requests.id
        return action
