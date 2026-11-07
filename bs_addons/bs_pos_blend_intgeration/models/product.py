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
        string="Available in Blend",
        default=True,
        copy=False,
    )
    blend_menu_active = fields.Boolean(
        string="Active in Blend Menu",
        default=True,
        copy=False,
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

    _blend_product_external_unique = models.Constraint(
        "unique (blend_config_id, blend_external_id, use_as_modifier)",
        "A Blend product or modifier ID must be unique inside a POS branch.",
    )

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
        if self.blend_config_id and self.blend_config_id != config:
            return False
        if config in self.blend_unavailable_config_ids:
            return False
        if not self.blend_external_id:
            return True
        if (
            not self.blend_menu_active
            or not self.blend_is_available
            or not config.blend_available
        ):
            return False
        if not self._blend_schedule_visible(config.blend_schedule_json, config, moment):
            return False
        if not self._blend_schedule_visible(self.blend_schedule_json, config, moment):
            return False
        categories = self.pos_categ_ids.filtered(
            lambda category: category.blend_config_id == config
            and category.blend_external_id
        )
        if categories and not any(
            category.blend_menu_active
            and self._blend_schedule_visible(category.blend_schedule_json, config, moment)
            for category in categories
        ):
            return False
        return True

    @api.model
    def _load_pos_data_fields(self, config):
        result = list(super()._load_pos_data_fields(config))
        for field_name in (
            "blend_config_id",
            "blend_external_id",
            "blend_is_available",
            "blend_menu_active",
            "blend_schedule_json",
        ):
            if field_name not in result:
                result.append(field_name)
        return result

    def _load_pos_data_search_read(self, data, config):
        products = super()._load_pos_data_search_read(data, config)
        if not config.blend_enabled or not products:
            return products
        template_ids = [product["id"] for product in products]
        blend_products = self.sudo().with_context(active_test=False).browse(template_ids).exists()
        product_by_id = {product.id: product for product in blend_products}
        result = []
        for values in products:
            product = product_by_id.get(values["id"])
            if not product:
                continue
            if product.blend_config_id and product.blend_config_id != config:
                continue
            if product.blend_external_id or config in product.blend_unavailable_config_ids:
                # Keep hidden products loaded so a later availability event or
                # schedule boundary can make them visible without reloading POS.
                values["available_in_pos"] = product._blend_is_visible_now(config)
            result.append(values)
        return result

    @api.model
    def _blend_menu_products(self, config, extra_domain=None):
        """One selection rule for both GET /api/menu and its invalidations."""
        config.ensure_one()
        model = self.sudo().with_company(config.company_id).with_context(active_test=False)
        return model.search([
            *model._load_pos_data_domain({}, config),
            ("use_as_modifier", "=", False),
            "|", ("blend_config_id", "=", False), ("blend_config_id", "=", config.id),
            *(extra_domain or []),
        ], order="pos_sequence, id")

    @api.model
    def _blend_public_image_product(self, product_id):
        """Grant image-only access to products published in a Blend menu.

        This private helper deliberately uses the menu's sudo/company scope.
        It does not grant the public user access to product data or attachments.
        Availability is not a publication rule: an out-of-stock menu item may
        still display its picture. Removing it from all enabled menus revokes
        access on the next request.
        """
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
            "name",
            "public_description",
            "list_price",
            "available_in_pos",
            "sale_ok",
            "active",
            "company_id",
            "pos_sequence",
            "image_1920",
            "image_1024",
            "image_512",
            "image_256",
            "image_128",
            "pos_categ_ids",
            "modifier_grp_ids",
            "is_modifier",
            "use_as_modifier",
            "blend_config_id",
            "blend_external_id",
            "blend_is_available",
            "blend_menu_active",
            "blend_schedule_json",
            "blend_unavailable_config_ids",
            "is_storable",
            "type",
            "uom_id",
        }
        if not watched.intersection(vals) or self.env.context.get("blend_inbound_sync"):
            return super().write(vals)
        notifier = BlendMenuNotificationService(self.env)
        before = notifier.snapshot(self)
        from ..services.stock_availability import BlendStockAvailabilityService

        availability = BlendStockAvailabilityService(self.env)
        availability_fields = {"blend_is_available", "blend_unavailable_config_ids", "blend_schedule_json",
                               "blend_menu_active", "is_storable", "type", "uom_id", "active", "available_in_pos"}
        stock_before = availability.capture_products(self) if availability_fields.intersection(vals) else []
        result = super(ProductTemplate, self.with_context(
            blend_menu_change_id=notifier.change_id,
        )).write(vals)
        targets = before | notifier.snapshot(self)
        if set(vals).issubset({"blend_is_available", "blend_unavailable_config_ids"}):
            unified_ids = set(self.env["pos.config"].sudo().search([("blend_outbound_webhook_url", "!=", False)]).ids)
            targets = {target for target in targets if target[0] not in unified_ids}
        notifier.enqueue(targets)
        availability.enqueue(stock_before)
        return result

    def unlink(self):
        notifier = BlendMenuNotificationService(self.env)
        before = notifier.snapshot(self)
        result = super(ProductTemplate, self.with_context(
            blend_menu_change_id=notifier.change_id,
        )).unlink()
        notifier.enqueue(before)
        return result


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
        string="Active in Blend Menu",
        default=True,
        copy=False,
    )
    blend_schedule_json = fields.Json(string="Blend Schedule", copy=False)
    blend_item_order_json = fields.Json(string="Blend Item Order", copy=False)
    blend_description_json = fields.Json(string="Blend Description", copy=False)

    _blend_category_external_unique = models.Constraint(
        "unique (blend_config_id, blend_external_id)",
        "A Blend category ID must be unique inside a POS branch.",
    )

    @api.model
    def _load_pos_data_fields(self, config):
        result = list(super()._load_pos_data_fields(config))
        for field_name in (
            "blend_config_id",
            "blend_external_id",
            "blend_menu_active",
            "blend_schedule_json",
            "blend_item_order_json",
        ):
            if field_name not in result:
                result.append(field_name)
        return result

    @api.model
    def _load_pos_data_search_read(self, data, config):
        categories = super()._load_pos_data_search_read(data, config)
        if not config.blend_enabled or not categories:
            return categories
        records = self.sudo().with_context(active_test=False).browse(
            [values["id"] for values in categories]
        )
        allowed_ids = {
            category.id
            for category in records
            if not category.blend_config_id or category.blend_config_id == config
        }
        return [values for values in categories if values["id"] in allowed_ids]

    @api.model_create_multi
    def create(self, vals_list):
        notifier = BlendMenuNotificationService(self.env)
        records = super(PosCategory, self.with_context(
            blend_menu_change_id=notifier.change_id,
        )).create(vals_list)
        notifier.enqueue(notifier.snapshot(records))
        return records.with_env(self.env)

    def write(self, vals):
        watched = {
            "name",
            "sequence",
            "parent_id",
            "blend_config_id",
            "blend_external_id",
            "blend_menu_active",
            "blend_schedule_json",
            "blend_description_json",
        }
        if not watched.intersection(vals) or self.env.context.get("blend_inbound_sync"):
            return super().write(vals)
        notifier = BlendMenuNotificationService(self.env)
        before = notifier.snapshot(self)
        result = super(PosCategory, self.with_context(
            blend_menu_change_id=notifier.change_id,
        )).write(vals)
        notifier.enqueue(before | notifier.snapshot(self))
        return result

    def unlink(self):
        notifier = BlendMenuNotificationService(self.env)
        before = notifier.snapshot(self)
        result = super(PosCategory, self.with_context(
            blend_menu_change_id=notifier.change_id,
        )).unlink()
        notifier.enqueue(before)
        return result


