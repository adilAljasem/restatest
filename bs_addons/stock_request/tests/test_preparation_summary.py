"""Contract tests against real Odoo domains; the ORM source is a secured fixture.

These tests do not simulate stock operations: changes to remaining quantities
stand for the existing stock workflow's output, which this report only reads.
"""
import base64
import io
import json
import zipfile
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch
from lxml import etree

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests.common import BaseCase
from ..models.preparation_summary import PreparationSummary, PreparationSummaryPDF


class Record(SimpleNamespace):
    def __getitem__(self, key):
        return getattr(self, key)


def record(id, name="Record", **values):
    return Record(id=id, display_name=name, **values)


def line(order_id, quantity, company=1, source=2, product=3, uom=4,
         destination=5, date="2026-01-10", flow="two_step", line_id=None):
    return Record(
        id=line_id or order_id, state="ready",
        order_id=record(order_id, company_id=record(company), source_warehouse_id=record(source),
                        warehouse_id=record(destination), request_date=date, transfer_flow=flow),
        product_id=record(product, "منتج اختبار", default_code="00123"),
        product_uom_id=record(uom, "كجم", rounding=0.01), remaining_delivery_qty=quantity)


def matches(record, domain):
    tokens = iter(list(fields.Domain(domain)))
    def evaluate():
        token = next(tokens)
        if token == "&":
            a, b = evaluate(), evaluate()
            return a and b
        if token == "|":
            a, b = evaluate(), evaluate()
            return a or b
        if token == "!":
            return not evaluate()
        name, op, expected = token
        actual = record
        if not isinstance(name, str):
            return name == expected
        for part in name.split("."):
            actual = actual[part]
        actual = actual.id if isinstance(actual, Record) else actual
        if op == "=": return actual == expected
        if op == "!=": return actual != expected
        if op == "in": return actual in expected
        if op == "not in": return actual not in expected
        if op == ">=": return actual >= expected
        if op == "<=": return actual <= expected
        if op == ">": return actual > expected
        if op == "ilike": return str(expected).lower() in str(actual).lower()
        raise AssertionError(op)
    return evaluate()


class Lines(list):
    def filtered(self, fn):
        return Lines(item for item in self if fn(item))


class Env:
    def _(self, message, *args):
        return message % args if args else message

    def __init__(self, lines, enabled="True", manager=True, responsible=True):
        self.uid = 7
        self.lang = "en_US"
        self.companies = SimpleNamespace(ids=[1], mapped=lambda field: ["Company"])
        self.user = SimpleNamespace(has_group=lambda name: manager if name.endswith("manager") else responsible)
        config = Mock()
        config.sudo.return_value = config
        config.get_param.return_value = enabled
        orders = Mock()
        orders.search.return_value = SimpleNamespace(ids=list({item.order_id.id for item in lines}))
        requests = Mock()
        requests.search.side_effect = lambda domain: Lines(item for item in lines if matches(item, domain))
        self.models = {"ir.config_parameter": config, "stock.request.order": orders, "stock.request": requests}
    def __getitem__(self, name): return self.models[name]
    def ref(self, name): return record(20 if name.endswith("tree") else 21)


