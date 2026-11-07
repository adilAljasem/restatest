"""Dependency-free package checks plus tests of the actual review calculation.

This does not replace the Odoo/PostgreSQL integration suite in tests/.
Run: python mrp_bom_consumption_adjustment/tools/check_module.py
"""
import ast
import csv
import json
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


class Records(list):
    def filtered(self, selector):
        return Records(x for x in self if (getattr(x, selector) if isinstance(selector, str) else selector(x)))

    def mapped(self, name):
        return [getattr(x, name) for x in self]

    def sorted(self, name):
        return Records(sorted(self, key=lambda r: getattr(r, name)))

    @property
    def ids(self):
        return [x.id for x in self]


class Unit:
    def __init__(self, scale=1):
        self.scale = scale
        self.id = 1

    def _compute_quantity(self, quantity, target, round=False):
        return quantity * self.scale / target.scale

    def round(self, quantity):
        return round(quantity, 6)


class CalculationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        module = ast.parse((ROOT / 'models' / 'adjustment.py').read_text(encoding='utf8'))
        model = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == 'Adjustment')
        method = next(n for n in model.body if isinstance(n, ast.FunctionDef) and n.name == '_calculate')
        extracted = ast.Module(body=[method], type_ignores=[])
        scope = {'json': json, '_': lambda text, *args: text % args if args else text,
                 'UserError': ValueError, 'Command': NS(set=lambda ids: (6, 0, ids))}
        exec(compile(ast.fix_missing_locations(extracted), str(ROOT / 'models' / 'adjustment.py'), 'exec'), scope)
        cls.calculate = staticmethod(scope['_calculate'])

    def scenario(self, recorded=20, new=0.25, previous=0, executed=100, base=1,
                 reverse=False, stock_unit=None, move_unit=None):
        unit = stock_unit or Unit()
        product = NS(id=1, uom_id=unit, standard_price=10)
        product.with_company = lambda company: product
        leaf = NS(id=12, product_id=product, display_name='Raw')
        move = NS(id=33, product_id=product, quantity=recorded, product_uom=move_unit or unit,
                  date='2026-10-25', location_id=NS(id=1, usage='customer' if reverse else 'internal'),
                  location_dest_id=NS(id=2, usage='internal' if reverse else 'customer'))
        move._recipe_original_bom_line = lambda: leaf
        source = NS(id=4, display_name='Execution', quantity=executed, uom_id=Unit(), product_id=NS(id=99),
                    needs_review=False, move_ids=Records([move]))
        line = NS(id=5, apply_change=True, bom_line_id=NS(id=6))
        prior_model = NS(search=lambda domain: Records([NS(adjustment_quantity=previous)]))
        document = NS(line_ids=Records([line]), bom_quantity=base, bom_uom_id=Unit(),
                      company_id=1, env={'mrp.bom.consumption.detail': prior_model})
        document._leaves = lambda product, line, new=False: [(leaf, quantity)]
        quantity = new
        return document, Records([source]), line, move

    def test_acceptance_net_consumption_and_repeat(self):
        document, sources, _, _ = self.scenario(recorded=16, new=4, executed=8)
        details, _ = self.calculate(document, sources)
        self.assertEqual([details[0][f] for f in ('recorded_quantity', 'required_quantity',
                         'adjustment_quantity', 'unit_cost', 'adjustment_value')], [16, 32, 16, 10, 160])
        document, sources, _, _ = self.scenario(recorded=16, new=4, executed=8, previous=16)
        details, _ = self.calculate(document, sources)
        self.assertEqual(details[0]['recorded_quantity'], 32)
        self.assertEqual(details[0]['adjustment_quantity'], 0)
        self.assertEqual(details[0]['adjustment_value'], 0)

    def test_additional_consumption(self):
        document, sources, _, _ = self.scenario()
        details, signature = self.calculate(document, sources)
        self.assertEqual(details[0]['required_quantity'], 25)
        self.assertEqual(details[0]['adjustment_quantity'], 5)
        self.assertEqual(details[0]['adjustment_value'], 50)
        self.assertTrue(json.loads(signature))

    def test_reduced_consumption(self):
        document, sources, _, _ = self.scenario(recorded=30)
        details, _ = self.calculate(document, sources)
        self.assertEqual(details[0]['adjustment_quantity'], -5)
        self.assertEqual(details[0]['adjustment_value'], -50)

    def test_overlapping_period_correction(self):
        document, sources, _, _ = self.scenario(previous=5)
        details, _ = self.calculate(document, sources)
        self.assertEqual(details[0]['adjustment_quantity'], 0)
        self.assertEqual(details[0]['recorded_quantity'], 25)

    def test_negative_previous_correction(self):
        document, sources, _, _ = self.scenario(recorded=30, previous=-5)
        details, _ = self.calculate(document, sources)
        self.assertEqual(details[0]['adjustment_quantity'], 0)
        self.assertEqual(details[0]['recorded_quantity'], 25)

    def test_bom_batch_of_ten(self):
        document, sources, _, _ = self.scenario(new=2.5, base=10)
        details, _ = self.calculate(document, sources)
        self.assertEqual(details[0]['adjustment_quantity'], 5)

    def test_customer_return(self):
        document, sources, _, _ = self.scenario(reverse=True, executed=-100)
        details, _ = self.calculate(document, sources)
        self.assertEqual(details[0]['recorded_quantity'], -20)
        self.assertEqual(details[0]['required_quantity'], -25)
        self.assertEqual(details[0]['adjustment_quantity'], -5)

    def test_unit_conversion(self):
        document, sources, _, _ = self.scenario(recorded=20000, move_unit=Unit(0.001))
        details, _ = self.calculate(document, sources)
        self.assertEqual(details[0]['recorded_quantity'], 20)
        self.assertEqual(details[0]['adjustment_quantity'], 5)

    def test_unchecked_lines_ignored(self):
        document, sources, line, _ = self.scenario()
        line.apply_change = False
        details, _ = self.calculate(document, sources)
        self.assertFalse(details)

    def test_unverified_sources_ignored_until_verified(self):
        document, sources, _, _ = self.scenario()
        sources[0].needs_review = True
        details, _ = self.calculate(document, sources)
        self.assertFalse(details)

    def test_intermediate_transfer_not_consumption(self):
        document, sources, _, move = self.scenario()
        move.location_dest_id.usage = 'internal'
        details, _ = self.calculate(document, sources)
        self.assertEqual(details[0]['recorded_quantity'], 0)

    def test_ambiguous_kit_paths_rejected(self):
        document, sources, line, _ = self.scenario()
        document.line_ids.append(NS(id=9, apply_change=False))
        with self.assertRaises(ValueError):
            self.calculate(document, sources)

    def test_missing_bom_provenance_rejected(self):
        document, sources, _, move = self.scenario()
        move._recipe_original_bom_line = lambda: False
        with self.assertRaises(ValueError):
            self.calculate(document, sources)


