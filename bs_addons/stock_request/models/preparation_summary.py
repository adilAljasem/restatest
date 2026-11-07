# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl.html).
"""Read-only virtual list, aggregating secured request lines after filtering."""
import base64
import io
import json
from decimal import Decimal

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.tools import float_compare, format_datetime

ENABLED_PARAMETER = "stock_request.preparation_summary_enabled"
SEARCH_FIELDS = {
    "company_id": "order_id.company_id",
    "source_warehouse_id": "order_id.source_warehouse_id",
    "warehouse_id": "order_id.warehouse_id",
    "product_id": "product_id", "code": "product_id.default_code",
    "product_uom_id": "product_uom_id", "request_date": "order_id.request_date",
}


class PreparationSummary(models.Model):
    _name = "stock.request.preparation.summary"
    _description = "Preparation Summary"
    # Odoo requires a backing relation for every concrete model. The ORM owns
    # this empty table; report rows are still computed from secured request
    # lines and are never inserted here. CRUD remains blocked below.
    _auto = True
    _log_access = False
    _rec_name = "product_id"
    _order = "company_id, source_warehouse_id, product_id, product_uom_id"

    company_id = fields.Many2one("res.company", string="Company", readonly=True)
    source_warehouse_id = fields.Many2one("stock.warehouse", string="Source Warehouse", readonly=True)
    warehouse_id = fields.Many2one("stock.warehouse", string="Receiving Warehouse", readonly=True)
    product_id = fields.Many2one("product.product", string="Product", readonly=True)
    code = fields.Char("Internal Reference", readonly=True)
    product_uom_id = fields.Many2one("uom.uom", string="Unit of Measure", readonly=True)
    request_date = fields.Date("Request Date", readonly=True)
    quantity = fields.Float("Quantity to Prepare", digits="Product Unit of Measure", readonly=True, aggregator=False)
    order_count = fields.Integer("Orders", readonly=True, aggregator="count_distinct")
    row_key = fields.Char(readonly=True)
    report_domain = fields.Char(readonly=True)

    @api.model
    def fields_get(self, allfields=None, attributes=None):
        result = super().fields_get(allfields, attributes)
        for name, description in result.items():
            if name not in SEARCH_FIELDS:
                description["searchable"] = False
            # Group only on dimensions of a summary row. Destination and date
            # remain filters before aggregation, never silently split a row.
            description["groupable"] = name in ("company_id", "source_warehouse_id", "product_id", "product_uom_id", "code")
        return result

    def _check_report_access(self):
        # Elevation is restricted to configuration metadata, never report data.
        enabled = self.env["ir.config_parameter"].sudo().get_param(ENABLED_PARAMETER, "True")
        if str(enabled).lower() not in ("true", "1"):
            raise AccessError(_("Preparation Summary is disabled in Settings."))
        if not self.env.user.has_group("stock_request.group_stock_request_warehouse_responsible"):
            raise AccessError(_("Only warehouse responsible users and managers can access this report."))

    def _clean_domain(self, domain):
        tokens = iter(list(fields.Domain(domain or [])))
        def consume():
            token = next(tokens)
            if token in ("&", "|"):
                left, right = consume(), consume()
                if left is None:
                    return right
                if right is None:
                    return left
                return left & right if token == "&" else left | right
            if token == "!":
                child = consume()
                return ~child if child is not None else None
            return None if token[0] == "transfer_flow" else fields.Domain([token])
        result = consume()
        return result if result is not None else fields.Domain.TRUE

    def _line_domain(self, domain):
        def translate(condition):
            target = SEARCH_FIELDS.get(condition.field_expr)
            if not target:
                raise ValidationError(self.env._("Unsupported Preparation Summary filter: %s", condition.field_expr))
            return fields.Domain(target, condition.operator, condition.value)
        return self._clean_domain(domain).map_conditions(translate)

    def _get_lines(self, domain=None):
        self._check_report_access()
        order_domain = [
            ("operation_type", "=", "request"),
            ("transfer_flow", "in", ["two_step", "two_step_no_transit"]),
            ("state", "in", ["ready", "in_transit"]),
            ("has_pending_delivery", "=", True),
            ("company_id", "in", self.env.companies.ids),
        ]
        if not self.env.user.has_group("stock_request.group_stock_request_manager"):
            order_domain.append(("source_warehouse_id.stock_request_responsible_user_ids", "in", [self.env.uid]))
        orders = self.env["stock.request.order"].search(order_domain)
        line_domain = fields.Domain([
            ("order_id", "in", orders.ids), ("state", "!=", "cancel"),
            ("remaining_delivery_qty", ">", 0),
        ]) & self._line_domain(domain)
        return self.env["stock.request"].search(line_domain).filtered(
            lambda line: float_compare(line.remaining_delivery_qty, 0,
                                       precision_rounding=line.product_uom_id.rounding) > 0)

    def _dataset(self, domain=None):
        rows = {}
        for line in self._get_lines(domain):
            order = line.order_id
            records = (order.company_id, order.source_warehouse_id, line.product_id, line.product_uom_id)
            key = tuple(record.id for record in records)
            if key not in rows:
                rows[key] = dict(zip(("company_id", "source_warehouse_id", "product_id", "product_uom_id"),
                                    [(record.id, record.display_name) for record in records]))
                rows[key].update(id=line.id, row_key=json.dumps(key), code=line.product_id.default_code or "",
                                 quantity=0.0, order_ids=set(),
                                 digits=max(0, -Decimal(str(line.product_uom_id.rounding)).as_tuple().exponent))
            row = rows[key]
            row["id"] = min(row["id"], line.id)
            row["quantity"] += line.remaining_delivery_qty
            row["order_ids"].add(order.id)
            row["order_count"] = len(row["order_ids"])
        return list(rows.values())

    def _sorted_rows(self, rows, order=None):
        for term in reversed((order or self._order).split(",")):
            parts = term.strip().split()
            name = parts[0]
            if name not in self._fields and name != "id":
                raise ValidationError(_("Unsupported report sort field: %s", name))
            rows.sort(key=lambda row: row[name][1] if isinstance(row.get(name), tuple) else row.get(name, ""),
                      reverse=len(parts) > 1 and parts[1].lower() == "desc")
        return rows

    @api.model
    def web_search_read(self, domain, specification, offset=0, limit=None, order=None, count_limit=None):
        rows = self._sorted_rows(self._dataset(domain), order)
        records = []
        for row in rows[offset: offset + limit if limit else None]:
            values = {"id": row["id"]}
            for name, spec in specification.items():
                value = json.dumps(list(fields.Domain(domain))) if name == "report_domain" else row.get(name, False)
                if isinstance(value, tuple):
                    value = {"id": value[0], "display_name": value[1]} if "fields" in spec else value[0]
                values[name] = value
            records.append(values)
        return {"length": len(rows), "records": records}

    @api.model
    def web_read_group(self, domain, groupby, aggregates=(), limit=None, offset=0, order=None,
                       auto_unfold=False, opening_info=None, unfold_read_specification=None,
                       unfold_read_default_limit=80, groupby_read_specification=None):
        self._check_report_access()
        field = groupby[0]
        if field not in ("company_id", "source_warehouse_id", "product_id", "product_uom_id", "code"):
            raise ValidationError(_("Group by Company, Source Warehouse, Product, Internal Reference or Unit of Measure."))
        buckets = {}
        for line in self._get_lines(domain):
            value = line
            for name in SEARCH_FIELDS[field].split("."):
                value = value[name]
            pair = (value.id, value.display_name) if self._fields[field].type == "many2one" else value or False
            buckets[pair] = True
        groups = []
        openings = {item["value"]: item for item in opening_info or []}
        values = sorted(buckets, key=lambda value: str(value[1] if isinstance(value, tuple) else value),
                        reverse=bool(order and "desc" in order.lower()))
        for value in values[offset:offset + limit if limit else None]:
            raw = value[0] if isinstance(value, tuple) else value
            extra = [(field, "=", raw)]
            scoped = list(fields.Domain(domain) & fields.Domain(extra))
            rows = self._dataset(scoped)
            group = {field: value, "__extra_domain": extra, "__count": len(rows),
                     "order_count": len(set().union(*(row["order_ids"] for row in rows)))}
            group["order_count:count_distinct"] = group["order_count"]
            info = openings.get(raw, {})
            if not info.get("folded", not auto_unfold):
                if len(groupby) > 1:
                    group["__groups"] = self.web_read_group(
                        scoped, groupby[1:], aggregates, opening_info=info.get("groups"),
                        auto_unfold=auto_unfold, unfold_read_specification=unfold_read_specification,
                        unfold_read_default_limit=unfold_read_default_limit)
                else:
                    group["__records"] = self.web_search_read(
                        scoped, unfold_read_specification or {}, offset=info.get("offset", 0),
                        limit=info.get("limit", unfold_read_default_limit), order=order)["records"]
            groups.append(group)
        return {"groups": groups, "length": len(values)}

    @api.model
    def open_orders(self, key, domain=None):
        key = json.loads(key) if isinstance(key, str) else key
        row = next((row for row in self._dataset(domain) if json.loads(row["row_key"]) == key), None)
        return {
            "type": "ir.actions.act_window", "name": _("Preparation Summary"),
            "res_model": "stock.request.order", "view_mode": "list,form",
            "views": [(self.env.ref("stock_request.stock_request_order_tree").id, "list"),
                      (self.env.ref("stock_request.stock_request_order_form").id, "form")],
            "domain": [("id", "in", sorted(row["order_ids"]) if row else [])],
            "context": {"create": False}, "target": "current",
        }

    def _export_data(self, domain=None):
        return {"rows": self._sorted_rows(self._dataset(domain)),
                "generated": format_datetime(self.env, fields.Datetime.now()),
                "filters": self._filter_description(domain),
                "rtl": bool(self.env.lang and self.env.lang.startswith("ar")),
                "title": _("Preparation Summary"),
                "headers": [_("Company"), _("Source Warehouse"), _("Product"), _("Internal Reference"),
                            _("Unit of Measure"), _("Quantity to Prepare"), _("Orders")]}

    def _filter_description(self, domain):
        labels = []
        for token in list(self._clean_domain(domain)):
            if isinstance(token, str):
                labels.append({"&": _("AND"), "|": _("OR"), "!": _("NOT")}.get(token, token))
                continue
            name, operator, value = token
            if not isinstance(name, str):
                continue
            if name == "transfer_flow":
                continue
            field = self._fields.get(name)
            if field and field.type == "many2one" and operator in ("=", "!=", "in", "not in"):
                ids = value if isinstance(value, (list, tuple)) else [value]
                if all(isinstance(item, int) and item for item in ids):
                    value = ", ".join(self.env[field.comodel_name].search([("id", "in", ids)]).mapped("display_name"))
            labels.append(f"{self.fields_get([name])[name]['string']} {operator} {value}")
        companies = ", ".join(self.env.companies.mapped("display_name"))
        return _("Companies: %s", companies) + " — " + ("; ".join(labels) or _("All matching requests"))

    @api.model
    def action_print_pdf(self, domain=None):
        self._check_report_access()
        self._line_domain(domain)
        return self.env.ref("stock_request.preparation_summary_pdf_action").report_action(
            self, data={"domain": domain or [], "allowed_company_ids": self.env.companies.ids})

    @api.model
    def export_xlsx(self, domain=None):
        import xlsxwriter
        data = self._export_data(domain)
        stream = io.BytesIO()
        workbook = xlsxwriter.Workbook(stream, {"in_memory": True, "strings_to_formulas": False,
                                               "strings_to_urls": False})
        sheet = workbook.add_worksheet(data["title"][:31])
        if data["rtl"]:
            sheet.right_to_left()
        style = {"font_name": "Arial", "reading_order": 2 if data["rtl"] else 1}
        title = workbook.add_format(dict(style, bold=True, font_size=16))
        header = workbook.add_format(dict(style, bold=True, bg_color="#E9ECEF", border=1, text_wrap=True))
        normal = workbook.add_format(style)
        wrap = workbook.add_format(dict(style, text_wrap=True))
        integer = workbook.add_format(dict(style, num_format="0"))
        sheet.merge_range(0, 0, 0, 6, data["title"], title)
        sheet.merge_range(1, 0, 1, 6, data["generated"], normal)
        sheet.merge_range(2, 0, 2, 6, data["filters"], wrap)
        sheet.set_row(2, 45)
        for col, (label, width) in enumerate(zip(data["headers"], [24, 26, 44, 22, 20, 22, 14])):
            sheet.write_string(4, col, label, header)
            sheet.set_column(col, col, width)
        formats = {}
        for index, row in enumerate(data["rows"], 5):
            for col, name in enumerate(("company_id", "source_warehouse_id", "product_id", "code", "product_uom_id")):
                value = row[name][1] if isinstance(row[name], tuple) else row[name]
                sheet.write_string(index, col, value, normal)
            digits = row["digits"]
            if digits not in formats:
                formats[digits] = workbook.add_format(dict(style, num_format="0" + ("." + "0" * digits if digits else "")))
            sheet.write_number(index, 5, row["quantity"], formats[digits])
            sheet.write_number(index, 6, row["order_count"], integer)
        sheet.freeze_panes(5, 0)
        sheet.autofilter(4, 0, max(4, 4 + len(data["rows"])), 6)
        workbook.close()
        return {"filename": "preparation_summary.xlsx", "content": base64.b64encode(stream.getvalue()).decode()}

    # Prevent generic ORM operations against the virtual model. Supported entry
    # points always recheck the setting and source record rules.
    def _search(self, *args, **kwargs):
        self._check_report_access()
        raise AccessError(_("Use the Preparation Summary report to read its data."))

    def read(self, *args, **kwargs):
        self._check_report_access()
        raise AccessError(_("Use the Preparation Summary report to read its data."))

    @api.model_create_multi
    def create(self, vals_list):
        raise AccessError(_("Preparation Summary is read-only."))

    def write(self, vals):
        raise AccessError(_("Preparation Summary is read-only."))

    def unlink(self):
        raise AccessError(_("Preparation Summary is read-only."))


class PreparationSummaryPDF(models.AbstractModel):
    _name = "report.stock_request.preparation_summary_pdf"
    _description = "Preparation Summary PDF"

    @api.model
    def _get_report_values(self, docids, data=None):
        data = data or {}
        report = self.env["stock.request.preparation.summary"]
        if data.get("allowed_company_ids"):
            report = report.with_context(allowed_company_ids=data["allowed_company_ids"])
        result = report._export_data(data.get("domain", []))
        result.update(doc_ids=[], doc_model=report._name, docs=report)
        return result
