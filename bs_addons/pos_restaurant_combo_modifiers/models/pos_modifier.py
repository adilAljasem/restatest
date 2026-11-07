from math import ceil

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError


class PosModifierGroup(models.Model):
    _name = "pos.modifier.group"
    _inherit = ["pos.load.mixin"]
    _description = "POS Modifier Group"
    _order = "sequence, id"
    _check_company_auto = True

    _company_active_idx = models.Index("(company_id, active)")

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    selection_type = fields.Selection(
        selection=[
            ("single", "Single"),
            ("multiple", "Multiple"),
        ],
        required=True,
        default="single",
    )
    min_qty = fields.Integer(string="Minimum Quantity", default=0)
    max_qty = fields.Integer(string="Maximum Quantity", default=1)
    included_qty = fields.Integer(string="Included Quantity", default=0)
    display_type = fields.Selection(
        selection=[
            ("grid", "Grid"),
            ("list", "List"),
        ],
        required=True,
        default="grid",
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        default=lambda self: self.env.company,
        required=True,
        index=True,
    )
    active = fields.Boolean(default=True, index=True)
    option_ids = fields.One2many(
        comodel_name="pos.modifier.option",
        inverse_name="group_id",
        string="Modifier Options",
        copy=True,
    )
    product_modifier_line_ids = fields.One2many(
        comodel_name="pos.product.modifier.group.line",
        inverse_name="modifier_group_id",
        string="Product Links",
    )
    product_template_ids = fields.Many2many(
        comodel_name="product.template",
        string="Products",
        compute="_compute_product_template_ids",
    )

    def get_options_for_product_editor(self, product_template_id=False):
        """Read shared options, including before a new product group line is saved."""
        self.ensure_one()
        self.check_access("read")
        group_line = self.env["pos.product.modifier.group.line"]
        if product_template_id:
            template = self.env["product.template"].browse(product_template_id).exists()
            template.check_access("read")
            group_line = template.modifier_group_line_ids.filtered(
                lambda line: line.modifier_group_id == self
            )[:1]
        return self._get_product_editor_options(group_line)

    def _get_product_editor_options(self, group_line):
        self.ensure_one()
        options = self.option_ids.filtered("active").sorted(
            lambda item: (item.sequence, item.id)
        )
        options.check_access("read")
        if group_line:
            group_line.check_access("read")
            group_line.option_line_ids.check_access("read")
        overrides = {line.option_id.id: line for line in group_line.option_line_ids}
        can_edit = self.env.user.has_group("point_of_sale.group_pos_manager")
        result = []
        for option in options:
            override = overrides.get(option.id)
            effective = override or option
            shared = {
                "name": option.name,
                "description": "",
                "product_id": option.product_id.id or False,
                "product_name": option.product_id.display_name or "",
                "price_mode": option.price_mode,
                "extra_price": option.extra_price,
                "default_qty": option.default_qty,
                "max_qty": option.max_qty,
                "kitchen_name": option.kitchen_name or "",
                "available": True,
            }
            result.append({
                "id": option.id,
                "overridden": bool(override),
                "editable": can_edit,
                "option_type": option.option_type,
                "company_id": group_line.company_id.id if group_line else self.company_id.id,
                "shared": shared,
                **shared,
                **({
                    "name": effective.name,
                    "description": override.description or "",
                    "product_id": effective.product_id.id or False,
                    "product_name": effective.product_id.display_name or "",
                    "price_mode": effective.price_mode,
                    "extra_price": effective.extra_price,
                    "default_qty": effective.default_qty,
                    "max_qty": effective.max_qty,
                    "kitchen_name": effective.kitchen_name or "",
                    "available": override.available,
                } if override else {}),
            })
        return result

    @api.depends("product_modifier_line_ids.product_tmpl_id")
    def _compute_product_template_ids(self):
        for group in self:
            group.product_template_ids = group.product_modifier_line_ids.product_tmpl_id

    @api.model
    def _load_pos_data_domain(self, data, config):
        if not config.enable_product_modifiers:
            return False
        group_ids = {
            line["modifier_group_id"]
            for line in data.get("pos.product.modifier.group.line", [])
            if line.get("modifier_group_id")
        }
        return [
            ("id", "in", list(group_ids)),
            ("active", "=", True),
            ("company_id", "=", config.company_id.id),
        ]

    @api.model
    def _load_pos_data_fields(self, config):
        return [
            "id", "name", "sequence", "selection_type", "min_qty", "max_qty",
            "included_qty", "display_type", "option_ids",
        ]

    @api.onchange("selection_type")
    def _onchange_selection_type(self):
        for group in self:
            if group.selection_type == "single":
                group.max_qty = 1
                group.min_qty = min(group.min_qty, 1)
                group.included_qty = min(group.included_qty, 1)

    @api.model_create_multi
    def create(self, vals_list):
        default_selection_type = self.default_get(["selection_type"]).get(
            "selection_type", "single"
        )
        normalized_vals_list = []
        for original_vals in vals_list:
            vals = dict(original_vals)
            if vals.get("selection_type", default_selection_type) == "single":
                vals["max_qty"] = 1
            normalized_vals_list.append(vals)
        return super().create(normalized_vals_list)

    def write(self, vals):
        vals = dict(vals)
        if vals.get("selection_type") == "single":
            vals["max_qty"] = 1
        elif "selection_type" not in vals and "max_qty" in vals:
            single_groups = self.filtered(lambda group: group.selection_type == "single")
            multiple_groups = self - single_groups
            if single_groups:
                single_values = dict(vals, max_qty=1)
                super(PosModifierGroup, single_groups).write(single_values)
            if multiple_groups:
                super(PosModifierGroup, multiple_groups).write(vals)
            return True
        return super().write(vals)

    @api.constrains(
        "selection_type",
        "min_qty",
        "max_qty",
        "included_qty",
    )
    def _check_selection_quantities(self):
        for group in self:
            if group.min_qty < 0:
                raise ValidationError(_("Minimum Quantity cannot be negative."))
            if group.max_qty < 1:
                raise ValidationError(_("Maximum Quantity must be at least 1."))
            if group.included_qty < 0:
                raise ValidationError(_("Included Quantity cannot be negative."))
            if group.selection_type == "single" and group.max_qty != 1:
                raise ValidationError(
                    _("Single selection groups must have a maximum quantity of 1.")
                )
            if group.min_qty > group.max_qty:
                raise ValidationError(
                    _("Minimum Quantity cannot be greater than Maximum Quantity.")
                )
            if group.included_qty > group.max_qty:
                raise ValidationError(
                    _("Included Quantity cannot be greater than Maximum Quantity.")
                )


