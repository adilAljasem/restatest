import io
import json

import xlsxwriter

from odoo import http
from odoo.http import content_disposition, request


class RestaAccountReportController(http.Controller):

    @http.route("/resta_account/report/xlsx", type="http", auth="user", methods=["POST"], csrf=True)
    def report_xlsx(self, kind, options="{}", **_kw):
        Engine = request.env["resta.account.report"]
        opts = json.loads(options or "{}")
        data = Engine.get_report(kind, opts)
        detail_kinds = {"gl", "partner"}
        out = io.BytesIO()
        wb = xlsxwriter.Workbook(out, {"in_memory": True})
        ws = wb.add_worksheet(data["title"][:31])
        ws.right_to_left()
        f_title = wb.add_format({"bold": True, "font_size": 14})
        f_head = wb.add_format({"bold": True, "bg_color": "#F2F2F2", "border": 1, "align": "center"})
        f_num = wb.add_format({"num_format": "#,##0.00"})
        f_bold = wb.add_format({"bold": True})
        f_bold_num = wb.add_format({"bold": True, "num_format": "#,##0.00", "top": 1})
        f_pct = wb.add_format({"num_format": "0.0\"%\""})
        f_muted = wb.add_format({"font_color": "#666666"})
        o = data["options"]
        ws.write(0, 0, data["title"], f_title)
        ws.write(1, 0, data["company"], f_muted)
        period = o["date_to"] if kind in ("bs", "aged") else "%s ← %s" % (o["date_from"], o["date_to"])
        ws.write(2, 0, period, f_muted)
        row = 4
        ws.write(row, 0, "", f_head)
        for i, col in enumerate(data["columns"]):
            ws.write(row, i + 1, col["label"], f_head)
        ws.set_column(0, 0, 48)
        ws.set_column(1, len(data["columns"]), 16)
        for r in data["rows"]:
            row += 1
            strong = r["type"] in ("section", "total")
            ws.write(row, 0, ("    " * max(r["level"] - 1, 0)) + r["name"], f_bold if strong else None)
            for i, v in enumerate(r["values"]):
                if v is None:
                    continue
                fmt = f_pct if data["columns"][i].get("percent") else (f_bold_num if strong else f_num)
                ws.write_number(row, i + 1, v, fmt)
            if kind in detail_kinds and r.get("expandable"):
                lines = Engine.get_detail(kind, opts, {"account_id": r.get("account_id"),
                                                       "partner_id": r.get("partner_id")})
                for line in lines["lines"]:
                    row += 1
                    ws.write(row, 0, "        %s  %s  %s" % (line["date"], line["move"], line["label"]), f_muted)
                    ws.write_number(row, 2, line["debit"], f_num)
                    ws.write_number(row, 3, line["credit"], f_num)
                    ws.write_number(row, 4, line["balance"], f_num)
        wb.close()
        filename = "%s %s.xlsx" % (data["title"], o["date_to"])
        return request.make_response(out.getvalue(), headers=[
            ("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            ("Content-Disposition", content_disposition(filename)),
        ])
