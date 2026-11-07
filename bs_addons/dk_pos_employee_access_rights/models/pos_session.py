# -*- coding: utf-8 -*-
from odoo import models


class PosSession(models.Model):
    _inherit = "pos.session"

    def _sbl_get_closing_move_employee_name(self):
        self.ensure_one()
        employee = self.employee_id if "employee_id" in self._fields else False
        return (employee.name if employee else False) or self.user_id.name or ""

    def _sbl_get_closing_move_ref(self):
        self.ensure_one()
        employee_name = self._sbl_get_closing_move_employee_name()
        if not employee_name:
            return self.name
        return f"{self.name} - Employee: {employee_name}"

    def _sbl_apply_closing_move_employee_ref(self):
        for session in self:
            if session.move_id:
                session.move_id.ref = session._sbl_get_closing_move_ref()

    def _sbl_get_closing_employee_partner(self):
        self.ensure_one()
        employee = self.employee_id if "employee_id" in self._fields else False
        if not employee:
            employee = self.user_id.employee_id or self.env.user.employee_id
        if not employee:
            return self.env["res.partner"]
        return employee.sudo()._get_related_partners()[:1]

    def _sbl_apply_closing_move_employee_partner(self):
        for session in self:
            if not session.move_id:
                continue
            partner = session._sbl_get_closing_employee_partner()
            if not partner:
                continue
            lines_without_partner = session.move_id.line_ids.filtered(
                lambda line: not line.partner_id
            )
            if lines_without_partner:
                company = session.company_id or self.env.company
                lines_without_partner.sudo().with_company(
                    company
                ).with_context(
                    check_move_validity=False,
                    skip_invoice_sync=True,
                ).write({"partner_id": partner.id})

    def _sbl_sanitize_cross_company_line_taxes(self):
        for session in self:
            company = session.company_id
            orders = session.order_ids.filtered(
                lambda o: o.state in ("paid", "done", "invoiced") and not o.is_invoiced
            )
            for order in orders:
                for line in order.lines:
                    wrong_taxes = line.tax_ids.filtered(
                        lambda tax: tax.company_id != company
                    )
                    if wrong_taxes:
                        line.write({
                            "tax_ids": [(3, tax.id) for tax in wrong_taxes],
                        })

    def _create_account_move(
        self,
        balancing_account=False,
        amount_to_balance=0,
        bank_payment_method_diffs=None,
    ):
        self._sbl_sanitize_cross_company_line_taxes()
        data = super()._create_account_move(
            balancing_account=balancing_account,
            amount_to_balance=amount_to_balance,
            bank_payment_method_diffs=bank_payment_method_diffs,
        )
        self._sbl_apply_closing_move_employee_ref()
        self._sbl_apply_closing_move_employee_partner()
        return data