class PosModifierOption(models.Model):
    _name = "pos.modifier.option"
    _inherit = ["pos.load.mixin"]
    _description = "POS Modifier Option"
    _order = "sequence, id"
    _check_company_auto = True

    _group_active_idx = models.Index("(group_id, active)")
    _product_active_idx = models.Index("(product_id) WHERE active IS TRUE")

    name = fields.Char(required=True, translate=True)
    group_id = fields.Many2one(
        comodel_name="pos.modifier.group",
        required=True,
        ondelete="cascade",
        index=True,
        check_company=True,
    )
    option_type = fields.Selection(
        selection=[
            ("product", "Product"),
            ("instruction", "Instruction"),
        ],
        required=True,
        default="product",
    )
    operation = fields.Selection(
        selection=[
            ("add", "Add"),
            ("remove", "Remove"),
            ("note", "Note"),
        ],
        required=True,
        default="add",
    )
    product_id = fields.Many2one(
        comodel_name="product.product",
        string="Product",
        ondelete="restrict",
        index="btree_not_null",
        check_company=True,
    )
    price_mode = fields.Selection(
        selection=[
            ("included", "Included"),
            ("fixed_extra", "Fixed Extra Price"),
            ("product_pricelist", "Product Pricelist Price"),
        ],
        required=True,
        default="included",
    )
    extra_price = fields.Monetary(
        string="Extra Price",
        currency_field="currency_id",
        default=0.0,
    )
    default_qty = fields.Float(string="Default Quantity", default=0.0, digits=(16, 3))
    max_qty = fields.Integer(string="Maximum Quantity", default=1)
    sequence = fields.Integer(default=10)
    kitchen_name = fields.Char(translate=True)
    active = fields.Boolean(default=True, index=True)
    company_id = fields.Many2one(
        comodel_name="res.company",
        related="group_id.company_id",
        store=True,
        index=True,
    )
    currency_id = fields.Many2one(
        comodel_name="res.currency",
        related="company_id.currency_id",
    )

    _product_group_unique = models.Constraint(
        "unique (group_id, product_id)",
        "The same product cannot be used more than once in a modifier group.",
    )

    @api.model
    def _load_pos_data_domain(self, data, config):
        if not config.enable_product_modifiers:
            return False
        group_ids = [group["id"] for group in data.get("pos.modifier.group", [])]
        return [
            ("group_id", "in", group_ids),
            ("active", "=", True),
            ("company_id", "=", config.company_id.id),
        ]

    @api.model
    def _load_pos_data_fields(self, config):
        return [
            "id", "name", "group_id", "option_type", "operation", "product_id",
            "price_mode", "extra_price", "default_qty", "max_qty", "sequence",
            "kitchen_name", "currency_id",
        ]

    @api.model
    def _load_pos_data_read(self, records, config):
        result = super()._load_pos_data_read(records, config)
        for option in result:
            if option["currency_id"] != config.currency_id.id:
                currency = self.env["res.currency"].browse(option["currency_id"])
                option["extra_price"] = currency._convert(
                    option["extra_price"], config.currency_id,
                    config.company_id, fields.Date.today(),
                )
        return result

    @api.model
    def _append_referenced_products_to_pos_data(self, data, options, config):
        if "product.product" not in data or "product.template" not in data:
            return
        product_ids = {
            option["product_id"]
            for option in options
            if option.get("option_type") == "product" and option.get("product_id")
        }

        # Incremental POS loads only return modifier options changed after the
        # last synchronization date.  Resolve the complete current POS scope
        # as well, otherwise an unchanged option can remain cached while its
        # internal product is still missing from IndexedDB.
        product_template_model = self.env["product.template"]
        pos_templates = product_template_model.search(
            product_template_model._load_pos_data_domain({}, config)
        )
        pos_templates |= (
            pos_templates.filtered(lambda template: template.type == "combo")
            .combo_ids.combo_item_ids.product_id.product_tmpl_id
        )
        modifier_lines = self.env["pos.product.modifier.group.line"].search([
            ("product_tmpl_id", "in", pos_templates.ids),
            ("modifier_group_id.active", "=", True),
            ("company_id", "=", config.company_id.id),
        ])
        if modifier_lines:
            product_ids.update(
                self.search([
                    ("group_id", "in", modifier_lines.modifier_group_id.ids),
                    ("option_type", "=", "product"),
                    ("active", "=", True),
                    ("company_id", "=", config.company_id.id),
                ]).product_id.ids
            )
            product_ids.update(
                self.env["pos.product.modifier.option.line"].search([
                    ("group_line_id", "in", modifier_lines.ids),
                    ("available", "=", True),
                    ("option_id.active", "=", True),
                ]).product_id.ids
            )

        loaded_product_ids = {product["id"] for product in data["product.product"]}
        missing_products = self.env["product.product"].search([
            ("id", "in", product_ids - loaded_product_ids),
            ("active", "=", True),
            "|",
            ("company_id", "=", False),
            ("company_id", "=", config.company_id.id),
        ])
        if not missing_products:
            return

        data["product.product"] += self.env[
            "product.product"
        ]._load_pos_data_read(missing_products, config)

        loaded_template_ids = {
            template["id"] for template in data["product.template"]
        }
        missing_templates = missing_products.product_tmpl_id.filtered(
            lambda template: template.id not in loaded_template_ids
        )
        if missing_templates:
            data["product.template"] += self.env[
                "product.template"
            ]._load_pos_data_read(missing_templates, config)

        if "account.tax" in data:
            loaded_tax_ids = {tax["id"] for tax in data["account.tax"]}
            missing_taxes = missing_products.taxes_id.filtered(
                lambda tax: tax.company_id == config.company_id
                and tax.id not in loaded_tax_ids
            )
            if missing_taxes:
                data["account.tax"] += self.env[
                    "account.tax"
                ]._load_pos_data_read(missing_taxes, config)

    @api.model
    def _load_pos_data_search_read(self, data, config):
        options = super()._load_pos_data_search_read(data, config)
        self._append_referenced_products_to_pos_data(data, options, config)
        return options

    @api.onchange("option_type")
    def _onchange_option_type(self):
        for option in self:
            if option.option_type == "instruction":
                option.product_id = False
                option.price_mode = "included"
                option.extra_price = 0.0

    @api.onchange("price_mode")
    def _onchange_price_mode(self):
        for option in self:
            if option.price_mode != "fixed_extra":
                option.extra_price = 0.0

    @api.onchange("default_qty")
    def _onchange_default_qty(self):
        for option in self:
            if option.default_qty > option.max_qty:
                option.max_qty = ceil(option.default_qty)

    @api.constrains("default_qty", "max_qty")
    def _check_option_quantities(self):
        for option in self:
            if option.default_qty < 0:
                raise ValidationError(_("Default Quantity cannot be negative."))
            if option.max_qty < 1:
                raise ValidationError(_("Option Maximum Quantity must be at least 1."))
            if option.default_qty > option.max_qty:
                raise ValidationError(
                    _("Default Quantity cannot be greater than the option Maximum Quantity.")
                )

    @api.constrains("option_type", "product_id", "price_mode", "extra_price")
    def _check_option_configuration(self):
        for option in self:
            if option.option_type == "product" and not option.product_id:
                raise ValidationError(_("A Product option must have a Product."))
            if option.option_type == "instruction" and (
                option.price_mode != "included" or option.extra_price != 0.0
            ):
                raise ValidationError(_("An Instruction option must have a zero price."))


