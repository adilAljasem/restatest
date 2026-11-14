import base64
import ipaddress
import logging
import socket
from urllib.parse import urlparse

import requests

from odoo import Command, _, fields

from .exceptions import BlendAPIError


_logger = logging.getLogger(__name__)


class BlendMenuService:
    """Apply Blend's documented menu schema without provider-owned mapping tables."""

    MAX_IMAGE_BYTES = 5 * 1024 * 1024

    def __init__(self, env, connection, store):
        self.env = env
        self.connection = connection.sudo().with_company(connection.company_id)
        self.store = store.sudo()
        self.config = store.pos_config_id.sudo().with_company(connection.company_id)
        inactive = {"active_test": False}
        self.category_model = env["pos.category"].sudo().with_context(**inactive)
        self.product_model = (
            env["product.template"]
            .sudo()
            .with_company(connection.company_id)
            .with_context(**inactive)
        )
        self.group_model = env["pos.modifier.group"].sudo().with_context(**inactive)
        self.active_language_codes = set(
            env["res.lang"].sudo().search([("active", "=", True)]).mapped("code")
        )

    @staticmethod
    def _required_id(value, label):
        result = str(value or "").strip()
        if not result:
            raise BlendAPIError("%s is required." % label)
        return result

    @staticmethod
    def _localized(value):
        if isinstance(value, dict):
            return {
                "en": str(value.get("en") or "").strip(),
                "ar": str(value.get("ar") or "").strip(),
            }
        text = str(value or "").strip()
        return {"en": text, "ar": ""}

    @staticmethod
    def _boolean(value, label="value"):
        if isinstance(value, bool):
            return value
        if value in (0, 1):
            return bool(value)
        if isinstance(value, str) and value.strip().lower() in ("true", "false", "1", "0"):
            return value.strip().lower() in ("true", "1")
        raise BlendAPIError("%s must be a boolean value." % label)

    def _write_translations(self, record, field_name, values):
        translations = {}
        if values.get("en") and "en_US" in self.active_language_codes:
            translations["en_US"] = values["en"]
        if values.get("ar") and "ar_001" in self.active_language_codes:
            translations["ar_001"] = values["ar"]
        if not translations:
            return
        try:
            record.update_field_translations(field_name, translations)
        except Exception:
            _logger.warning(
                "Blend translation update failed for %s,%s field=%s",
                record._name,
                record.id,
                field_name,
                exc_info=True,
            )

    @staticmethod
    def _safe_public_host(hostname):
        try:
            addresses = {result[4][0] for result in socket.getaddrinfo(hostname, None)}
        except socket.gaierror:
            return False
        return bool(addresses) and all(
            ipaddress.ip_address(address).is_global for address in addresses
        )

    def _download_image(self, url):
        parsed = urlparse(str(url or ""))
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            return False
        if not self._safe_public_host(parsed.hostname):
            return False
        try:
            # Bandit cannot infer the bounded timeout expression on the next line.
            response = requests.get(  # nosec B113
                url,
                timeout=min(self.connection.blend_timeout_seconds, 20),
                stream=True,
            )
            response.raise_for_status()
            if not (response.headers.get("Content-Type") or "").lower().startswith("image/"):
                return False
            chunks = []
            total = 0
            for chunk in response.iter_content(chunk_size=65536):
                total += len(chunk)
                if total > self.MAX_IMAGE_BYTES:
                    return False
                chunks.append(chunk)
            return base64.b64encode(b"".join(chunks)) if chunks else False
        except requests.RequestException:
            _logger.warning("Blend image download failed for %s", url, exc_info=True)
            return False

    def sync_menu(self, event):
        payload = event.get("payload")
        if not isinstance(payload, dict):
            raise BlendAPIError("menu_sync.payload must be an object.")
        categories = payload.get("categories", [])
        items = payload.get("items", [])
        toppings = payload.get("toppings", [])
        if not all(isinstance(value, list) for value in (categories, items, toppings)):
            raise BlendAPIError("categories, items, and toppings must be arrays.")

        self.config.with_context(blend_inbound_sync=True).write(
            {
                "blend_schedule_json": payload.get("branch_schedule_time") or {},
                "blend_last_menu_sync_at": fields.Datetime.now(),
            }
        )
        category_records = self._sync_categories(categories)
        product_records = self._sync_items(items, category_records, categories)
        modifier_groups, modifier_options = self._sync_toppings(toppings, product_records)
        self._deactivate_missing(
            category_records,
            product_records,
            modifier_groups,
            modifier_options,
        )
        self.config._blend_notify(
            {
                "kind": "menu_sync",
                "branch_id": self.store.remote_store_id,
                "message": "Blend menu synchronized. Reload the POS to load structural menu changes.",
            }
        )
        return {
            "categories": len(category_records),
            "items": len(product_records),
            "toppings": len(modifier_groups),
            "options": len(modifier_options),
        }

    def _sync_categories(self, categories):
        result = self.category_model.browse()
        for sequence, data in enumerate(categories, start=1):
            if not isinstance(data, dict):
                raise BlendAPIError("Each menu category must be an object.")
            external_id = self._required_id(data.get("id"), "categories[].id")
            names = self._localized(data.get("name"))
            category = self.category_model.search(
                [("blend_external_id", "=", external_id)], limit=1
            )
            values = {
                "name": names["en"] or names["ar"] or "Blend Category %s" % external_id,
                "sequence": sequence,
                "blend_external_id": external_id,
                "blend_menu_active": True,
                "blend_schedule_json": data.get("schedule_time") or {},
                "blend_item_order_json": [str(item_id) for item_id in data.get("items", [])],
            }
            if category:
                category.with_context(blend_inbound_sync=True).write(values)
            else:
                category = self.category_model.with_context(blend_inbound_sync=True).create(values)
            self._write_translations(category, "name", names)
            result |= category
        return result

    def _sync_items(self, items, category_records, categories):
        category_by_external = {
            category.blend_external_id: category for category in category_records
        }
        item_sequence = {}
        for category in categories:
            if not isinstance(category, dict):
                continue
            for sequence, item_id in enumerate(category.get("items", []), start=1):
                item_sequence.setdefault(str(item_id), sequence)

        result = self.product_model.browse()
        for data in items:
            if not isinstance(data, dict):
                raise BlendAPIError("Each menu item must be an object.")
            external_id = self._required_id(data.get("id"), "items[].id")
            names = self._localized(data.get("name"))
            descriptions = self._localized(data.get("description"))
            try:
                price = float(data.get("price", 0.0))
            except (TypeError, ValueError):
                raise BlendAPIError("items[].price must be numeric.") from None
            available = self._boolean(data.get("is_available", True), "items[].is_available")
            product = self._product_for_external_id(external_id, modifier=False)
            values = {
                "name": names["en"] or names["ar"] or "Blend Item %s" % external_id,
                "list_price": price,
                "sale_ok": True,
                "available_in_pos": True,
                "active": True,
                "pos_sequence": item_sequence.get(external_id, 10),
                "blend_external_id": external_id,
                "blend_is_available": available,
                "blend_menu_active": True,
                "blend_schedule_json": data.get("schedule_time") or {},
                "blend_image_urls_json": data.get("images") or [],
            }
            if product:
                product.with_context(blend_inbound_sync=True).write(values)
            else:
                values["default_code"] = external_id
                values["type"] = "consu"
                product = self.product_model.with_context(blend_inbound_sync=True).create(values)
            categories_for_product = [
                category_by_external[str(category_id)]
                for category_id in data.get("categories", [])
                if str(category_id) in category_by_external
            ]
            product.with_context(blend_inbound_sync=True).write(
                {"pos_categ_ids": [Command.set([item.id for item in categories_for_product])]}
            )
            self._write_translations(product, "name", names)
            self._write_translations(product, "public_description", descriptions)
            self._bind_product(product, external_id)
            image_urls = data.get("images") or []
            if self.connection.blend_sync_images and image_urls:
                image = self._download_image(image_urls[0])
                if image:
                    product.with_context(blend_inbound_sync=True).image_1920 = image
            result |= product
        return result

    def _sync_toppings(self, toppings, product_records):
        product_by_external = {
            product.blend_external_id: product for product in product_records
        }
        groups_by_product = {
            external_id: self.group_model.browse() for external_id in product_by_external
        }
        group_result = self.group_model.browse()
        option_result = self.product_model.browse()
        for data in toppings:
            if not isinstance(data, dict):
                raise BlendAPIError("Each topping must be an object.")
            external_id = self._required_id(data.get("id"), "toppings[].id")
            names = self._localized(data.get("name"))
            required = self._boolean(data.get("is_required", False), "toppings[].is_required")
            try:
                minimum = int(data.get("min", 0))
                maximum = int(data.get("max", 1))
                per_quantity = int(data.get("max_per_quantity", maximum or 1))
            except (TypeError, ValueError):
                raise BlendAPIError("Topping min/max values must be integers.") from None
            if minimum < 0 or maximum < minimum or per_quantity < 1:
                raise BlendAPIError("Invalid topping selection limits.")
            if required and minimum == 0:
                minimum = 1
            if maximum < 1:
                raise BlendAPIError("A topping group must allow at least one option.")
            group = self.group_model.search([
                ("blend_config_id", "=", self.config.id),
                ("blend_external_id", "=", external_id),
            ], limit=1)
            item_ids = [str(item_id) for item_id in data.get("items", [])]
            options = self.product_model.browse()
            for option_data in data.get("options", []):
                option = self._sync_option(option_data)
                options |= option
                option_result |= option
            if not options:
                raise BlendAPIError("A topping group must have at least one option.")
            values = {
                "name": names["en"] or names["ar"] or "Blend Topping %s" % external_id,
                "blend_config_id": self.config.id,
                "blend_external_id": external_id,
                "blend_item_external_ids_json": item_ids,
                "blend_menu_active": True,
                "blend_is_available": True,
                "active": True,
                "company_id": self.config.company_id.id,
                "selection_type": "multiple" if maximum > 1 else "single",
                "min_qty": minimum,
                "max_qty": maximum,
                "included_qty": 0,
            }
            if group:
                group.with_context(blend_inbound_sync=True).write(values)
            else:
                group = self.group_model.with_context(blend_inbound_sync=True).create(values)
            self._write_translations(group, "name", names)
            option_model = self.env["pos.modifier.option"].sudo().with_context(active_test=False)
            selected_options = option_model.browse()
            for option in options:
                option_record = group.option_ids.filtered(
                    lambda item: item.blend_external_id == option.blend_external_id
                )[:1]
                option_values = {
                    "name": option.name,
                    "blend_external_id": option.blend_external_id,
                    "group_id": group.id,
                    "option_type": "product",
                    "operation": "add",
                    "product_id": option.product_variant_id.id,
                    "price_mode": "fixed_extra",
                    "extra_price": option.blend_option_price,
                    "max_qty": per_quantity,
                    "active": True,
                }
                if option_record:
                    option_record.with_context(blend_inbound_sync=True).write(option_values)
                else:
                    option_record = option_model.with_context(blend_inbound_sync=True).create(option_values)
                selected_options |= option_record
            (group.option_ids - selected_options).filtered("blend_external_id").with_context(
                blend_inbound_sync=True,
            ).write({"active": False})
            group_result |= group
            for item_id in item_ids:
                if item_id in groups_by_product:
                    groups_by_product[item_id] |= group

        all_blend_groups = self.group_model.search([
            ("blend_config_id", "=", self.config.id),
            ("blend_external_id", "!=", False),
        ])
        for product in product_records:
            selected = groups_by_product.get(product.blend_external_id, self.group_model.browse())
            current_lines = product.modifier_group_line_ids
            stale_lines = current_lines.filtered(
                lambda line: line.modifier_group_id in all_blend_groups
                and line.modifier_group_id not in selected
            )
            stale_lines.with_context(blend_inbound_sync=True).unlink()
            for group in selected:
                if group not in product.modifier_group_line_ids.modifier_group_id:
                    self.env["pos.product.modifier.group.line"].sudo().with_context(
                        blend_inbound_sync=True,
                    ).create({
                        "product_tmpl_id": product.id,
                        "modifier_group_id": group.id,
                        "company_id": self.config.company_id.id,
                    })
            preserved_combos = product.combo_ids.filtered(
                lambda combo: not combo.blend_external_id
            )
            product.with_context(blend_inbound_sync=True).write({
                "allow_modifiers": bool(product.modifier_group_line_ids),
                "combo_ids": [Command.set(preserved_combos.ids)],
                "type": "combo" if preserved_combos else "consu",
            })
        return group_result, option_result

    def _sync_option(self, data):
        if not isinstance(data, dict):
            raise BlendAPIError("Each topping option must be an object.")
        external_id = self._required_id(data.get("id"), "toppings[].options[].id")
        names = self._localized(data.get("name"))
        try:
            price = float(data.get("price", 0.0))
        except (TypeError, ValueError):
            raise BlendAPIError("toppings[].options[].price must be numeric.") from None
        product = self._product_for_external_id(external_id, modifier=True)
        values = {
            "name": names["en"] or names["ar"] or "Blend Option %s" % external_id,
            "list_price": price,
            "sale_ok": True,
            "available_in_pos": True,
            "active": True,
            "type": "consu",
            "blend_is_option": True,
            "blend_option_price": price,
            "hide_from_pos_screen": True,
            "blend_config_id": self.config.id,
            "blend_external_id": external_id,
            "blend_is_available": True,
            "blend_menu_active": True,
        }
        if product:
            product.with_context(blend_inbound_sync=True).write(values)
        else:
            values["default_code"] = external_id
            product = self.product_model.with_context(blend_inbound_sync=True).create(values)
        self._write_translations(product, "name", names)
        self._bind_product(product, external_id)
        return product

    def _product_for_external_id(self, external_id, *, modifier):
        binding = self.env["bs.delivery.product.binding"].sudo().search(
            [
                ("connection_id", "=", self.connection.id),
                ("external_product_id", "=", external_id),
                ("active", "=", True),
            ],
            limit=1,
        )
        if binding:
            template = binding.product_id.product_tmpl_id
            if bool(template.blend_is_option) == modifier:
                return template
        return self.product_model.search(
            [
                ("blend_external_id", "=", external_id),
                ("blend_is_option", "=", modifier),
                "|",
                ("company_id", "=", False),
                ("company_id", "=", self.connection.company_id.id),
            ],
            limit=1,
        )

    def _bind_product(self, template, external_id):
        Binding = self.env["bs.delivery.product.binding"].sudo()
        binding = Binding.search(
            [
                ("connection_id", "=", self.connection.id),
                ("external_product_id", "=", external_id),
            ],
            limit=1,
        )
        values = {
            "connection_id": self.connection.id,
            "product_id": template.product_variant_id.id,
            "external_product_id": external_id,
            "external_name": template.name,
            "last_sync_date": fields.Datetime.now(),
            "active": True,
        }
        if binding:
            binding.write(values)
        else:
            Binding.create(values)

    def _deactivate_missing(self, categories, products, groups, options):
        self.category_model.search(
            [("blend_external_id", "!=", False), ("id", "not in", categories.ids)]
        ).with_context(blend_inbound_sync=True).write({"blend_menu_active": False})
        bound_templates = self.env["bs.delivery.product.binding"].sudo().search(
            [("connection_id", "=", self.connection.id), ("active", "=", True)]
        ).mapped("product_id.product_tmpl_id")
        missing_products = bound_templates.filtered(
            lambda product: not product.blend_is_option and product not in products
        )
        missing_options = bound_templates.filtered(
            lambda product: product.blend_is_option and product not in options
        )
        (missing_products | missing_options).with_context(blend_inbound_sync=True).write(
            {"blend_menu_active": False, "blend_is_available": False}
        )
        self.group_model.search(
            [("blend_config_id", "=", self.config.id),
             ("blend_external_id", "!=", False),
             ("id", "not in", groups.ids)]
        ).with_context(blend_inbound_sync=True).write({
            "blend_menu_active": False,
            "active": False,
        })

    def set_item_availability(self, item_id, is_available, *, by_sku=False):
        external_id = self._required_id(item_id, "item_id")
        product = self._product_for_external_id(external_id, modifier=False)
        if not product and by_sku:
            product = self.product_model.search([("default_code", "=", external_id)], limit=1)
            if product:
                product.with_context(blend_inbound_sync=True).write(
                    {"blend_external_id": external_id, "blend_menu_active": True}
                )
                self._bind_product(product, external_id)
        if not product:
            raise BlendAPIError(
                "Blend item '%s' was not found in Odoo products." % external_id, 404
            )
        available = self._boolean(is_available, "is_available")
        product.with_context(blend_inbound_sync=True).write(
            {"blend_is_available": available, "blend_menu_active": True}
        )
        self._notify_availability("item", external_id, product, available)
        return {"item_id": external_id, "is_available": available}

    def set_modifier_availability(self, modifier_id, is_available):
        external_id = self._required_id(modifier_id, "modifier_id")
        product = self._product_for_external_id(external_id, modifier=True)
        if not product:
            raise BlendAPIError(
                "Blend modifier '%s' was not found in Odoo products." % external_id, 404
            )
        available = self._boolean(is_available, "is_available")
        product.with_context(blend_inbound_sync=True).write(
            {"blend_is_available": available, "blend_menu_active": True}
        )
        self._notify_availability("modifier", external_id, product, available)
        return {"modifier_id": external_id, "is_available": available}

    def _notify_availability(self, kind, external_id, product, available):
        self.config._blend_notify(
            {
                "kind": kind,
                "branch_id": self.store.remote_store_id,
                "external_id": external_id,
                "product_tmpl_id": product.id,
                "available": available,
            }
        )

    def set_branch_availability(self, is_available):
        available = self._boolean(is_available, "is_available")
        self.config.with_context(blend_inbound_sync=True).write(
            {"blend_available": available}
        )
        if self.store:
            if not available and not self.store.is_paused:
                self.store.pause_receiving(reason=_("Paused by Blend store update."))
            elif available and self.store.is_paused:
                self.store.resume_receiving()
        self.config._blend_notify(
            {
                "kind": "branch",
                "branch_id": self.store.remote_store_id,
                "available": available,
            }
        )
        return {"branch_id": self.store.remote_store_id, "is_available": available}
