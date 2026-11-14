import io

import xlsxwriter

from odoo import http
from odoo.http import content_disposition, request


class RestaPayrollController(http.Controller):

    @http.route("/resta_payroll/bank_sheet/<int:batch_id>", type="http", auth="user")
    def bank_sheet(self, batch_id, **_kw):
        """Salary transfer sheet: one row per employee with IBAN and net pay (for the bank / WPS upload)."""
        batch = request.env["resta.payslip.batch"].browse(batch_id)
        batch.check_access("read")
        out = io.BytesIO()
        wb = xlsxwriter.Workbook(out, {"in_memory": True})
        ws = wb.add_worksheet("Salaries")
        ws.right_to_left()
        head = wb.add_format({"bold": True, "bg_color": "#F2F2F2", "border": 1})
        num = wb.add_format({"num_format": "#,##0.00"})
        cols = ["#", "اسم الموظف", "رقم الهوية / الإقامة", "رقم الموظف", "البنك", "IBAN", "طريقة الصرف",
                "الراتب الأساسي", "بدل السكن", "بدلات أخرى", "الخصومات", "صافي الراتب"]
        for i, c in enumerate(cols):
            ws.write(0, i, c, head)
        ws.set_column(1, 1, 30)
        ws.set_column(2, 6, 18)
        ws.set_column(7, 11, 14)
        row = 0
        for slip in batch.slip_ids.filtered(lambda s: s.state != "cancel").sorted(lambda s: s.employee_id.name):
            emp = slip.employee_id.sudo()
            bank = emp.primary_bank_account_id
            amounts = {l.code: l.amount for l in slip.line_ids}
            other = sum(l.amount for l in slip.line_ids if l.category in ("allowance", "addition")
                        and l.code != "HOUSING")
            row += 1
            ws.write(row, 0, row)
            ws.write(row, 1, emp.name)
            ws.write(row, 2, emp.identification_id or emp.passport_id or "")
            ws.write(row, 3, emp.barcode or emp.id)
            ws.write(row, 4, bank.bank_id.name or "")
            ws.write(row, 5, bank.acc_number or "")
            ws.write(row, 6, "نقداً" if emp.resta_payment_method == "cash" else "تحويل")
            ws.write_number(row, 7, amounts.get("BASIC", 0.0), num)
            ws.write_number(row, 8, amounts.get("HOUSING", 0.0), num)
            ws.write_number(row, 9, other, num)
            ws.write_number(row, 10, slip.deductions, num)
            ws.write_number(row, 11, slip.net, num)
        ws.write(row + 1, 1, "الإجمالي", head)
        ws.write_formula(row + 1, 11, "=SUM(L2:L%d)" % (row + 1), num)
        wb.close()
        return request.make_response(out.getvalue(), headers=[
            ("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            ("Content-Disposition", content_disposition("%s.xlsx" % batch.name.replace("/", "-"))),
        ])
