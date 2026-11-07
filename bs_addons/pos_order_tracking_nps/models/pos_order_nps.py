from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

NPS_CATEGORIES = [
    ('promoter', 'Promoter'),
    ('passive', 'Passive'),
    ('detractor', 'Detractor'),
]


class PosOrderNps(models.Model):
    _name = 'pos.order.nps'
    _description = 'POS Customer NPS Feedback'
    _order = 'date desc, id desc'
    _rec_name = 'order_id'

    order_id = fields.Many2one('pos.order', string="Order", required=True, ondelete='cascade', index=True)
    date = fields.Datetime(string="Date", default=fields.Datetime.now, required=True)
    score = fields.Integer(string="Score (0-10)", required=True, aggregator='avg')
    category = fields.Selection(NPS_CATEGORIES, compute='_compute_category', store=True)
    nps_value = fields.Float(
        string="NPS", compute='_compute_category', store=True, aggregator='avg',
        help="+100 for promoters, 0 for passives, -100 for detractors. "
             "The average of this column is the Net Promoter Score.",
    )
    comment = fields.Text(string="Comment")
    config_id = fields.Many2one(related='order_id.config_id', store=True, string="Point of Sale")
    session_id = fields.Many2one(related='order_id.session_id', store=True)
    company_id = fields.Many2one(related='order_id.company_id', store=True)
    partner_id = fields.Many2one(related='order_id.partner_id', store=True, string="Customer")
    employee_name = fields.Char(compute='_compute_employee_name', string="Served by")
    order_number = fields.Char(related='order_id.tracking_number', string="Order Number")
    amount_total = fields.Monetary(related='order_id.amount_total', currency_field='currency_id')
    currency_id = fields.Many2one(related='order_id.currency_id')
    preparation_minutes = fields.Integer(
        string="Wait (min)", compute='_compute_preparation_minutes', store=True, aggregator='avg',
        help="Minutes between the order and the moment it was ready.",
    )

    _order_unique = models.Constraint('unique(order_id)', 'This order has already been rated.')

    @api.constrains('score')
    def _check_score(self):
        for rec in self:
            if not 0 <= rec.score <= 10:
                raise ValidationError(_("The NPS score must be between 0 and 10."))

    @api.depends('score')
    def _compute_category(self):
        for rec in self:
            if rec.score >= 9:
                rec.category, rec.nps_value = 'promoter', 100.0
            elif rec.score >= 7:
                rec.category, rec.nps_value = 'passive', 0.0
            else:
                rec.category, rec.nps_value = 'detractor', -100.0

    @api.depends('order_id.date_order', 'order_id.tracking_ready_date')
    def _compute_preparation_minutes(self):
        for rec in self:
            order = rec.order_id
            if order.tracking_ready_date and order.date_order:
                rec.preparation_minutes = max(0, int((order.tracking_ready_date - order.date_order).total_seconds() // 60))
            else:
                rec.preparation_minutes = 0

    def _compute_employee_name(self):
        for rec in self:
            order = rec.order_id
            employee = getattr(order, 'employee_id', False)
            rec.employee_name = employee.name if employee else order.user_id.name
