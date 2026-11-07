from odoo import models
from odoo.tools.safe_eval import safe_eval


def _action_context(action, companies):
    context = action.get("context") or {}
    if isinstance(context, str):
        context = safe_eval(context, {"allowed_company_ids": companies.ids})
    return dict(context)


class ProductProduct(models.Model):
    _inherit = "product.product"

    def action_used_in_bom(self):
        action = super().action_used_in_bom()
        context = _action_context(action, self.env.companies)
        context.pop("selected_component_product_tmpl_id", None)
        action["context"] = {**context, "selected_component_product_id": self.id}
        return action


class ProductTemplate(models.Model):
    _inherit = "product.template"

    def action_used_in_bom(self):
        action = super().action_used_in_bom()
        context = _action_context(action, self.env.companies)
        context.pop("selected_component_product_id", None)
        action["context"] = {**context, "selected_component_product_tmpl_id": self.id}
        return action
