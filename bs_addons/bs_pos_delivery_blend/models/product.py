import pytz

from odoo import api, fields, models
from odoo.exceptions import ValidationError

from ..services.menu_notifications import BlendMenuNotificationService


class ProductTemplate(models.Model):
    _inherit = "product.template"

    blend_config_id = fields.Many2one(
        "pos.config",
        string="Blend POS Branch",
        copy=False,
        index=True,
        ondelete="set null",
    )
    blend_external_id = fields.Char(string="Blend ID", copy=False, index=True)
    blend_is_available = fields.Boolean(
        string="Available in Blend", default=True, copy=False
    )
    blend_menu_active = fields.Boolean(
        string="Active in Blend Menu", default=True, copy=False
    )
    blend_schedule_json = fields.Json(string="Blend Schedule", copy=False)
    blend_image_urls_json = fields.Json(string="Blend Image URLs", copy=False)
    blend_unavailable_config_ids = fields.Many2many(
        "pos.config",
        "blend_product_unavailable_config_rel",
        "product_tmpl_id",
        "config_id",
        string="Unavailable in Blend Branches",
        copy=False,
    )
    blend_is_option = fields.Boolean(string="Blend Topping Option", copy=False)
    blend_is_base = fields.Boolean(string="Blend Combo Base", copy=False)
    blend_option_price = fields.Float(string="Blend Topping Price", copy=False)

    @api.model
    def _blend_schedule_visible(self, schedule, config, moment=None):
        if not schedule or not isinstance(schedule, dict):
            return True
        timezone = config.company_id.partner_id.tz or self.env.user.tz or "UTC"
        moment = moment or fields.Datetime.now()
        if moment.tzinfo is None:
            moment = pytz.UTC.localize(moment)
        local_time = moment.astimezone(pytz.timezone(timezone))
        intervals = schedule.get(local_time.strftime("%A").lower())
        if intervals is None:
            return True
        if isinstance(intervals, dict):
            intervals = [intervals]
        minute = local_time.hour * 60 + local_time.minute
        for interval in intervals or []:
            if not isinstance(interval, dict) or not interval.get("is_visible"):
                continue
            try:
                start_h, start_m = map(int, str(interval.get("start", "00:00")).split(":"))
                end_h, end_m = map(int, str(interval.get("end", "00:00")).split(":"))
            except (TypeError, ValueError):
                continue
            start = start_h * 60 + start_m
            end = end_h * 60 + end_m
            if start == end:
                return True
            if start < end and start <= minute < end:
                return True
            if start > end and (minute >= start or minute < end):
                return True
        return False

    def _blend_is_visible_now(self, config, moment=None):
        self.ensure_one()
        if not getattr(self, "delivery_available", True):
            return False
        if self.blend_config_id and self.blend_config_id != config:
            return False
        if config in self.blend_unavailable_config_ids:
            return False
        if not self.blend_external_id:
            return True
        if not self.blend_menu_active or not self.blend_is_available or not config.blend_available:
            return False
        if not self._blend_schedule_visible(config.blend_schedule_json, config, moment):
            return False
        if not self._blend_schedule_visible(self.blend_schedule_json, config, moment):
            return False
        categories = self.pos_categ_ids.filtered(
            lambda category: category.blend_external_id and category.blend_menu_active
        )
        if categories and not any(
            self._blend_schedule_visible(category.blend_schedule_json, config, moment)
            for category in categories
        ):
            return False
        return True

    def _blend_menu_products(self, config, extra_domain=None):
        config.ensure_one()
        model = self.sudo().with_company(config.company_id).with_context(active_test=False)
        pos_domain = model.with_context(
            skip_pos_screen_visibility_filter=True
        )._load_pos_data_domain({}, config)
        domain = [
            *pos_domain,
            "|", ("blend_config_id", "=", False), ("blend_config_id", "=", config.id),
            # Imported Blend toppings remain options, not standalone menu items.
            ("blend_is_option", "=", False),
        ]
        if extra_domain:
            domain.extend(extra_domain)
        return model.search(domain, order="pos_sequence, id")

    @api.model
    def _blend_public_image_product(self, product_id):
        products = self.sudo().with_context(active_test=False)
        product = products.browse(product_id).exists()
        if not product:
            return products.browse()
        domain = [("blend_enabled", "=", True)]
        if product.blend_config_id:
            domain.append(("id", "=", product.blend_config_id.id))
        configs = self.env["pos.config"].sudo().search(domain)
        for config in configs:
            if products._blend_menu_products(config, [("id", "=", product.id)]):
                return product
        return products.browse()

    def _load_pos_data_search_read(self, data, config):
        products = super()._load_pos_data_search_read(data, config)
        if not config.blend_enabled or not products:
            return products
        template_ids = [product["id"] for product in products]
        blend_products = self.sudo().with_context(active_test=False).browse(template_ids).exists()
        blocked_ids = {
            product.id
            for product in blend_products
            if product.blend_external_id and not product._blend_is_visible_now(config)
        }
        return [product for product in products if product["id"] not in blocked_ids]

    @api.model_create_multi
    def create(self, vals_list):
        notifier = BlendMenuNotificationService(self.env)
        records = super(ProductTemplate, self.with_context(
            blend_menu_change_id=notifier.change_id,
        )).create(vals_list)
        notifier.enqueue(notifier.snapshot(records))
        return records.with_env(self.env)

    def write(self, vals):
        watched = {
            "name", "public_description", "list_price", "available_in_pos",
            "sale_ok", "active", "company_id", "pos_sequence", "image_1920",
            "image_1024", "image_512", "image_256", "image_128",
            "pos_categ_ids", "modifier_group_line_ids", "combo_ids",
            # Per-product option settings (Available, name, product, price)
            # saved from the product's Modifiers tab.
            "modifier_option_editor_changes", "modifier_option_line_ids",
            "allow_modifiers", "blend_config_id", "blend_external_id",
            "blend_is_available", "blend_menu_active", "blend_schedule_json",
            "blend_unavailable_config_ids", "delivery_available", "is_storable",
            "type", "uom_id",
        }
        if (
            not watched.intersection(vals)
            or self.env.context.get("blend_inbound_sync")
            or self.env.context.get("blend_skip_menu_notification")
        ):
            return super().write(vals)
        notifier = BlendMenuNotificationService(self.env)
        before = notifier.snapshot(self)
        result = super(ProductTemplate, self.with_context(
            blend_menu_change_id=notifier.change_id,
        )).write(vals)
        notifier.enqueue(before | notifier.snapshot(self))
        return result

    def unlink(self):
        notifier = BlendMenuNotificationService(self.env)
        before = notifier.snapshot(self)
        result = super(ProductTemplate, self.with_context(
            blend_menu_change_id=notifier.change_id,
        )).unlink()
        notifier.enqueue(before)
        return result

    def action_delivery_toggle_availability(self):
        # Core sends item.updated for this button; a second menu.updated would duplicate it.
        return super(ProductTemplate, self.with_context(
            blend_skip_menu_notification=True,
        )).action_delivery_toggle_availability()


