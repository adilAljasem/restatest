# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class PosOrder(models.Model):
    _inherit = "pos.order"

    sbl_can_refund_pos_order = fields.Boolean(
        compute="_compute_sbl_can_refund_pos_order",
        string="Can Refund POS Order",
    )

    @api.depends_context("uid")
    def _compute_sbl_can_refund_pos_order(self):
        can_refund = not self._sbl_get_refund_restricted_employee()
        for order in self:
            order.sbl_can_refund_pos_order = can_refund

    def _sbl_get_refund_restricted_employee(self):
        employee = self.env.user.employee_id
        if employee and employee.sbl_hide_pos_action_refund:
            return employee
        return self.env["hr.employee"]

    def _sbl_check_can_refund_pos_order(self):
        if self._sbl_get_refund_restricted_employee():
            raise UserError(_("You are not allowed to refund POS orders."))

    def _refund(self):
        self._sbl_check_can_refund_pos_order()
        return super()._refund()

    def refund(self):
        self._sbl_check_can_refund_pos_order()
        return super().refund()


class PosOrderLine(models.Model):
    _inherit = "pos.order.line"

    @api.model_create_multi
    def create(self, vals_list):
        if any(vals.get("refunded_orderline_id") for vals in vals_list):
            self.env["pos.order"]._sbl_check_can_refund_pos_order()
        return super().create(vals_list)
