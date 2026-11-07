from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.fields import Domain


class ProductTemplate(models.Model):
    """Extend products used by native combos and modifier flows."""

    _inherit = "product.template"

    allow_modifiers = fields.Boolean(string="Allow Modifiers")
    modifier_group_line_ids = fields.One2many(
        comodel_name="pos.product.modifier.group.line",
        inverse_name="product_tmpl_id",
        string="Modifier Group Lines",
        copy=True,
    )
    modifier_option_line_ids = fields.One2many(
        "pos.product.modifier.option.line", "product_tmpl_id",
        string="Product Modifier Options", copy=False,
    )
    modifier_option_editor_changes = fields.Json(
        string="Pending Modifier Option Changes", copy=False,
        help="Temporary changes applied when the product form is saved.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [vals.copy() for vals in vals_list]
        changes = [vals.pop("modifier_option_editor_changes", False) for vals in vals_list]
        templates = super().create(vals_list)
        for template, draft in zip(templates, changes):
            template._apply_modifier_option_editor_changes(draft)
        return templates

    def write(self, vals):
        vals = vals.copy()
        changes = vals.pop("modifier_option_editor_changes", False)
        if changes:
            self.check_access("write")
        result = super().write(vals) if vals else True
        for template in self:
            template._apply_modifier_option_editor_changes(changes)
        return result

    def _apply_modifier_option_editor_changes(self, changes):
        self.ensure_one()
        if not changes:
            return
        if not isinstance(changes, list):
            raise ValidationError(_("Invalid modifier option changes."))
        group_lines = {line.modifier_group_id.id: line for line in self.modifier_group_line_ids}
        seen = set()
        for change in changes:
            if (not isinstance(change, dict)
                    or set(change) - {"group_id", "option_id", "values", "reset"}):
                raise ValidationError(_("Invalid modifier option changes."))
            group_id, option_id = change.get("group_id"), change.get("option_id")
            if (type(group_id) is not int or type(option_id) is not int):
                raise ValidationError(_("Invalid modifier option changes."))
            if (group_id, option_id) in seen or group_id not in group_lines:
                raise ValidationError(_("Modifier option is not linked to this product."))
            seen.add((group_id, option_id))
            line = group_lines[group_id]
            if change.get("reset") is True and "values" not in change:
                line.reset_option_from_editor(option_id)
            elif "values" in change and not change.get("reset"):
                line.save_option_from_editor(option_id, change["values"])
            else:
                raise ValidationError(_("Invalid modifier option changes."))

    def action_sync_modifier_options(self):
        option_line_model = self.env["pos.product.modifier.option.line"]
        values = []
        for template in self:
            for group_line in template.modifier_group_line_ids:
                existing_option_ids = set(group_line.option_line_ids.option_id.ids)
                for option in group_line.modifier_group_id.option_ids.filtered("active"):
                    if option.id in existing_option_ids:
                        continue
                    values.append({
                        "group_line_id": group_line.id,
                        "product_tmpl_id": template.id,
                        "option_id": option.id,
                        "name": option.name,
                        "product_id": option.product_id.id,
                        "price_mode": option.price_mode,
                        "extra_price": option.extra_price,
                        "default_qty": option.default_qty,
                        "max_qty": option.max_qty,
                        "kitchen_name": option.kitchen_name,
                        "sequence": option.sequence,
                    })
        if values:
            option_line_model.create(values)
        return True
    hide_from_pos_screen = fields.Boolean(
        string="Hide from POS Product Screen",
        help="Keep this product available as a modifier while hiding it from the main POS product grid.",
    )
    is_pos_modifier_product = fields.Boolean(
        string="Used as Modifier Product",
        compute="_compute_is_pos_modifier_product",
        store=True,
    )

    @api.depends(
        "product_variant_ids.pos_modifier_option_ids.active",
        "product_variant_ids.pos_modifier_option_ids.option_type",
    )
    def _compute_is_pos_modifier_product(self):
        for template in self:
            template.is_pos_modifier_product = any(
                option.active and option.option_type == "product"
                for option in template.product_variant_ids.pos_modifier_option_ids
            )

    @api.model
    def _load_pos_data_domain(self, data, config):
        domain = super()._load_pos_data_domain(data, config)
        # Delivery catalogues reuse the POS eligibility rules, not its screen filter.
        if self.env.context.get("skip_pos_screen_visibility_filter"):
            return domain
        return Domain.AND([
            domain,
            [("hide_from_pos_screen", "=", False)],
        ])

    @api.model
    def _load_pos_data_fields(self, config_id):
        fields_to_load = super()._load_pos_data_fields(config_id)
        if not config_id.enable_product_modifiers:
            return fields_to_load
        return fields_to_load + [
            "allow_modifiers", "modifier_group_line_ids", "hide_from_pos_screen",
            "is_pos_modifier_product",
        ]

class ProductProduct(models.Model):
    _inherit = "product.product"

    pos_modifier_option_ids = fields.One2many(
        comodel_name="pos.modifier.option",
        inverse_name="product_id",
        string="Modifier Options",
    )