class TestPreparationSummary(BaseCase):
    test_tags = {"standard", "at_install"}

    def service(self, lines, **options):
        result = SimpleNamespace(env=Env(lines, **options), _order=PreparationSummary._order)
        result._fields = {name: value for name, value in PreparationSummary.__dict__.items() if isinstance(value, fields.Field)}
        result.fields_get = lambda names: {name: {"string": result._fields[name].string or name} for name in names}
        for name in ("_check_report_access", "_clean_domain", "_line_domain", "_get_lines", "_dataset", "_sorted_rows",
                     "web_search_read", "web_read_group", "open_orders", "_filter_description"):
            setattr(result, name, MethodType(getattr(PreparationSummary, name), result))
        return result

    def test_acceptance_and_distinct_orders(self):
        lines = [line(10, 10), line(11, 15, flow="two_step_no_transit")]
        service = self.service(lines)
        row, = service._dataset([])
        self.assertEqual((row["quantity"], row["order_count"]), (25, 2))
        self.assertEqual(service.open_orders(row["row_key"], [])["domain"], [("id", "in", [10, 11])])
        lines[1].remaining_delivery_qty = 0
        row, = service._dataset([])
        self.assertEqual((row["quantity"], row["order_count"]), (10, 1))
        lines[0].remaining_delivery_qty = 6
        row, = service._dataset([])
        self.assertEqual((row["quantity"], row["order_count"]), (6, 1))

    def test_filter_before_aggregation_and_drilldown(self):
        service = self.service([line(10, 10), line(11, 15, destination=6, date="2026-02-01")])
        for domain in ([('warehouse_id', '=', 5)], [('request_date', '>=', '2026-01-01'), ('request_date', '<=', '2026-01-31')]):
            row, = service._dataset(domain)
            self.assertEqual((row["quantity"], row["order_count"]), (10, 1))
            self.assertEqual(service.open_orders(row["row_key"], domain)["domain"], [("id", "in", [10])])
        self.assertEqual(service._dataset([("code", "ilike", "001")])[0]["quantity"], 25)

    def test_partition_and_duplicate_order_lines(self):
        service = self.service([line(10, 3), line(10, 7, line_id=100), line(11, 15, destination=6),
                                line(12, 8, uom=9), line(13, 9, source=8), line(14, 4, company=9)])
        rows = service._dataset([])
        self.assertEqual(len(rows), 4)
        self.assertEqual((rows[0]["quantity"], rows[0]["order_count"]), (25, 2))

    def test_grouping_counts_and_scoped_records(self):
        service = self.service([line(10, 3), line(10, 7, product=8, line_id=100), line(11, 15, destination=6)])
        result = service.web_read_group([], ["company_id"], auto_unfold=True,
                                        unfold_read_specification={"quantity": {}, "row_key": {}})
        group, = result["groups"]
        self.assertEqual(group["order_count"], 2)  # not 3 summed order counts
        self.assertEqual(group["__count"], 2)
        self.assertNotIn("quantity", group)  # no invalid total across units
        self.assertEqual(len(group["__records"]), 2)
        result = service.web_read_group([], ["product_id"], limit=1)
        self.assertEqual(result["length"], 2)
        result = service.web_read_group([], ["company_id", "product_id"], auto_unfold=True)
        self.assertEqual(result["groups"][0]["__groups"]["length"], 2)

    def test_pagination_and_many2one_protocol(self):
        service = self.service([line(10, 3), line(11, 4, product=8)])
        result = service.web_search_read([], {"product_id": {"fields": {"display_name": {}}}, "quantity": {}}, limit=1)
        self.assertEqual(result["length"], 2)
        self.assertEqual(len(result["records"]), 1)
        self.assertEqual(result["records"][0]["product_id"]["id"], 3)
        self.assertEqual(len(service._dataset([])), 2)

    def test_base_scope_and_responsibility(self):
        service = self.service([line(10, 10)], manager=False)
        service._dataset([])
        domain = service.env["stock.request.order"].search.call_args.args[0]
        for condition in [("operation_type", "=", "request"), ("transfer_flow", "in", ["two_step", "two_step_no_transit"]),
                          ("state", "in", ["ready", "in_transit"]), ("has_pending_delivery", "=", True),
                          ("company_id", "in", [1]), ("source_warehouse_id.stock_request_responsible_user_ids", "in", [7])]:
            self.assertIn(condition, domain)
        service.env["stock.request.order"].sudo.assert_not_called()
        service.env["stock.request"].sudo.assert_not_called()

    def test_precision_and_cancelled_lines(self):
        tiny, cancelled = line(11, 0.001), line(12, 10)
        cancelled.state = "cancel"
        service = self.service([line(10, 6), tiny, cancelled])
        self.assertEqual(service._dataset([])[0]["quantity"], 6)

    def test_legacy_flow_ignored(self):
        service = self.service([line(10, 10), line(11, 15, flow="two_step_no_transit")])
        for operator in ("=", "!="):
            self.assertEqual(service._dataset([("transfer_flow", operator, "two_step")])[0]["quantity"], 25)
        domain = ["|", ("transfer_flow", "=", "two_step"), ("product_id", "=", 99)]
        self.assertEqual(service._dataset(domain), [])

    def test_disabled_and_unauthorized_entry_points(self):
        for options in ({"enabled": "False"}, {"responsible": False}):
            service = self.service([line(10, 10)], **options)
            service._export_data = MethodType(PreparationSummary._export_data, service)
            for call in (lambda: service.web_search_read([], {}), lambda: service.web_read_group([], ["company_id"]),
                         lambda: service.open_orders([1, 2, 3, 4], []),
                         lambda: PreparationSummary.action_print_pdf(service, []),
                         lambda: PreparationSummary.export_xlsx(service, []),
                         lambda: service._export_data([])):
                with self.assertRaises(AccessError): call()

    def test_invalid_domain_and_foreign_key(self):
        service = self.service([line(10, 10)])
        with self.assertRaises(ValidationError): service._dataset([("order_ids", "in", [123])])
        self.assertEqual(service.open_orders([99, 2, 3, 4], [])["domain"], [("id", "in", [])])

    def test_excel_real_numeric_cells_and_complete_dataset(self):
        service = self.service([line(10, 10), line(11, 15, product=8)])
        data = {"rows": service._dataset([]), "generated": "2026-10-21 17:00", "filters": "Company",
                "rtl": True, "title": "ملخص التجهيز", "headers": ["الشركة", "المخزن", "المنتج", "الكود", "الوحدة", "الكمية", "الطلبات"]}
        service._export_data = Mock(return_value=data)
        output = PreparationSummary.export_xlsx(service, [("warehouse_id", "=", 5)])
        service._export_data.assert_called_once_with([("warehouse_id", "=", 5)])
        archive = zipfile.ZipFile(io.BytesIO(base64.b64decode(output["content"])))
        xml = etree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        self.assertEqual(xml.xpath("string(//s:pane/@ySplit)", namespaces=ns), "5")
        self.assertEqual(xml.xpath("string(//s:sheetView/@rightToLeft)", namespaces=ns), "1")
        self.assertEqual(xml.xpath("string(//s:c[@r='F6']/s:v)", namespaces=ns), "10")
        self.assertEqual(xml.xpath("string(//s:c[@r='F7']/s:v)", namespaces=ns), "15")
        self.assertEqual(xml.xpath("string(//s:c[@r='D6']/@t)", namespaces=ns), "s")
        self.assertIn("00123", archive.read("xl/sharedStrings.xml").decode())
        self.assertIn("منتج اختبار", archive.read("xl/sharedStrings.xml").decode())

    def test_pdf_uses_shared_source_and_selected_companies(self):
        report = Mock()
        report.with_context.return_value = report
        report._export_data.return_value = {"rows": []}
        service = SimpleNamespace(env={"stock.request.preparation.summary": report})
        domain = [("warehouse_id", "=", 5)]
        result = PreparationSummaryPDF._get_report_values(service, [], {"domain": domain, "allowed_company_ids": [1]})
        report.with_context.assert_called_once_with(allowed_company_ids=[1])
        report._export_data.assert_called_once_with(domain)
        self.assertEqual(result["rows"], [])