class PosCategory(models.Model):
    _inherit = "pos.category"

    blend_config_id = fields.Many2one(
        "pos.config",
        string="Blend POS Branch",
        copy=False,
        index=True,
        ondelete="set null",
    )
    blend_external_id = fields.Char(string="Blend ID", copy=False, index=True)
    blend_menu_active = fields.Boolean(
        string="Active in Blend Menu", default=True, copy=False
    )
    blend_schedule_json = fields.Json(string="Blend Schedule", copy=False)
    blend_item_order_json = fields.Json(string="Blend Item Order", copy=False)
    blend_description_json = fields.Json(string="Blend Description", copy=False)


class PosModifierGroup(models.Model):
    _inherit = "pos.modifier.group"

    blend_config_id = fields.Many2one(
        "pos.config",
        string="Blend POS Branch",
        copy=False,
        index=True,
        ondelete="set null",
    )
    blend_external_id = fields.Char(string="Blend Group ID", copy=False, index=True)
    blend_menu_active = fields.Boolean(
        string="Active in Blend Menu", default=True, copy=False
    )
    blend_is_available = fields.Boolean(
        string="Available in Blend", default=True, copy=False
    )
    blend_item_external_ids_json = fields.Json(copy=False)

    _blend_modifier_group_external_unique = models.Constraint(
        "unique (blend_config_id, blend_external_id)",
        "A Blend modifier group ID must be unique inside a POS branch.",
    )

    def _blend_parent_products(self):
        return self.product_modifier_line_ids.product_tmpl_id

    def write(self, vals):
        watched = {
            "name", "option_ids", "min_qty", "max_qty", "active",
            "blend_external_id", "blend_config_id", "blend_menu_active",
            "blend_is_available",
        }
        if not watched.intersection(vals) or self.env.context.get("blend_inbound_sync"):
            return super().write(vals)
        notifier = BlendMenuNotificationService(self.env)
        before = notifier.snapshot(self._blend_parent_products())
        result = super().write(vals)
        notifier.enqueue(before | notifier.snapshot(self._blend_parent_products()))
        return result

    def unlink(self):
        notifier = BlendMenuNotificationService(self.env)
        before = notifier.snapshot(self._blend_parent_products())
        result = super().unlink()
        notifier.enqueue(before)
        return result


