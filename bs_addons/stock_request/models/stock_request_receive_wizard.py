# Copyright 2026 Mohamed Elmalky
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl.html).

from odoo import fields, models


class StockRequestReceiveWizard(models.TransientModel):
    _name = "stock.request.receive.wizard"
    _description = "Receive Stock Request without Transit"

    order_id = fields.Many2one(
        comodel_name="stock.request.order",
        string="Stock Request",
        required=True,
        readonly=True,
        check_company=True,
        ondelete="cascade",
    )
    company_id = fields.Many2one(
        related="order_id.company_id",
        readonly=True,
    )
    line_ids = fields.One2many(
        comodel_name="stock.request.receive.wizard.line",
        inverse_name="wizard_id",
        string="Products",
    )
    processed = fields.Boolean(readonly=True, copy=False)

    def action_confirm_receipt(self):
        self.ensure_one()
        self.lock_for_update(allow_referencing=True)
        self.invalidate_recordset(["processed"])
        if self.processed:
            return {"type": "ir.actions.act_window_close"}
        self.order_id._create_no_transit_receipt(
            [
                (
                    line.stock_request_id.id,
                    line.quantity,
                    line.remaining_quantity,
                )
                for line in self.line_ids
            ]
        )
        self.processed = True
        return {"type": "ir.actions.act_window_close"}


class StockRequestReceiveWizardLine(models.TransientModel):
    _name = "stock.request.receive.wizard.line"
    _description = "Receive Stock Request without Transit Line"

    wizard_id = fields.Many2one(
        comodel_name="stock.request.receive.wizard",
        required=True,
        ondelete="cascade",
    )
    stock_request_id = fields.Many2one(
        comodel_name="stock.request",
        string="Request Line",
        required=True,
        readonly=True,
        check_company=True,
    )
    company_id = fields.Many2one(
        related="wizard_id.company_id",
        readonly=True,
    )
    product_id = fields.Many2one(
        related="stock_request_id.product_id",
        string="Product",
        readonly=True,
    )
    product_uom_id = fields.Many2one(
        related="stock_request_id.product_uom_id",
        string="Unit",
        readonly=True,
    )
    remaining_quantity = fields.Float(
        string="Remaining Quantity",
        digits="Product Unit of Measure",
        required=True,
        readonly=True,
    )
    quantity = fields.Float(
        string="Received Quantity",
        digits="Product Unit of Measure",
        required=True,
    )