class PackageTests(unittest.TestCase):
    def test_python_and_xml(self):
        for path in ROOT.rglob('*.py'):
            ast.parse(path.read_text(encoding='utf8'), filename=str(path))
        for path in ROOT.rglob('*.xml'):
            ET.parse(path)
        messages = set()
        for block in (ROOT / 'i18n' / 'ar.po').read_text(encoding='utf8').split('\n\n'):
            source = target = None
            for line in block.splitlines():
                if line.startswith('msgid '):
                    source = ast.literal_eval(line[6:])
                elif line.startswith('msgstr '):
                    target = ast.literal_eval(line[7:])
            if source:
                self.assertNotIn(source, messages, 'duplicate translation')
                messages.add(source)
                self.assertTrue(target, source)
                self.assertEqual(source.count('%s'), target.count('%s'), source)

    def test_manifest_and_acl_references(self):
        manifest = ast.literal_eval((ROOT / '__manifest__.py').read_text())
        models = set()
        for path in (ROOT / 'models').glob('*.py'):
            for node in ast.walk(ast.parse(path.read_text(encoding='utf8'))):
                if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == '_name' for t in node.targets):
                    models.add('model_' + ast.literal_eval(node.value).replace('.', '_'))
        for filename in manifest['data']:
            self.assertTrue((ROOT / filename).is_file(), filename)
        with (ROOT / 'security' / 'ir.model.access.csv').open(newline='') as stream:
            for acl in csv.DictReader(stream):
                if '.' not in acl['model_id:id']:
                    self.assertIn(acl['model_id:id'], models)
                else:
                    self.assertIn(acl['model_id:id'], {'account.model_account_move',
                        'account.model_account_move_line', 'account.model_account_journal'})
                self.assertEqual(acl['group_id:id'], 'mrp.group_mrp_manager')


if __name__ == '__main__':
    unittest.main(verbosity=2)