class PosProductModifierGroupLine(models.Model):
    _name = "pos.product.modifier.group.line"
    _inherit = ["pos.load.mixin"]
    _description = "Product Modifier Group Line"
    _order = "sequence, id"
    _check_company_auto = True

    _product_company_idx = models.Index("(product_tmpl_id, company_id)")

    product_tmpl_id = fields.Many2one(
        comodel_name="product.template",
        string="Product",
        required=True,
        ondelete="cascade",
        index=True,
        check_company=True,
    )
    modifier_group_id = fields.Many2one(
        comodel_name="pos.modifier.group",
        string="Modifier Group",
        required=True,
        ondelete="cascade",
        index=True,
        check_company=True,
    )
    sequence = fields.Integer(default=10)
    auto_open = fields.Boolean(default=True)
    company_id = fields.Many2one(
        comodel_name="res.company",
        default=lambda self: self.env.company,
        required=True,
        index=True,
    )
    option_line_ids = fields.One2many(
        "pos.product.modifier.option.line", "group_line_id",
        string="Product Modifier Options", copy=False,
    )

    _product_group_unique = models.Constraint(
        "unique (product_tmpl_id, modifier_group_id)",
        "The same modifier group cannot be linked twice to a product.",
    )

    @api.model
    def _load_pos_data_domain(self, data, config):
        if not config.enable_product_modifiers:
            return False
        product_template_ids = [
            product["id"]
            for product in data.get("product.template", [])
            if product.get("allow_modifiers")
        ]
        return [
            ("product_tmpl_id", "in", product_template_ids),
            ("modifier_group_id.active", "=", True),
            ("company_id", "=", config.company_id.id),
        ]

    @api.model
    def _load_pos_data_fields(self, config):
        return [
            "id", "product_tmpl_id", "modifier_group_id", "sequence", "auto_open",
            "option_line_ids",
        ]

    def _effective_option(self, option):
        self.ensure_one()
        return self.option_line_ids.filtered(lambda line: line.option_id == option)[:1] or option

    def get_options_for_editor(self):
        """Return shared choices with this product's overrides for the inline editor."""
        self.ensure_one()
        self.check_access("read")
        return self.modifier_group_id._get_product_editor_options(self)

    def reset_option_from_editor(self, option_id):
        """Remove one product override so the shared option becomes effective."""
        self.ensure_one()
        self.check_access("write")
        if not self.env.user.has_group("point_of_sale.group_pos_manager"):
            raise AccessError(_("Only Point of Sale managers can edit modifier options."))
        option = self.modifier_group_id.option_ids.filtered(
            lambda item: item.id == option_id and item.active
        )
        if len(option) != 1:
            raise ValidationError(_("The modifier option does not belong to this group."))
        option.check_access("read")
        lines = self.option_line_ids.filtered(lambda line: line.option_id == option)
        lines.check_access("unlink")
        lines.unlink()
        return self.get_options_for_editor()

    def save_option_from_editor(self, option_id, values):
        """Persist only this product's option, leaving the shared option untouched."""
        self.ensure_one()
        self.check_access("write")
        if not self.env.user.has_group("point_of_sale.group_pos_manager"):
            raise AccessError(
                _("Only Point of Sale managers can edit modifier options.")
            )
        allowed = {
            "name", "description", "product_id", "price_mode", "extra_price",
            "default_qty", "max_qty", "kitchen_name", "available",
        }
        if not isinstance(values, dict) or not values or set(values) - allowed:
            raise ValidationError(_("Invalid modifier option settings."))
        option = self.modifier_group_id.option_ids.filtered(
            lambda item: item.id == option_id and item.active
        )
        if len(option) != 1:
            raise ValidationError(
                _("The modifier option does not belong to this group.")
            )
        option.check_access("read")
        line = self.option_line_ids.filtered(lambda item: item.option_id == option)
        if line:
            line.check_access("write")
            line.write(values)
        else:
            defaults = {
                "group_line_id": self.id,
                "product_tmpl_id": self.product_tmpl_id.id,
                "option_id": option.id,
                "name": option.name,
                "description": "",
                "product_id": option.product_id.id,
                "price_mode": option.price_mode,
                "extra_price": option.extra_price,
                "default_qty": option.default_qty,
                "max_qty": option.max_qty,
                "kitchen_name": option.kitchen_name,
                "sequence": option.sequence,
                **values,
            }
            self.env["pos.product.modifier.option.line"].create(defaults)
        return self.get_options_for_editor()


