import base64
import io
import ipaddress
import logging
import socket
from urllib.parse import urlparse

import requests
from PIL import Image, WebPImagePlugin  # noqa: F401 - registers the optional WebP decoder.

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tools.image import IMAGE_MAX_RESOLUTION, image_process

from .exceptions import BlendAPIError


_logger = logging.getLogger(__name__)


class BlendMenuService:
    MAX_IMAGE_BYTES = 5 * 1024 * 1024

    def __init__(self, env, config):
        self.env = env
        self.config = config.sudo().with_company(config.company_id).with_context(blend_inbound_sync=True)
        scoped_context = {"active_test": False}
        self.category_model = env["pos.category"].sudo().with_context(**scoped_context)
        self.product_model = env["product.template"].sudo().with_company(
            config.company_id
        ).with_context(**scoped_context)
        self.group_model = env["modifiers.groups"].sudo().with_context(**scoped_context)
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
            return bool(addresses) and all(
                ip.is_global and not ip.is_multicast and not ip.is_reserved
                for ip in (ipaddress.ip_address(address) for address in addresses)
            )
        except (OSError, ValueError):
            return False

    def _skip_image(self, reason):
        # Signed URLs and Requests exception messages may contain credentials.
        # Log only controlled reasons and the configuration, never the URL.
        _logger.warning("Blend image skipped config_id=%s reason=%s", self.config.id, reason)
        return False

    def _validated_image(self, data):
        if not data:
            return self._skip_image("empty image")
        try:
            # Odoo intentionally passes SVG/WebP through image_process. Check
            # the actual raster data too: image/* alone is not validation.
            with Image.open(io.BytesIO(data)) as image:
                if image.width * image.height > IMAGE_MAX_RESOLUTION:
                    return self._skip_image("image exceeds Odoo's resolution limit")
                image.verify()
            # verify() does not decode pixel data for every format. Decode now
            # so image data errors cannot interrupt the later menu write. Keep
            # Odoo's decoder policy; never toggle process-wide Pillow settings.
            with Image.open(io.BytesIO(data)) as image:
                image.load()
            return base64.b64encode(image_process(data, verify_resolution=True))
        except (UserError, OSError, ValueError, SyntaxError, Image.DecompressionBombError):
            return self._skip_image("invalid, unsupported or excessive image data")

    @staticmethod
    def _reject_image_redirect(response, *args, **kwargs):
        # Requests can consume a redirect body to build Response.next even
        # with allow_redirects=False. Stop in the response hook, before that
        # unbounded read, and release the socket even though get() will raise.
        if 300 <= response.status_code < 400:
            response.close()
            raise requests.HTTPError("Image redirects are disabled.", response=response)
        return response

    def _download_image(self, url):
        if not isinstance(url, str) or not url.strip() or any(ord(char) < 32 or ord(char) == 127 for char in url):
            return self._skip_image("invalid image URL")
        url = url.strip()
        try:
            parsed = urlparse(url)
            if (parsed.scheme.lower() != "https" or not parsed.hostname or parsed.port == 0
                    or parsed.username is not None or parsed.password is not None):
                return self._skip_image("HTTPS URL without embedded credentials required")
        except ValueError:
            return self._skip_image("malformed image URL")
        if not self._safe_public_host(parsed.hostname):
            return self._skip_image("image host is non-public or cannot be resolved")
        try:
            with requests.get(
                url, timeout=min(max(self.config.blend_timeout, 1), 20),
                stream=True, allow_redirects=False,
                hooks={"response": self._reject_image_redirect},
            ) as response:
                # raise_for_status() does not reject 3xx. Never read or follow
                # redirect bodies, even if they claim to be images.
                if response.status_code != 200:
                    return self._skip_image("HTTP %s; direct HTTP 200 image required" % response.status_code)
                content_type = (response.headers.get("Content-Type") or "").lower()
                if not content_type.startswith("image/"):
                    return self._skip_image("non-image Content-Type")
                content_length = response.headers.get("Content-Length")
                if content_length is not None:
                    try:
                        length = int(content_length)
                    except ValueError:
                        return self._skip_image("invalid Content-Length")
                    if not 0 <= length <= self.MAX_IMAGE_BYTES:
                        return self._skip_image("Content-Length exceeds image limit or is invalid")
                chunks = []
                total = 0
                for chunk in response.iter_content(chunk_size=65536):
                    total += len(chunk)
                    if total > self.MAX_IMAGE_BYTES:
                        return self._skip_image("image stream exceeds 5 MB limit")
                    chunks.append(chunk)
                return self._validated_image(b"".join(chunks))
        except requests.RequestException as error:
            if error.response is not None:
                return self._skip_image("HTTP %s; direct HTTP 200 image required" % error.response.status_code)
            return self._skip_image("download failed (%s)" % type(error).__name__)

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
                "branch_id": self.config.blend_branch_id,
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
                [
                    ("blend_config_id", "=", self.config.id),
                    ("blend_external_id", "=", external_id),
                ],
                limit=1,
            )
            if not category:
                category = self.category_model.search(
                    [
                        ("blend_config_id", "=", False),
                        ("blend_external_id", "=", external_id),
                    ],
                    limit=1,
                )
            category_name = names["en"] or names["ar"] or "Blend Category %s" % external_id
            values = {
                "name": category_name,
                "sequence": sequence,
                "blend_config_id": self.config.id,
                "blend_external_id": external_id,
                "blend_menu_active": True,
                "blend_schedule_json": data.get("schedule_time") or {},
                "blend_item_order_json": [str(item_id) for item_id in data.get("items", [])],
                "blend_description_json": self._localized(data.get("description")),
            }
            if category:
                category.with_context(blend_inbound_sync=True).write(values)
            else:
                category = self.env["pos.category"].sudo().with_context(
                    blend_inbound_sync=True
                ).create(values)
            self._write_translations(category, "name", names)
            result |= category
        return result

    def _sync_items(self, items, category_records, categories):
        category_by_external = {
            category.blend_external_id: category for category in category_records
        }
        item_sequence = {}
        for category in categories:
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
            product = self.product_model.search(
                self._regular_product_domain(external_id),
                limit=1,
            )
            if not product:
                product = self.product_model.search(
                    self._legacy_regular_product_domain(external_id),
                    limit=1,
                )
            product_name = names["en"] or names["ar"] or "Blend Item %s" % external_id
            values = {
                "name": product_name,
                "list_price": price,
                "sale_ok": True,
                "available_in_pos": True,
                "active": True,
                "type": "consu",
                "company_id": self.config.company_id.id,
                "pos_sequence": item_sequence.get(external_id, 10),
                "use_as_modifier": False,
                "blend_config_id": self.config.id,
                "blend_external_id": external_id,
                "blend_is_available": available,
                "blend_menu_active": True,
                "blend_schedule_json": data.get("schedule_time") or {},
                "blend_image_urls_json": data.get("images") or [],
                "blend_unavailable_config_ids": [
                    Command.unlink(self.config.id) if available else Command.link(self.config.id)
                ],
            }
            if product:
                product.with_context(blend_inbound_sync=True).write(values)
            else:
                values["default_code"] = external_id
                product = self.env["product.template"].sudo().with_company(
                    self.config.company_id
                ).with_context(blend_inbound_sync=True).create(values)
            category_ids = [
                category_by_external[str(category_id)]
                for category_id in data.get("categories", [])
                if str(category_id) in category_by_external
            ]
            product.with_context(blend_inbound_sync=True).write(
                {
                    "pos_categ_ids": [
                        Command.set([category.id for category in category_ids])
                    ],
                }
            )
            self._write_translations(product, "name", names)
            self._write_translations(product, "public_description", descriptions)
            image_urls = data.get("images") or []
            if self.config.blend_sync_images and image_urls:
                image = (self._download_image(image_urls[0]) if isinstance(image_urls, list)
                         else self._skip_image("images must be a list of URLs"))
                if image:
                    product.with_context(blend_inbound_sync=True).image_1920 = image
            result |= product
        return result

    def _sync_toppings(self, toppings, product_records):
        product_by_external = {
            product.blend_external_id: product for product in product_records
        }
        group_result = self.group_model.browse()
        option_result = self.product_model.browse()
        groups_by_product = {
            external_id: self.env["modifiers.groups"].sudo()
            for external_id in product_by_external
        }

        for data in toppings:
            if not isinstance(data, dict):
                raise BlendAPIError("Each topping must be an object.")
            external_id = self._required_id(data.get("id"), "toppings[].id")
            names = self._localized(data.get("name"))
            is_required = self._boolean(data.get("is_required", False), "toppings[].is_required")
            try:
                minimum = int(data.get("min", 0))
                maximum = int(data.get("max", 1))
                max_per_quantity = int(data.get("max_per_quantity", maximum or 1))
            except (TypeError, ValueError):
                raise BlendAPIError("Topping min/max values must be integers.") from None
            if (
                minimum < 0
                or maximum < 1
                or maximum < minimum
                or max_per_quantity < 1
            ):
                raise BlendAPIError("Invalid topping selection limits.")
            group = self.group_model.search(
                [
                    ("blend_config_id", "=", self.config.id),
                    ("blend_external_id", "=", external_id),
                ],
                limit=1,
            )
            if not group:
                group = self.group_model.search(
                    [
                        ("blend_config_id", "=", False),
                        ("blend_external_id", "=", external_id),
                    ],
                    limit=1,
                )
            group_name = names["en"] or names["ar"] or "Blend Topping %s" % external_id
            item_external_ids = [str(item_id) for item_id in data.get("items", [])]
            group_values = {
                "name": group_name,
                "blend_config_id": self.config.id,
                "blend_external_id": external_id,
                "blend_is_required": is_required,
                "blend_min_selection": minimum,
                "blend_max_selection": maximum,
                "blend_max_per_quantity": max_per_quantity,
                "blend_item_external_ids_json": item_external_ids,
                "blend_menu_active": True,
                "blend_is_available": True,
            }
            if group:
                group.with_context(blend_inbound_sync=True).write(group_values)
            else:
                group = self.group_model.with_context(blend_inbound_sync=True).create(
                    group_values
                )
            self._write_translations(group, "name", names)
            group_result |= group

            option_templates = self.env["product.template"].sudo().with_company(
                self.config.company_id
            )
            for option in data.get("options", []):
                option_product = self._sync_option(option)
                option_templates |= option_product
                option_result |= option_product
            group.with_context(blend_inbound_sync=True).write(
                {"modifier_ids": [Command.set(option_templates.ids)]}
            )
            for item_external_id in item_external_ids:
                if item_external_id in groups_by_product:
                    groups_by_product[item_external_id] |= group

        all_blend_groups = self.group_model.search(
            [
                ("blend_config_id", "=", self.config.id),
                ("blend_external_id", "!=", False),
            ]
        )
        all_products = self.product_model.search(self._regular_product_domain())
        empty_groups = self.env["modifiers.groups"].sudo()
        for product in all_products:
            external_id = product.blend_external_id
            product = product.with_context(blend_inbound_sync=True)
            preserved = product.modifier_grp_ids - all_blend_groups
            selected_groups = groups_by_product.get(external_id, empty_groups)
            assigned_groups = preserved | selected_groups
            product.write(
                {
                    "modifier_grp_ids": [Command.set(assigned_groups.ids)],
                    "is_modifier": bool(assigned_groups),
                    "use_as_modifier": False,
                }
            )
            product.get_modifier()
            for group in group_result.filtered(
                lambda modifier_group: external_id
                in (modifier_group.blend_item_external_ids_json or [])
            ):
                option_templates = group.modifier_ids.filtered(
                    lambda option: option.blend_menu_active and option.blend_is_available
                )
                product.modifier_ids.filtered(
                    lambda line: line.name.product_tmpl_id in option_templates
                ).write(
                    {
                        "apply_selector": True,
                        "min_selector": group.blend_min_selection,
                        "max_selector": group.blend_max_selection,
                        "blend_group_external_id": group.blend_external_id,
                        "blend_is_required": group.blend_is_required,
                        "blend_min_selection": group.blend_min_selection,
                        "blend_max_selection": group.blend_max_selection,
                        "blend_max_per_quantity": group.blend_max_per_quantity,
                        "blend_group_available": group.blend_is_available,
                    }
                )
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
        product = self.product_model.search(
            [
                ("blend_config_id", "=", self.config.id),
                ("blend_external_id", "=", external_id),
                ("use_as_modifier", "=", True),
                "|",
                ("company_id", "=", False),
                ("company_id", "=", self.config.company_id.id),
            ],
            limit=1,
        )
        if not product:
            product = self.product_model.search(
                [
                    ("blend_config_id", "=", False),
                    ("blend_external_id", "=", external_id),
                    ("use_as_modifier", "=", True),
                    "|",
                    ("company_id", "=", False),
                    ("company_id", "=", self.config.company_id.id),
                ],
                limit=1,
            )
        product_name = names["en"] or names["ar"] or "Blend Option %s" % external_id
        values = {
            "name": product_name,
            "list_price": price,
            "sale_ok": True,
            "available_in_pos": True,
            "active": True,
            "type": "consu",
            "company_id": self.config.company_id.id,
            "is_modifier": False,
            "use_as_modifier": True,
            "blend_config_id": self.config.id,
            "blend_external_id": external_id,
            "blend_is_available": True,
            "blend_menu_active": True,
            "blend_unavailable_config_ids": [Command.unlink(self.config.id)],
        }
        if product:
            product.with_context(blend_inbound_sync=True).write(values)
        else:
            values["default_code"] = external_id
            product = self.env["product.template"].sudo().with_company(
                self.config.company_id
            ).with_context(blend_inbound_sync=True).create(values)
        self._write_translations(product, "name", names)
        return product

    def _regular_product_domain(self, external_id=None):
        domain = [
            ("blend_config_id", "=", self.config.id),
            ("blend_external_id", "!=", False),
            ("use_as_modifier", "=", False),
            "|",
            ("company_id", "=", False),
            ("company_id", "=", self.config.company_id.id),
        ]
        if external_id is not None:
            domain[1] = ("blend_external_id", "=", external_id)
        return domain

    def _legacy_regular_product_domain(self, external_id):
        return [
            ("blend_config_id", "=", False),
            ("blend_external_id", "=", external_id),
            ("use_as_modifier", "=", False),
            "|",
            ("company_id", "=", False),
            ("company_id", "=", self.config.company_id.id),
        ]

    def _deactivate_missing(self, categories, products, groups, options):
        missing_categories = self.category_model.search(
            [
                ("blend_config_id", "=", self.config.id),
                ("blend_external_id", "!=", False),
                ("id", "not in", categories.ids),
            ]
        )
        missing_categories.with_context(blend_inbound_sync=True).write(
            {"blend_menu_active": False}
        )

        missing_products = self.product_model.search(
            self._regular_product_domain() + [("id", "not in", products.ids)]
        )
        missing_products.with_context(blend_inbound_sync=True).write(
            {
                "blend_menu_active": False,
                "blend_is_available": False,
                "blend_unavailable_config_ids": [Command.link(self.config.id)],
            }
        )

        missing_groups = self.group_model.search(
            [
                ("blend_config_id", "=", self.config.id),
                ("blend_external_id", "!=", False),
                ("id", "not in", groups.ids),
            ]
        )
        missing_groups.with_context(blend_inbound_sync=True).write(
            {"blend_menu_active": False, "blend_is_available": False}
        )

        missing_options = self.product_model.search(
            [
                ("blend_config_id", "=", self.config.id),
                ("blend_external_id", "!=", False),
                ("use_as_modifier", "=", True),
                ("id", "not in", options.ids),
                "|",
                ("company_id", "=", False),
                ("company_id", "=", self.config.company_id.id),
            ]
        )
        missing_options.with_context(blend_inbound_sync=True).write(
            {
                "blend_menu_active": False,
                "blend_is_available": False,
                "blend_unavailable_config_ids": [Command.link(self.config.id)],
            }
        )

    def set_item_availability(self, item_id, is_available, *, by_sku=False):
        external_id = self._required_id(item_id, "item_id")
        product = self.product_model.search(self._regular_product_domain(external_id), limit=1)
        if not product and by_sku:
            product = self.env["product.template"].sudo().with_company(
                self.config.company_id
            ).search(
                [
                    ("default_code", "=", external_id),
                    ("blend_config_id", "=", False),
                    "|",
                    ("company_id", "=", False),
                    ("company_id", "=", self.config.company_id.id),
                ],
                limit=1,
            )
        if not product:
            raise BlendAPIError("Blend item '%s' was not found in Odoo products." % external_id, 404)
        available = self._boolean(is_available, "is_available")
        values = {
            "blend_unavailable_config_ids": [
                Command.unlink(self.config.id) if available else Command.link(self.config.id)
            ]
        }
        if product.blend_config_id == self.config:
            values.update({"blend_is_available": available, "blend_menu_active": True})
        product.with_context(blend_inbound_sync=True).write(values)
        self.config._blend_notify(
            {
                "kind": "item",
                "branch_id": self.config.blend_branch_id,
                "external_id": external_id,
                "product_tmpl_id": product.id,
                "available": available,
            }
        )
        return {"item_id": external_id, "is_available": available}

    def set_modifier_availability(self, modifier_id, is_available):
        external_id = self._required_id(modifier_id, "modifier_id")
        product = self.product_model.search(
            [
                ("blend_config_id", "=", self.config.id),
                ("blend_external_id", "=", external_id),
                ("use_as_modifier", "=", True),
                "|",
                ("company_id", "=", False),
                ("company_id", "=", self.config.company_id.id),
            ],
            limit=1,
        )
        available = self._boolean(is_available, "is_available")
        if product:
            product.with_context(blend_inbound_sync=True).write(
                {
                    "blend_is_available": available,
                    "blend_menu_active": True,
                    "blend_unavailable_config_ids": [
                        Command.unlink(self.config.id)
                        if available
                        else Command.link(self.config.id)
                    ],
                }
            )
            self.config._blend_notify(
                {
                    "kind": "modifier",
                    "branch_id": self.config.blend_branch_id,
                    "external_id": external_id,
                    "product_tmpl_id": product.id,
                    "available": available,
                }
            )
            return {"modifier_id": external_id, "is_available": available}

        group = self.group_model.search(
            [
                ("blend_config_id", "=", self.config.id),
                ("blend_external_id", "=", external_id),
            ],
            limit=1,
        )
        if not group:
            raise BlendAPIError(
                "Blend modifier or modifier group '%s' was not found in Odoo." % external_id,
                404,
            )
        group.with_context(blend_inbound_sync=True).write(
            {"blend_is_available": available, "blend_menu_active": True}
        )
        lines = self.env["product.modifiers"].sudo().search(
            [
                ("product_id.blend_config_id", "=", self.config.id),
                ("blend_group_external_id", "=", external_id),
            ]
        )
        lines.write({"blend_group_available": available})
        self.config._blend_notify(
            {
                "kind": "modifier_group",
                "branch_id": self.config.blend_branch_id,
                "external_id": external_id,
                "available": available,
            }
        )
        return {
            "modifier_id": external_id,
            "modifier_type": "group",
            "is_available": available,
        }

    def set_branch_availability(self, is_available):
        available = self._boolean(is_available, "is_available")
        self.config.write({"blend_available": available})
        self.config._blend_notify(
            {
                "kind": "branch",
                "branch_id": self.config.blend_branch_id,
                "available": available,
            }
        )
        return {"branch_id": self.config.blend_branch_id, "is_available": available}
