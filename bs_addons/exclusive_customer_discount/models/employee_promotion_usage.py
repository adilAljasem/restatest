from odoo import fields, models

class ExclusivePromotionUsage(models.Model):
    _name = "exclusive.promotion.usage"
    _description = "Exclusive Customer Discounts"
    _order = "order_date desc, id desc"

    partner_id = fields.Many2one("res.partner", string="Customer", required=True, readonly=True, ondelete="restrict", index=True)
    program_id = fields.Many2one("loyalty.program", string="Promotion Name", required=True, readonly=True, ondelete="restrict", index=True)
    pos_order_id = fields.Many2one("pos.order", string="POS Order Reference", required=True, readonly=True, ondelete="cascade", index=True)
    usage_date = fields.Date(required=True, readonly=True, index=True)
    company_id = fields.Many2one("res.company", required=True, readonly=True, index=True)
    config_id = fields.Many2one(related="pos_order_id.config_id", store=True, string="POS")
    order_date = fields.Datetime(related="pos_order_id.date_order", store=True)
    currency_id = fields.Many2one(related="pos_order_id.currency_id")
    amount_before = fields.Monetary(string="Order Amount Before Discount", readonly=True)
    discount_percentage = fields.Float(string="Discount Percentage", readonly=True)
    discount_amount = fields.Monetary(string="Discount Amount", readonly=True)
    amount_after = fields.Monetary(string="Order Amount After Discount", readonly=True)
    _order_program_unique = models.Constraint("UNIQUE(program_id, partner_id, pos_order_id)", "A promotion is counted once per customer and order.")