class PosProductModifierOptionLine(models.Model):
    _name = "pos.product.modifier.option.line"
    _inherit = ["pos.load.mixin"]
    _description = "Product Modifier Option Settings"
    _order = "sequence, id"
    _check_company_auto = True

    group_line_id = fields.Many2one(
        "pos.product.modifier.group.line", required=True, ondelete="cascade",
        index=True, check_company=True,
    )
    product_tmpl_id = fields.Many2one(
        "product.template", required=True, ondelete="cascade", index=True,
        check_company=True,
    )
    option_id = fields.Many2one(
        "pos.modifier.option", required=True, ondelete="cascade", index=True,
        check_company=True,
    )
    modifier_group_id = fields.Many2one(
        "pos.modifier.group", related="group_line_id.modifier_group_id",
        store=True,
    )
    group_id = fields.Many2one("pos.modifier.group", related="option_id.group_id")
    option_type = fields.Selection(related="option_id.option_type")
    name = fields.Char(required=True, translate=True)
    description = fields.Char(translate=True)
    product_id = fields.Many2one(
        "product.product", ondelete="restrict", check_company=True,
    )
    operation = fields.Selection(related="option_id.operation")
    price_mode = fields.Selection(
        selection=[
            ("included", "Included"),
            ("fixed_extra", "Fixed Extra Price"),
            ("product_pricelist", "Product Pricelist Price"),
        ], required=True, default="included",
    )
    extra_price = fields.Monetary(currency_field="currency_id")
    default_qty = fields.Float(digits=(16, 3))
    max_qty = fields.Integer(default=1)
    kitchen_name = fields.Char(translate=True)
    sequence = fields.Integer(default=10)
    available = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", related="group_line_id.company_id", store=True,
    )
    currency_id = fields.Many2one(
        "res.currency", related="company_id.currency_id",
    )

    _group_option_unique = models.Constraint(
        "unique (group_line_id, option_id)",
        "The modifier option is already configured for this product.",
    )

    @api.constrains("group_line_id", "option_id", "product_tmpl_id")
    def _check_links(self):
        for line in self:
            if (line.option_id.group_id != line.group_line_id.modifier_group_id
                    or line.product_tmpl_id != line.group_line_id.product_tmpl_id):
                raise ValidationError(_("The modifier option must belong to this product and group."))

    @api.constrains("default_qty", "max_qty", "product_id", "price_mode", "extra_price")
    def _check_configuration(self):
        for line in self:
            if line.default_qty < 0 or line.max_qty < 1 or line.default_qty > line.max_qty:
                raise ValidationError(_("Invalid modifier option quantity settings."))
            if line.option_type == "product" and not line.product_id:
                raise ValidationError(_("A Product option must have a Product."))
            if line.option_type == "instruction" and (
                line.product_id or line.price_mode != "included" or line.extra_price
            ):
                raise ValidationError(_("An Instruction option must have no product or price."))

    @api.model
    def _load_pos_data_domain(self, data, config):
        if not config.enable_product_modifiers:
            return False
        line_ids = [line["id"] for line in data.get("pos.product.modifier.group.line", [])]
        return [("group_line_id", "in", line_ids), ("company_id", "=", config.company_id.id)]

    @api.model
    def _load_pos_data_fields(self, config):
        return [
            "id", "group_line_id", "option_id", "name", "description", "product_id",
            "price_mode", "extra_price", "default_qty", "max_qty", "kitchen_name",
            "sequence", "available", "currency_id",
        ]

    @api.model
    def _load_pos_data_read(self, records, config):
        result = super()._load_pos_data_read(records, config)
        for line in result:
            if line["currency_id"] != config.currency_id.id:
                currency = self.env["res.currency"].browse(line["currency_id"])
                line["extra_price"] = currency._convert(
                    line["extra_price"], config.currency_id,
                    config.company_id, fields.Date.today(),
                )
        return result