class PosModifierOption(models.Model):
    _inherit = "pos.modifier.option"

    blend_external_id = fields.Char(string="Blend Option ID", copy=False, index=True)

    _blend_modifier_option_external_unique = models.Constraint(
        "unique (group_id, blend_external_id)",
        "A Blend modifier option ID must be unique inside its group.",
    )

    def _blend_parent_products(self):
        return self.group_id.product_modifier_line_ids.product_tmpl_id

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        if not self.env.context.get("blend_inbound_sync"):
            notifier = BlendMenuNotificationService(self.env)
            notifier.enqueue(notifier.snapshot(records._blend_parent_products()))
        return records

    def write(self, vals):
        watched = {
            "name", "product_id", "price_mode", "extra_price", "max_qty",
            "active", "group_id", "blend_external_id",
        }
        if not watched.intersection(vals) or self.env.context.get("blend_inbound_sync"):
            return super().write(vals)
        notifier = BlendMenuNotificationService(self.env)
        before = notifier.snapshot(self._blend_parent_products())
        result = super().write(vals)
        notifier.enqueue(before | notifier.snapshot(self._blend_parent_products()))
        return result

    def unlink(self):
        notifier = BlendMenuNotificationService(self.env)
        before = notifier.snapshot(self._blend_parent_products())
        result = super().unlink()
        notifier.enqueue(before)
        return result


class PosProductModifierOptionLine(models.Model):
    """Per-product option settings also change what Blend shows for the product."""

    _inherit = "pos.product.modifier.option.line"

    _blend_watched = frozenset({
        "available", "name", "product_id", "price_mode", "extra_price",
        "max_qty", "sequence", "option_id", "group_line_id",
    })

    def _blend_parent_products(self):
        return self.product_tmpl_id | self.group_line_id.product_tmpl_id

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        if not self.env.context.get("blend_inbound_sync"):
            notifier = BlendMenuNotificationService(self.env)
            notifier.enqueue(notifier.snapshot(records._blend_parent_products()))
        return records

    def write(self, vals):
        if not self._blend_watched.intersection(vals) or self.env.context.get("blend_inbound_sync"):
            return super().write(vals)
        notifier = BlendMenuNotificationService(self.env)
        before = notifier.snapshot(self._blend_parent_products())
        result = super().write(vals)
        notifier.enqueue(before | notifier.snapshot(self._blend_parent_products()))
        return result

    def unlink(self):
        if self.env.context.get("blend_inbound_sync"):
            return super().unlink()
        notifier = BlendMenuNotificationService(self.env)
        before = notifier.snapshot(self._blend_parent_products())
        result = super().unlink()
        notifier.enqueue(before)
        return result


class ProductCombo(models.Model):
    _inherit = "product.combo"

    blend_external_id = fields.Char(string="Blend ID", copy=False, index=True)
    blend_is_required = fields.Boolean(string="Required in Blend", copy=False)
    blend_min_selection = fields.Integer(
        string="Blend Minimum Selection", default=0, copy=False
    )
    blend_max_selection = fields.Integer(
        string="Blend Maximum Selection", default=1, copy=False
    )
    blend_max_per_quantity = fields.Integer(
        string="Blend Maximum per Quantity", default=1, copy=False
    )
    blend_item_external_ids_json = fields.Json(string="Blend Item IDs", copy=False)
    blend_menu_active = fields.Boolean(
        string="Active in Blend Menu", default=True, copy=False
    )

    @api.constrains(
        "blend_min_selection", "blend_max_selection", "blend_max_per_quantity"
    )
    def _check_blend_selection_limits(self):
        for group in self.filtered("blend_external_id"):
            if (
                group.blend_min_selection < 0
                or group.blend_max_selection < group.blend_min_selection
                or group.blend_max_per_quantity < 1
            ):
                raise ValidationError("Invalid Blend modifier selection limits.")
