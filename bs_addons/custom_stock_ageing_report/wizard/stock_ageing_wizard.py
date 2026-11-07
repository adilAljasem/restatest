import base64
from io import BytesIO

import xlsxwriter

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class StockAgeingReportWizard(models.TransientModel):
    _name = "stock.ageing.report.wizard"
    _description = "Stock Ageing Report"

    name = fields.Char(default=lambda self: _("Stock Ageing Report"), readonly=True)
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        required=True,
        default=lambda self: self.env.company,
    )
    filter_by = fields.Selection(
        [("product", "Product"), ("category", "Category")],
        string="Filter By",
        required=True,
        default="product",
    )
    report_based_on = fields.Selection(
        [("warehouse", "Warehouse"), ("location", "Location")],
        string="Generate Report Based on",
        required=True,
        default="warehouse",
    )
    warehouse_ids = fields.Many2many(
        "stock.warehouse",
        string="Warehouse",
        check_company=True,
        help="Leave empty to include every warehouse in the selected company.",
    )
    location_ids = fields.Many2many(
        "stock.location",
        string="Location",
        check_company=True,
        help="Selected internal locations include their internal child locations.",
    )
    product_ids = fields.Many2many(
        "product.product",
        string="Product",
        check_company=True,
        help="Leave empty to include all storable products.",
    )
    category_ids = fields.Many2many(
        "product.category",
        string="Category",
        help="Products in child categories are included.",
    )
    report_date = fields.Date(
        string="From Date",
        required=True,
        default=fields.Date.context_today,
        help="Completed stock move lines through the end of this date are included.",
    )
    period_length = fields.Integer(
        string="Period Length (Days)",
        required=True,
        default=30,
    )
    period_count = fields.Integer(
        string="Number of Periods",
        required=True,
        default=4,
        help="Includes the final open-ended bucket. Four buckets of 30 days are 0-30, 31-60, 61-90, and over 90.",
    )
    state = fields.Selection(
        [("filters", "Filters"), ("results", "Results")],
        default="filters",
        required=True,
    )
    result_html = fields.Html(string="Report Results", sanitize=False, readonly=True)
    result_data = fields.Json(copy=False, readonly=True)
    xlsx_file = fields.Binary(copy=False, readonly=True, attachment=False)
    xlsx_filename = fields.Char(copy=False, readonly=True)

    @api.constrains("period_length", "period_count")
    def _check_periods(self):
        for wizard in self:
            if not 1 <= wizard.period_length <= 3650:
                raise ValidationError(_("Period length must be between 1 and 3,650 days."))
            if not 2 <= wizard.period_count <= 12:
                raise ValidationError(_("The number of buckets must be between 2 and 12."))

    @api.constrains(
        "company_id",
        "filter_by",
        "report_based_on",
        "warehouse_ids",
        "location_ids",
        "product_ids",
    )
    def _check_filter_companies(self):
        for wizard in self:
            if (
                wizard.report_based_on == "warehouse"
                and wizard.warehouse_ids.filtered(lambda warehouse: warehouse.company_id != wizard.company_id)
            ):
                raise ValidationError(_("Every selected warehouse must belong to the report company."))
            invalid_locations = (
                wizard.location_ids.filtered(
                    lambda location: location.company_id and location.company_id != wizard.company_id
                )
                if wizard.report_based_on == "location"
                else self.env["stock.location"]
            )
            if invalid_locations:
                raise ValidationError(_("Every selected location must belong to the report company or be shared."))
            invalid_products = (
                wizard.product_ids.filtered(
                    lambda product: product.company_id and product.company_id != wizard.company_id
                )
                if wizard.filter_by == "product"
                else self.env["product.product"]
            )
            if invalid_products:
                raise ValidationError(_("Every selected product must belong to the report company or be shared."))

    @api.onchange("company_id")
    def _onchange_company_id(self):
        self.warehouse_ids = False
        self.location_ids = False
        self.product_ids = False
        self._clear_results()

    @api.onchange("filter_by")
    def _onchange_filter_by(self):
        if self.filter_by == "product":
            self.category_ids = False
        else:
            self.product_ids = False
        self._clear_results()

    @api.onchange("report_based_on")
    def _onchange_report_based_on(self):
        if self.report_based_on == "warehouse":
            self.location_ids = False
        else:
            self.warehouse_ids = False
        self._clear_results()

    @api.onchange(
        "warehouse_ids",
        "location_ids",
        "product_ids",
        "category_ids",
        "report_date",
        "period_length",
        "period_count",
    )
    def _onchange_filters(self):
        self._clear_results()

    def _clear_results(self):
        self.state = "filters"
        self.result_html = False
        self.result_data = False
        self.xlsx_file = False
        self.xlsx_filename = False

    def _compute_report(self):
        self.ensure_one()
        self._check_periods()
        self._check_filter_companies()
        data = self.env["stock.ageing.engine"]._get_report_data(self)
        html = self.env["ir.qweb"]._render(
            "custom_stock_ageing_report.stock_ageing_result_table",
            {"data": data, "show_filters": True, "pdf_mode": False},
        )
        self.write({
            "state": "results",
            "result_data": data,
            "result_html": html,
            "xlsx_file": False,
            "xlsx_filename": False,
        })
        return data

    def _report_data_for_output(self):
        """Reuse the displayed snapshot so screen, PDF, and Excel stay identical."""
        self.ensure_one()
        if self.state == "results" and self.result_data:
            return self.result_data
        return self._compute_report()

    def action_view_report(self):
        self._compute_report()
        return {
            "type": "ir.actions.act_window",
            "name": _("Stock Ageing Report"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "view_id": self.env.ref("custom_stock_ageing_report.stock_ageing_report_wizard_view_form").id,
            "target": "new",
            "context": dict(self.env.context, dialog_size="extra-large"),
        }

    def action_back_to_filters(self):
        self.ensure_one()
        self.state = "filters"
        return {
            "type": "ir.actions.act_window",
            "name": _("Stock Ageing Report"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "view_id": self.env.ref("custom_stock_ageing_report.stock_ageing_report_wizard_view_form").id,
            "target": "new",
            "context": dict(self.env.context, dialog_size="extra-large"),
        }

    def action_print_pdf(self):
        self.ensure_one()
        self._report_data_for_output()
        return self.env.ref("custom_stock_ageing_report.action_report_stock_ageing").report_action(self)

    def _generate_xlsx(self, data):
        output = BytesIO()
        workbook = xlsxwriter.Workbook(output, {"in_memory": True})
        workbook.set_properties({
            "title": _("Stock Ageing Report"),
            "company": data["company"],
        })
        worksheet = workbook.add_worksheet(_("Stock Ageing")[:31])

        title_format = workbook.add_format({
            "bold": True,
            "font_size": 16,
            "align": "center",
            "valign": "vcenter",
            "font_color": "#FFFFFF",
            "bg_color": "#2F5597",
        })
        label_format = workbook.add_format({"bold": True, "bg_color": "#D9E2F3", "border": 1})
        value_format = workbook.add_format({"border": 1, "text_wrap": True})
        no_data_format = workbook.add_format({
            "italic": True,
            "font_color": "#595959",
            "border": 1,
            "align": "center",
            "valign": "vcenter",
        })
        header_format = workbook.add_format({
            "bold": True,
            "font_color": "#FFFFFF",
            "bg_color": "#4472C4",
            "border": 1,
            "align": "center",
            "valign": "vcenter",
            "text_wrap": True,
        })
        text_format = workbook.add_format({"border": 1, "valign": "top"})
        quantity_format = workbook.add_format({
            "border": 1,
            "num_format": "#,##0.00",
            "valign": "top",
            "font_color": "#008A00",
            "bold": True,
        })
        money_format = workbook.add_format({
            "border": 1,
            "num_format": '#,##0.00;[Red]-#,##0.00',
            "valign": "top",
        })
        total_label_format = workbook.add_format({
            "bold": True,
            "bg_color": "#D9EAD3",
            "border": 1,
        })
        total_quantity_format = workbook.add_format({
            "bold": True,
            "bg_color": "#D9EAD3",
            "border": 1,
            "num_format": "#,##0.00",
            "font_color": "#008A00",
        })
        total_money_format = workbook.add_format({
            "bold": True,
            "bg_color": "#D9EAD3",
            "border": 1,
            "num_format": '#,##0.00;[Red]-#,##0.00',
        })

        column_count = 6 + 2 * len(data["periods"])
        worksheet.merge_range(0, 0, 1, column_count - 1, _("Stock Ageing Report"), title_format)
        metadata = [
            (_("Company"), data["company"]),
            (_("Report Date"), data["report_date"]),
            (_("Filter By"), data["filters"]["filter_by"]),
            (
                data["filters"]["filter_by"],
                data["filters"]["products"]
                if data["filters"]["filter_by_key"] == "product"
                else data["filters"]["categories"],
            ),
            (_("Generate Report Based on"), data["filters"]["report_based_on"]),
            (
                data["filters"]["report_based_on"],
                data["filters"]["warehouses"]
                if data["filters"]["report_based_on_key"] == "warehouse"
                else data["filters"]["locations"],
            ),
            (
                _("Bucket Configuration"),
                _(
                    "%(count)s buckets, %(days)s days",
                    count=data["period_count"],
                    days=data["period_length"],
                ),
            ),
            (_("Currency"), data["currency"]),
        ]
        row = 3
        for label, value in metadata:
            worksheet.write(row, 0, label, label_format)
            worksheet.merge_range(row, 1, row, column_count - 1, value, value_format)
            row += 1
        row += 1

        header_row = row
        fixed_headers = [
            _("Code"),
            _("Product Name"),
            _("Unit of Measure"),
            _("Warehouse"),
            _("Total Qty"),
            _("Total Value"),
        ]
        for column, header in enumerate(fixed_headers):
            worksheet.merge_range(header_row, column, header_row + 1, column, header, header_format)
        column = len(fixed_headers)
        for period in data["periods"]:
            worksheet.merge_range(
                header_row,
                column,
                header_row,
                column + 1,
                period["label"],
                header_format,
            )
            worksheet.write(header_row + 1, column, _("Qty"), header_format)
            worksheet.write(header_row + 1, column + 1, _("Value"), header_format)
            column += 2

        row += 2
        first_data_row = row
        if not data["lines"]:
            worksheet.merge_range(
                row,
                0,
                row,
                column_count - 1,
                _("No stock ageing results were found for the selected filters and report date."),
                no_data_format,
            )
            worksheet.set_row(row, 28)
            row += 1
        for line in data["lines"]:
            worksheet.write(row, 0, line["default_code"], text_format)
            worksheet.write(row, 1, line["product"], text_format)
            worksheet.write(row, 2, line["uom"], text_format)
            worksheet.write(row, 3, line["warehouse"], text_format)
            worksheet.write_number(row, 4, line["total_qty"], quantity_format)
            worksheet.write_number(row, 5, line["total_value"], money_format)
            column = 6
            for bucket in line["buckets"]:
                worksheet.write_number(row, column, bucket["quantity"], quantity_format)
                worksheet.write_number(row, column + 1, bucket["value"], money_format)
                column += 2
            row += 1

        worksheet.merge_range(row, 0, row, 3, _("Grand Total"), total_label_format)
        worksheet.write_number(row, 4, data["totals"]["quantity"], total_quantity_format)
        worksheet.write_number(row, 5, data["totals"]["value"], total_money_format)
        column = 6
        for bucket in data["totals"]["buckets"]:
            worksheet.write_number(row, column, bucket["quantity"], total_quantity_format)
            worksheet.write_number(row, column + 1, bucket["value"], total_money_format)
            column += 2

        worksheet.freeze_panes(first_data_row, 4)
        worksheet.set_column(0, 0, 18)
        worksheet.set_column(1, 1, 28)
        worksheet.set_column(2, 3, 20)
        worksheet.set_column(4, column_count - 1, 15)
        worksheet.set_row(0, 24)
        worksheet.set_row(header_row, 24)
        worksheet.set_row(header_row + 1, 20)
        worksheet.set_landscape()
        worksheet.fit_to_pages(1, 0)
        worksheet.set_footer(_("Page &P of &N"))
        workbook.close()
        return output.getvalue()

    def action_export_xlsx(self):
        self.ensure_one()
        data = self._report_data_for_output()
        filename = "Stock Ageing Report %s.xlsx" % self.report_date.isoformat()
        self.write({
            "xlsx_file": base64.b64encode(self._generate_xlsx(data)),
            "xlsx_filename": filename,
        })
        download = self.env["stock.ageing.report.download"].create({
            "file_data": self.xlsx_file,
            "filename": self.xlsx_filename,
        })
        return {
            "type": "ir.actions.act_window",
            "name": _("Files to Download"),
            "res_model": "stock.ageing.report.download",
            "res_id": download.id,
            "view_mode": "form",
            "view_id": self.env.ref(
                "custom_stock_ageing_report.stock_ageing_report_download_view_form"
            ).id,
            "target": "new",
            "context": dict(self.env.context, dialog_size="large"),
        }


class StockAgeingReportDownload(models.TransientModel):
    _name = "stock.ageing.report.download"
    _description = "Stock Ageing Report Download"

    file_data = fields.Binary(string="Here is the Excel Report", readonly=True, required=True)
    filename = fields.Char(readonly=True, required=True)
