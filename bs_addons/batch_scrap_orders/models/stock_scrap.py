from odoo import _, api, fields, models
from odoo.exceptions import UserError


class StockScrap(models.Model):
    _inherit = "stock.scrap"

    batch_reference = fields.Char(
        string="Batch Reference",
        readonly=True,
        copy=False,
        index="btree_not_null",
    )
    batch_scrap_order_id = fields.Many2one(
        "batch.scrap.order",
        string="Batch Scrap Order",
        readonly=True,
        copy=False,
        ondelete="set null",
        index="btree_not_null",
        check_company=True,
    )
    batch_scrap_line_id = fields.Many2one(
        "batch.scrap.order.line",
        string="Batch Scrap Line",
        readonly=True,
        copy=False,
        ondelete="set null",
        index="btree_not_null",
        check_company=True,
    )
    batch_scrap_order_count = fields.Integer(
        compute="_compute_batch_scrap_order_count"
    )

    _batch_scrap_line_unique = models.Constraint(
        "UNIQUE(batch_scrap_line_id)",
        "A Scrap Order has already been created for this batch line.",
    )

    @api.depends("batch_scrap_order_id")
    def _compute_batch_scrap_order_count(self):
        for scrap in self:
            scrap.batch_scrap_order_count = 1 if scrap.batch_scrap_order_id else 0

    def action_open_batch_scrap_order(self):
        self.ensure_one()
        if not self.batch_scrap_order_id:
            raise UserError(_("This Scrap Order was not created from a batch."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Batch Scrap Order"),
            "res_model": "batch.scrap.order",
            "res_id": self.batch_scrap_order_id.id,
            "views": [
                (
                    self.env.ref(
                        "batch_scrap_orders.batch_scrap_order_form_view"
                    ).id,
                    "form",
                )
            ],
            "target": "current",
        }
