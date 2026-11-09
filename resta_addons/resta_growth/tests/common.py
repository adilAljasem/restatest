from datetime import timedelta

from odoo import Command, fields
from odoo.tests.common import TransactionCase, new_test_user


class GrowthCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.profile = cls.env["resta.growth.profile"]._get(cls.company)
        cls.profile.sudo().write({"activation_date": fields.Date.today() - timedelta(days=120),
                                  "welcome_state": "done", "plan": "trial"})
        cls.engine = cls.env["resta.growth.engine"]
        cls.today = fields.Date.today()
        cls.owner = new_test_user(cls.env, login="growth_owner", name="صاحب المطعم",
                                  groups="base.group_user,resta_growth.group_resta_owner,point_of_sale.group_pos_manager,stock.group_stock_manager")
        cls.cashier = new_test_user(cls.env, login="growth_cashier", name="كاشير",
                                    groups="base.group_user,point_of_sale.group_pos_user")
        cls.support = new_test_user(cls.env, login="growth_support", name="دعم",
                                    groups="base.group_user,resta_growth.group_resta_support,stock.group_stock_manager")
        cls.level1 = cls.env.ref("resta_growth.level_foundation")
        cls.level2 = cls.env.ref("resta_growth.level_discipline")
        cls.inventory = cls.env.ref("resta_growth.feature_inventory")
        cls.accounting = cls.env.ref("resta_growth.feature_accounting")
        Daily = cls.env["resta.growth.daily"].sudo()
        Daily.search([("company_id", "=", cls.company.id)]).unlink()

    def make_days(self, n=60, **vals):
        """n days ending yesterday, all planned and recorded unless overridden."""
        Daily = self.env["resta.growth.daily"].sudo()
        rows = []
        for i in range(n, 0, -1):
            day = self.today - timedelta(days=i)
            row = {"company_id": self.company.id, "date": day, "planned": True, "recorded": True,
                   "orders": 100, "sales_total": 4600.0, "sales_untaxed": 4000.0, "cash_sales": 1000.0,
                   "sessions_closed": 1, "sessions_cash_checked": 1, "sessions_cash_ok": 1,
                   "invoices_total": 100, "invoices_sent": 100, "consumption_value": 1300.0,
                   "waste_value": 30.0}
            row.update({k: (v(day) if callable(v) else v) for k, v in vals.items()})
            rows.append(row)
        return Daily.create(rows)

    def make_menu(self, n=15, complete=True):
        tax = self.env["account.tax"].search([("company_id", "=", self.company.id), ("type_tax_use", "=", "sale")], limit=1)
        categ = self.env["pos.category"].create({"name": "اختبار"})
        self.env["product.template"].search([("available_in_pos", "=", True)]).write({"available_in_pos": False})
        return self.env["product.template"].create([{
            "name": "صنف %s" % i, "list_price": 10 + i, "available_in_pos": True, "type": "consu",
            "taxes_id": [Command.set(tax.ids)] if (complete and tax) else False,
            "pos_categ_ids": [Command.set(categ.ids)] if complete else False,
        } for i in range(n)])

    def status(self, key):
        cond = self.env["resta.growth.condition"].search([("compute_key", "=", key)], limit=1)
        return cond._evaluate(self.profile, self.today)