class ModifiersGroups(models.Model):
    _inherit = "modifiers.groups"

    blend_config_id = fields.Many2one(
        "pos.config",
        string="Blend POS Branch",
        copy=False,
        index=True,
        ondelete="set null",
    )
    blend_external_id = fields.Char(string="Blend ID", copy=False, index=True)
    blend_is_required = fields.Boolean(string="Required in Blend", copy=False)
    blend_min_selection = fields.Integer(string="Blend Minimum Selection", default=0, copy=False)
    blend_max_selection = fields.Integer(string="Blend Maximum Selection", default=1, copy=False)
    blend_max_per_quantity = fields.Integer(
        string="Blend Maximum per Quantity",
        default=1,
        copy=False,
    )
    blend_item_external_ids_json = fields.Json(string="Blend Item IDs", copy=False)
    blend_menu_active = fields.Boolean(string="Active in Blend Menu", default=True, copy=False)
    blend_is_available = fields.Boolean(string="Available in Blend", default=True, copy=False)

    _blend_modifier_group_external_unique = models.Constraint(
        "unique (blend_config_id, blend_external_id)",
        "A Blend modifier group ID must be unique inside a POS branch.",
    )

    @api.constrains(
        "blend_min_selection",
        "blend_max_selection",
        "blend_max_per_quantity",
    )
    def _check_blend_selection_limits(self):
        for group in self:
            if (
                group.blend_min_selection < 0
                or group.blend_max_selection < 1
                or group.blend_max_selection < group.blend_min_selection
                or group.blend_max_per_quantity < 1
            ):
                raise ValidationError("Invalid Blend modifier selection limits.")

    @api.model_create_multi
    def create(self, vals_list):
        notifier = BlendMenuNotificationService(self.env)
        records = super(ModifiersGroups, self.with_context(
            blend_menu_change_id=notifier.change_id,
        )).create(vals_list)
        notifier.enqueue(notifier.snapshot(records))
        return records.with_env(self.env)

    def write(self, vals):
        watched = {
            "name",
            "modifier_ids",
            "blend_config_id",
            "blend_external_id",
            "blend_is_required",
            "blend_min_selection",
            "blend_max_selection",
            "blend_max_per_quantity",
            "blend_menu_active",
            "blend_is_available",
        }
        if not watched.intersection(vals) or self.env.context.get("blend_inbound_sync"):
            return super().write(vals)
        notifier = BlendMenuNotificationService(self.env)
        before = notifier.snapshot(self)
        result = super(ModifiersGroups, self.with_context(
            blend_menu_change_id=notifier.change_id,
        )).write(vals)
        notifier.enqueue(before | notifier.snapshot(self))
        return result

    def unlink(self):
        notifier = BlendMenuNotificationService(self.env)
        before = notifier.snapshot(self)
        result = super(ModifiersGroups, self.with_context(
            blend_menu_change_id=notifier.change_id,
        )).unlink()
        notifier.enqueue(before)
        return result


class ProductModifiers(models.Model):
    _inherit = "product.modifiers"

    blend_group_external_id = fields.Char(index=True)
    blend_is_required = fields.Boolean()
    blend_min_selection = fields.Integer(default=0)
    blend_max_selection = fields.Integer(default=1)
    blend_max_per_quantity = fields.Integer(default=1)
    blend_group_available = fields.Boolean(default=True)

    @api.model
    def _load_pos_data_fields(self, config):
        result = list(super()._load_pos_data_fields(config))
        for field_name in (
            "blend_group_external_id",
            "blend_is_required",
            "blend_min_selection",
            "blend_max_selection",
            "blend_max_per_quantity",
            "blend_group_available",
        ):
            if field_name not in result:
                result.append(field_name)
        return result
