import logging
import math
import uuid

from odoo.tools.mail import html2plaintext

from .exceptions import BlendAPIError
from .stock_availability import BlendStockAvailabilityService


_logger = logging.getLogger(__name__)


class BlendPOSAPIService:
    """Implementation of the Manual POS endpoints (Menu and Branches) published by Blend."""

    # Blend stores modifier groups and modifiers as menu-wide records keyed by
    # ID, and a modifier belongs to one group. Sharing a Bunbastic group or
    # option ID between products (or reusing an option ID in several groups)
    # makes Blend merge or move them: groups go empty, choices appear under
    # the wrong group and one product's price replaces another's. Every native
    # group a product shows and every choice inside it is therefore published
    # under its own ID, in two disjoint ranges reserved above native Odoo IDs
    # and below the signed 32-bit limit:
    #   groups:    GROUP_ID_BASE + pos.product.modifier.group.line id
    #   modifiers: MODIFIER_ID_BASE + bs.blend.modifier.identity id
    MODIFIER_ID_BASE = 1_500_000_000
    GROUP_ID_BASE = 2_000_000_000
    ID_LIMIT = 2_147_483_647
    # 19.0.1.12.0 briefly published per-product option settings as
    # GROUP_ID_BASE + pos.product.modifier.option.line id; accept those orders.
    LEGACY_OPTION_LINE_BASE = GROUP_ID_BASE

    @classmethod
    def _ranged_id(cls, base, upper, record_id):
        value = base + int(record_id)
        if value > upper:
            raise BlendAPIError("Blend modifier ID range exhausted (%s)." % record_id, 422)
        return value

    @classmethod
    def group_api_id(cls, group_line):
        return cls._ranged_id(cls.GROUP_ID_BASE, cls.ID_LIMIT, group_line.id)

    @classmethod
    def modifier_api_id(cls, identity_id):
        return cls._ranged_id(cls.MODIFIER_ID_BASE, cls.GROUP_ID_BASE - 1, identity_id)

    @classmethod
    def decode_modifier_id(cls, value):
        """Return ``("identity" | "option_line", local id)`` for a reserved ID, else ``None``."""
        text = str(value or "").strip()
        if not text.isdecimal():
            return None
        number = int(text)
        if cls.MODIFIER_ID_BASE < number < cls.GROUP_ID_BASE:
            return "identity", number - cls.MODIFIER_ID_BASE
        if cls.LEGACY_OPTION_LINE_BASE < number <= cls.ID_LIMIT:
            return "option_line", number - cls.LEGACY_OPTION_LINE_BASE
        return None

    @staticmethod
    def _product_overrides(group_line):
        """Per-product option settings that belong to the line's current group."""
        group = group_line.modifier_group_id
        if "option_line_ids" not in group_line._fields:
            return {}
        return {
            line.option_id.id: line
            for line in group_line.option_line_ids
            if line.option_id.group_id == group
        }

    def __init__(self, env, configs, request_headers=None, request_id=None, connection=None):
        self.env = env
        self.configs = configs.with_env(env).sudo().sorted("id")
        self.connection = connection.with_env(env).sudo() if connection else connection
        self.request_headers = request_headers or {}
        self.request_id = request_id or uuid.uuid4().hex
        self._stock_availability = {}
        # Set to a list by the product "Blend Menu Preview" to record why each
        # option is sent, closed or left out; ``None`` in normal API calls.
        self.trace = None

    @staticmethod
    def _text(value):
        return str(value or "").strip()

    @staticmethod
    def _number(value, field_name, *, minimum=None):
        if isinstance(value, bool):
            raise BlendAPIError("%s must be numeric." % field_name)
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise BlendAPIError("%s must be numeric." % field_name) from None
        if not math.isfinite(number):
            raise BlendAPIError("%s must be a finite number." % field_name)
        if minimum is not None and number < minimum:
            raise BlendAPIError("%s must be at least %s." % (field_name, minimum))
        return number

    @staticmethod
    def _quantity(value, field_name):
        number = BlendPOSAPIService._number(value, field_name, minimum=1)
        if not number.is_integer():
            raise BlendAPIError("%s must be an integer." % field_name)
        return int(number)

    @staticmethod
    def _canonical_id(value):
        value = str(value or "").strip()
        return str(int(value)) if value.isdecimal() else value

    @staticmethod
    def _record_external_id(record):
        external = getattr(record, "blend_external_id", False)
        if external and str(external).strip().isdecimal():
            return BlendPOSAPIService._canonical_id(external)
        return str(record.id)

    @staticmethod
    def _numeric_api_id(value, field_name):
        text = str(value or "").strip()
        if not text.isdecimal():
            raise BlendAPIError("%s must be a numeric ID." % field_name, 422)
        return int(text)

    @staticmethod
    def _localized(record, field_name):
        def read(language):
            if not language:
                return ""
            if language != "en_US":
                try:
                    lang_data = record.env["res.lang"]._get_data(code=language)
                    if not lang_data:
                        return ""
                except Exception:
                    return ""
            try:
                value = record.with_context(lang=language)[field_name] or ""
            except Exception:
                return ""
            if field_name == "public_description":
                value = html2plaintext(str(value))
            return str(value).strip()

        english = read("en_US") or str(record[field_name] or "").strip()
        arabic = ""
        ar_code = "ar_001"
        try:
            if not record.env["res.lang"]._get_data(code=ar_code):
                active_ar = record.env["res.lang"].search([("code", "=like", "ar_%"), ("active", "=", True)], limit=1)
                ar_code = active_ar.code if active_ar else None
        except Exception:
            ar_code = None

        if ar_code:
            arabic = read(ar_code)

        return {"en": english or arabic, "ar": arabic or english}

    def _resolve_config(self, branch_id, brand_id=None):
        branch = self._text(branch_id)
        if not branch:
            raise BlendAPIError("branch_id is required.")
        matches = self.configs.filtered(lambda config: config.blend_branch_id == branch)
        brand = self._text(brand_id)
        if brand:
            matches = matches.filtered(lambda config: config.blend_brand_id == brand)
        if not matches:
            raise BlendAPIError("No authorized Blend POS branch matches this request.", 404)
        if len(matches) > 1:
            raise BlendAPIError("Blend branch mapping is ambiguous.", 409)
        return matches

    def get_branches(self):
        schedule_model = self.env["product.template"].sudo()
        branches = []
        for branch in self.configs:
            partner = branch.company_id.partner_id
            location = partner.contact_address or partner.display_name or branch.name
            schedule_open = schedule_model._blend_schedule_visible(
                branch.blend_schedule_json, branch
            )
            branches.append(
                {
                    "id": branch.blend_branch_id,
                    "name": branch.name,
                    "status": bool(branch.blend_available and schedule_open),
                    "brand_id": branch.blend_brand_id or "",
                    "location": str(location).strip(),
                }
            )
        return {"branches": branches}

    def _products_for_config(self, config):
        return self.env["product.template"]._blend_menu_products(config, [
            ("active", "=", True),
            ("delivery_available", "=", True),
            ("blend_menu_active", "=", True),
            ("blend_is_available", "=", True),
        ])

    def _menu_pricelist(self):
        connection = self.connection
        if not connection:
            connections = self.env["bs.delivery.connection"].sudo().search([
                ("provider_id.code", "=", "blend"),
                ("active", "=", True),
                ("pos_config_ids", "in", self.configs.ids),
            ])
            if len(connections) != 1:
                raise BlendAPIError("Blend menu must resolve to one delivery connection.", 409)
            connection = connections[0]
        pricelist = connection._get_menu_pricelist()
        if not pricelist:
            raise BlendAPIError("Configure an active menu pricelist on this Blend connection.", 409)
        return pricelist

    def _product_available(self, product, config, *, modifier=False):
        if not product or not product.active or (not modifier and not product.available_in_pos):
            return False
        if not getattr(product, "delivery_available", True):
            return False
        schedule_model = self.env["product.template"].sudo()
        if not config.blend_available or not schedule_model._blend_schedule_visible(
            config.blend_schedule_json, config
        ):
            return False
        stock = self._stock_availability.setdefault(config.id, {})
        if product.id not in stock:
            stock.update(BlendStockAvailabilityService(self.env).stock_available(product, config))
        if not stock.get(product.id, True):
            return False
        if product.blend_external_id:
            return product._blend_is_visible_now(config)
        return config not in product.blend_unavailable_config_ids

    def _product_image_url(self, product):
        attachment = self.env["ir.attachment"].sudo().search(
            [
                ("res_model", "=", "product.template"),
                ("res_id", "=", product.id),
                ("res_field", "=", "image_1920"),
            ],
            limit=1,
        )
        if not attachment:
            return ""
        base_url = self.env["ir.config_parameter"].sudo().get_param(
            "web.base.url", ""
        )
        url = "%s/api/blend/products/%s/image" % (
            base_url.rstrip("/"),
            product.id,
        )
        if attachment.checksum:
            url += "?v=%s" % attachment.checksum
        return url

    def _product_identity_map(self, products):
        result = {}
        for product in products:
            key = self._record_external_id(product)
            if key in result and result[key] != product:
                raise BlendAPIError(
                    "Ambiguous Blend product ID '%s': native and external IDs must be unique within the branch catalogue." % key,
                    422,
                )
            result[key] = product
        return result

    @staticmethod
    def _modifier_product_in_catalog(product, config):
        if not product or not product.active:
            return False
        template = product.product_tmpl_id
        return bool(
            template.active
            and template.sale_ok
            and template.delivery_available
            and template.blend_menu_active
            and (not template.blend_external_id or template.blend_is_available)
            and (not template.blend_config_id or template.blend_config_id == config)
            and config not in template.blend_unavailable_config_ids
        )

    def _trace(self, **row):
        if self.trace is not None:
            self.trace.append(row)

    @staticmethod
    def _catalog_reason(product, config):
        """Why ``_modifier_product_in_catalog`` rejects a modifier product (preview only)."""
        if not product:
            return "No product on the option"
        template = product.product_tmpl_id
        checks = [
            (product.active and template.active, "Product archived"),
            (template.sale_ok, "Product is not 'Can be Sold'"),
            (template.delivery_available, "'Delivery Platforms' is unchecked on the product"),
            (template.blend_menu_active, "Product 'Active in Blend Menu' is off (left by an earlier Blend menu import)"),
            (not template.blend_external_id or template.blend_is_available,
             "Product 'Available in Blend' is off (Blend ID %s)" % template.blend_external_id),
            (not template.blend_config_id or template.blend_config_id == config,
             "Product belongs to another Blend branch (%s)" % template.blend_config_id.display_name),
            (config not in template.blend_unavailable_config_ids, "Product closed in this branch"),
        ]
        return next((reason for ok, reason in checks if not ok), "")

    def _availability_reason(self, template, config):
        """Why ``_product_available`` reports a modifier as unavailable (preview only)."""
        if not template.active:
            return "Product archived"
        if not template.delivery_available:
            return "'Delivery Platforms' is unchecked on the product"
        schedule_model = self.env["product.template"].sudo()
        if not config.blend_available:
            return "Branch is closed in Blend"
        if not schedule_model._blend_schedule_visible(config.blend_schedule_json, config):
            return "Branch is outside its Blend schedule"
        if template.blend_external_id and not template._blend_is_visible_now(config):
            return "Hidden by its Blend availability or schedule (Blend ID %s)" % template.blend_external_id
        if config in template.blend_unavailable_config_ids:
            return "Product closed in this branch"
        return "Unavailable"

    def _modifier_groups(self, product, config, modifier_group_filter=None, pricelist=None):
        result = []
        # 1. Product Modifiers (pos.modifier.group via pos.product.modifier.group.line)
        if getattr(product, "allow_modifiers", False) and hasattr(product, "modifier_group_line_ids"):
            group_lines = product.modifier_group_line_ids.sorted("sequence")
            for group_line in group_lines:
                group = group_line.modifier_group_id
                group_skip = (
                    "Group archived" if not group.active
                    else "Group belongs to another Blend branch"
                    if group.blend_config_id and group.blend_config_id != config
                    else "Group 'Active in Blend Menu' is off" if not group.blend_menu_active
                    else "Group 'Available in Blend' is off" if not group.blend_is_available
                    else ""
                )
                if group_skip:
                    self._trace(group=group.display_name, option="", sent=False, reason=group_skip)
                    continue

                overrides = self._product_overrides(group_line)
                # Blend-owned groups keep Blend's IDs; native groups are
                # published per product (see GROUP_ID_BASE).
                native = not group.blend_external_id
                if native:
                    group_id = self.group_api_id(group_line)
                    active_options = group.option_ids.filtered("active")
                    identities = self.env["bs.blend.modifier.identity"].sudo()._ids_for(
                        (group_line.id, option.id) for option in active_options
                    )
                else:
                    group_id = self._numeric_api_id(
                        self._record_external_id(group), "modifier_groups[].id"
                    )
                if modifier_group_filter and str(group_id) != modifier_group_filter:
                    continue
                minimum = max(int(group.min_qty or 0), 0)
                maximum = max(int(group.max_qty or 1), minimum, 1)
                multiple = bool(group.selection_type == "multiple" or maximum != 1)
                required = bool(minimum > 0)

                modifiers = []
                for option in group.option_ids.filtered("active").sorted(
                    lambda item: (
                        overrides[item.id].sequence if item.id in overrides else item.sequence,
                        item.id,
                    )
                ):
                    override = overrides.get(option.id)
                    effective_record = override if (override and override.name) else option
                    if override and not override.available:
                        self._trace(
                            group=group.display_name, option=effective_record.name, sent=False,
                            reason="'Available' is unchecked for this product",
                        )
                        continue

                    option_product = override.product_id if override and override.product_id else option.product_id
                    if option.option_type == "product" and not self._modifier_product_in_catalog(
                        option_product, config
                    ):
                        self._trace(
                            group=group.display_name, option=effective_record.name, sent=False,
                            product=option_product.display_name,
                            reason=self._catalog_reason(option_product, config),
                        )
                        continue
                    price_mode = override.price_mode if override and override.price_mode else option.price_mode
                    extra_price = override.extra_price if override and override.extra_price is not False else option.extra_price

                    if price_mode == "fixed_extra":
                        price = float(extra_price or 0.0)
                    elif price_mode == "product_pricelist" and option_product:
                        price = float(pricelist._get_product_price(option_product, 1.0))
                    else:
                        price = 0.0

                    status = bool(getattr(group, "blend_is_available", True))
                    status_reason = ""
                    if option.option_type == "product" and option_product:
                        if not self._product_available(
                            option_product.product_tmpl_id, config, modifier=True,
                        ):
                            status = False
                            if self.trace is not None:
                                status_reason = self._availability_reason(
                                    option_product.product_tmpl_id, config,
                                )

                    if native:
                        option_id = self.modifier_api_id(
                            identities[(group_line.id, option.id)]
                        )
                    else:
                        option_id = self._numeric_api_id(
                            self._record_external_id(option), "modifiers[].id"
                        )
                    self._trace(
                        group=group.display_name, group_id=group_id, option=effective_record.name,
                        option_id=option_id, product=option_product.display_name or "",
                        sent=True, status=status, price=price,
                        reason=status_reason or ("Shown in Blend" if status else ""),
                    )
                    modifiers.append(
                        {
                            "id": option_id,
                            "status": status,
                            "name": self._localized(effective_record, "name"),
                            "price": price,
                        }
                    )

                if not any(modifier["status"] for modifier in modifiers):
                    self._trace(
                        group=group.display_name, option="", sent=False,
                        reason="Whole group not sent: none of its options is available",
                    )
                    continue

                result.append(
                    {
                        "id": group_id,
                        "name": self._localized(group, "name"),
                        "multiple": multiple,
                        "required": required,
                        "min_selection": minimum,
                        "max_selection": maximum,
                        "modifiers": modifiers,
                    }
                )

        # 2. Combos (product.combo via product.combo_ids)
        if getattr(product, "combo_ids", False):
            for combo in product.combo_ids.sorted("sequence"):
                if hasattr(combo, "blend_menu_active") and not combo.blend_menu_active:
                    continue
                combo_external_id = self._record_external_id(combo)
                if modifier_group_filter and combo_external_id != modifier_group_filter:
                    continue
                combo_id = self._numeric_api_id(
                    combo_external_id, "modifier_groups[].id"
                )
                min_qty = getattr(combo, "qty_min", 0)
                max_qty = getattr(combo, "qty_max", 1) or 1
                minimum = max(int(min_qty or 0), 0)
                maximum = max(int(max_qty or 1), minimum, 1)
                multiple = bool(maximum != 1)
                required = bool(minimum > 0)
                if hasattr(combo, "blend_is_required") and combo.blend_is_required:
                    required = True

                modifiers = []
                for item in combo.combo_item_ids:
                    item_product = item.product_id
                    status = bool(
                        item_product.active
                        and self._product_available(item_product.product_tmpl_id, config)
                    )
                    item_id = self._numeric_api_id(
                        self._record_external_id(item), "modifiers[].id"
                    )
                    modifiers.append(
                        {
                            "id": item_id,
                            "status": status,
                            "name": self._localized(item_product, "name"),
                            "price": float(getattr(item, "extra_price", 0.0) or 0.0),
                        }
                    )

                if not any(modifier["status"] for modifier in modifiers):
                    continue

                result.append(
                    {
                        "id": combo_id,
                        "name": self._localized(combo, "name"),
                        "multiple": multiple,
                        "required": required,
                        "min_selection": minimum,
                        "max_selection": maximum,
                        "modifiers": modifiers,
                    }
                )

        return result

    def get_menu(self, *, page=1, per=25, filters=None, path=""):
        pricelist = self._menu_pricelist()
        filters = filters or {}
        product_filter = self._canonical_id(filters.get("product_id"))
        category_filter = self._text(filters.get("category_id"))
        modifier_group_filter = self._text(filters.get("modifier_group_id"))

        products_by_config = {
            branch.id: self._products_for_config(branch) for branch in self.configs
        }
        stock_service = BlendStockAvailabilityService(self.env)
        for branch in self.configs:
            products = products_by_config[branch.id]
            modifier_products = self.env["product.template"]
            if hasattr(products, "modifier_group_line_ids"):
                modifier_products |= products.modifier_group_line_ids.modifier_group_id.option_ids.product_id.product_tmpl_id
            if hasattr(products, "combo_ids"):
                modifier_products |= products.combo_ids.combo_item_ids.product_id.product_tmpl_id
            self._stock_availability[branch.id] = stock_service.stock_available(
                products | modifier_products, branch,
            )
        product_maps = {
            branch.id: self._product_identity_map(products_by_config[branch.id])
            for branch in self.configs
        }
        categories = {}
        for branch in self.configs:
            for product in products_by_config[branch.id]:
                product_id = self._record_external_id(product)
                if product_filter and product_id != product_filter:
                    continue
                groups = self._modifier_groups(
                    product, branch, modifier_group_filter or None, pricelist=pricelist
                )
                if modifier_group_filter and not groups:
                    continue
                product_categories = product.pos_categ_ids.filtered(
                    lambda category: not category.blend_config_id
                    or category.blend_config_id == branch
                )
                if branch.limit_categories:
                    product_categories &= branch.iface_available_categ_ids
                if not product_categories:
                    product_categories = self.env["pos.category"].sudo().browse()

                availability = []
                for target in self.configs:
                    target_product = product_maps[target.id].get(product_id)
                    availability.append(
                        {
                            "branch_id": target.blend_branch_id,
                            "is_stock": self._product_available(target_product, target),
                        }
                    )
                in_stock = any(item["is_stock"] for item in availability)
                product_data = {
                    "id": self._numeric_api_id(product_id, "products[].id"),
                    "name": self._localized(product, "name"),
                    "description": self._localized(product, "public_description"),
                    "stock": in_stock,
                    "status": bool(product.active and product.available_in_pos and in_stock),
                    "price": pricelist._get_product_price(product, 1.0),
                    "image_url": self._product_image_url(product),
                    "available_branches": availability,
                    "modifier_groups": groups,
                }

                category_records = product_categories or [False]
                for category in category_records:
                    category_id = (
                        self._record_external_id(category)
                        if category
                        else "uncategorized"
                    )
                    if category_filter and category_id != category_filter:
                        continue
                    category_data = categories.setdefault(
                        category_id,
                        {
                            "id": self._numeric_api_id(
                                category_id, "categories[].id"
                            )
                            if category
                            else 0,
                            "name": self._localized(category, "name")
                            if category
                            else {"en": "Uncategorized", "ar": "غير مصنف"},
                            "products": {},
                            "sequence": category.sequence if category else 999999,
                        },
                    )
                    category_data["products"].setdefault(product_id, product_data)

        ordered = sorted(
            categories.values(), key=lambda item: (item["sequence"], str(item["id"]))
        )
        product_rows = [
            (category, product)
            for category in ordered
            for product in category["products"].values()
        ]
        total_pages = (len(product_rows) + per - 1) // per
        start = (page - 1) * per
        selected = product_rows[start : start + per]
        category_payload = []
        for category, product in selected:
            if not category_payload or category_payload[-1]["id"] != category["id"]:
                category_payload.append(
                    {
                        "id": category["id"],
                        "name": category["name"],
                        "products": [],
                    }
                )
            category_payload[-1]["products"].append(product)
        return {
            "categories": category_payload,
            "pagination": {
                "from": 1 if total_pages else 0,
                "to": total_pages,
                "per": per,
                "total": total_pages,
                "current": page,
                "path": path,
            },
        }
