from odoo import fields, models


class EmployeePromotionUsage(models.Model):
    _name = "employee.promotion.usage"
    _description = "Employee Promotion Usage"
    _order = "usage_date desc, id desc"

    employee_id = fields.Many2one(
        "hr.employee", required=True, readonly=True, ondelete="restrict", index=True
    )
    employee_name = fields.Char(required=True, readonly=True)
    partner_id = fields.Many2one(
        "res.partner", string="Customer", required=True, readonly=True,
        ondelete="restrict", index=True,
    )
    program_id = fields.Many2one(
        "loyalty.program", string="Promotion Program", required=True,
        readonly=True, ondelete="cascade", index=True,
    )
    pos_order_id = fields.Many2one(
        "pos.order", string="POS Order", required=True, readonly=True,
        ondelete="cascade", index=True,
    )
    usage_date = fields.Date(required=True, readonly=True, index=True)
    company_id = fields.Many2one(
        "res.company", required=True, readonly=True, index=True
    )

    _employee_promotion_order_unique = models.Constraint(
        "UNIQUE(program_id, employee_id, pos_order_id)",
        "A promotion usage can only be recorded once per employee and POS order.",
    )
