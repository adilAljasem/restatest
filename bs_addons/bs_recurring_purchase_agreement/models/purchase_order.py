from odoo import fields, models


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    bs_recurring_schedule_date = fields.Date(
        string="Recurring Schedule Date",
        copy=False,
        index=True,
        readonly=True,
    )

    _bs_recurring_schedule_unique = models.Constraint(
        "UNIQUE(requisition_id, bs_recurring_schedule_date)",
        "A recurring purchase order already exists for this agreement and date.",
    )
