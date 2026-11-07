import math
import time
import uuid
from datetime import datetime

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tools.mail import html2plaintext

from .exceptions import BlendAPIError
from .stock_availability import BlendStockAvailabilityService


class BlendPOSAPIService:
    """Implementation of the Manual POS endpoints published by Blend."""

    ALLOWED_ORDER_STATUSES = {
        "PENDING": "pending",
        "SCHEDULED": "scheduled",
        "ACCEPTED": "accepted",
        "PREPARING": "preparing",
        "READY_TO_PICK_UP": "ready_to_pick_up",
        "ASSIGNED": "assigned",
        "AT_PICK_UP": "at_pick_up",
        "ON_THE_WAY": "on_the_way",
        "COMPLETED": "completed",
        "CANCELED": "cancelled",
        "REJECTED": "rejected",
        "TIME_OUT": "time_out",
    }
    TERMINAL_ORDER_STATUSES = {"CANCELED", "REJECTED", "TIME_OUT"}

    def __init__(self, env, configs, request_headers=None, request_id=None):
        self.env = env
        self.configs = configs.sudo().sorted("id")
        self.request_headers = request_headers or {}
        self.request_id = request_id or uuid.uuid4().hex
        self.log_model = env["blend.integration.log"].sudo()
        self._stock_availability = {}

    @staticmethod
    def _required(payload, field_name):
        value = payload.get(field_name)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise BlendAPIError("%s is required." % field_name)
        return value

    @staticmethod
    def _text(value):
        return str(value or "").strip()

    @classmethod
    def _required_text(cls, payload, field_name):
        value = cls._required(payload, field_name)
        if not isinstance(value, str):
            raise BlendAPIError("%s must be a string." % field_name)
        return value.strip()

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
        return BlendPOSAPIService._canonical_id(
            getattr(record, "blend_external_id", False)
            or record.id
        )

    @staticmethod
    def _numeric_api_id(value, field_name):
        text = str(value or "").strip()
        if not text.isdecimal():
            raise BlendAPIError("%s must be a numeric ID." % field_name, 422)
        return int(text)

    @staticmethod
    def _localized(record, field_name):
        def read(language):
            value = record.with_context(lang=language)[field_name] or ""
            if field_name == "public_description":
                value = html2plaintext(str(value))
            return str(value).strip()

        english = read("en_US")
        arabic = read("ar_001")
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

    def _start_log(self, config, event, payload, *, stable_key=None, external_reference=None):
        key = stable_key or "pos_api:%s:%s:%s" % (config.id, event, self.request_id)
        self.log_model.lock_idempotency_key(key)
        log = self.log_model.search(
            [
                ("company_id", "=", config.company_id.id),
                ("direction", "=", "inbound"),
                ("idempotency_key", "=", key),
            ],
            limit=1,
        )
        if log and log.state == "success":
            return log, True, time.perf_counter()
        if not log:
            log = self.log_model.create(
                {
                    "name": "%s - %s" % (event, (external_reference or key)[-32:]),
                    "direction": "inbound",
                    "event": event,
                    "event_time": fields.Datetime.now(),
                    "state": "received",
                    "company_id": config.company_id.id,
                    "config_id": config.id,
                    "external_reference": external_reference,
                    "idempotency_key": key,
                    "request_payload": payload or {},
                    "request_headers": self.request_headers,
                }
            )
        started_at = fields.Datetime.now()
        log.write(
            {
                "state": "processing",
                "started_at": started_at,
                "last_attempt_at": started_at,
                "attempt_count": log.attempt_count + 1,
                "error_message": False,
            }
        )
        return log, False, time.perf_counter()

    @staticmethod
    def _finish_log(log, response, started_clock, order=None):
        values = {
            "state": "success",
            "response_payload": response,
            "http_status": 200,
            "completed_at": fields.Datetime.now(),
            "duration_ms": (time.perf_counter() - started_clock) * 1000,
        }
        if order:
            values["order_id"] = order.id
        log.write(values)

    @staticmethod
    def _fail_log(log, error, started_clock):
        status_code = getattr(error, "status_code", 500)
        message = getattr(error, "message", str(error))
        log.write(
            {
                "state": "failed",
                "response_payload": {"success": False, "message": message},
                "http_status": status_code,
                "completed_at": fields.Datetime.now(),
                "duration_ms": (time.perf_counter() - started_clock) * 1000,
                "error_message": message[:4000],
            }
        )

    def get_branches(self):
        config = self.configs[:1]
        log, cached, started = self._start_log(config, "pos.branches.get", {})
        if cached:
            return log.response_payload
        try:
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
            response = {"branches": branches}
            self._finish_log(log, response, started)
            return response
        except Exception as error:
            self._fail_log(log, error, started)
            raise

    def _products_for_config(self, config):
        return self.env["product.template"]._blend_menu_products(config)

    def _product_available(self, product, config):
        if not product or not product.active or not product.available_in_pos:
            return False
        schedule_model = self.env["product.template"].sudo()
        if not config.blend_available or not schedule_model._blend_schedule_visible(
            config.blend_schedule_json, config
        ):
            return False
        stock = self._stock_availability.setdefault(config.id, {})
        if product.id not in stock:
            stock.update(BlendStockAvailabilityService(self.env).stock_available(product, config))
        if not stock[product.id]:
            return False
        if product.blend_external_id:
            return product._blend_is_visible_now(config)
        return config not in product.blend_unavailable_config_ids

    def _product_image_url(self, product):
        """Build the public image route without loading the binary attachment.

        Reading ``image_128`` while serializing a menu forces Odoo to open the
        filestore.  A stale attachment must not make the menu endpoint slow or
        noisy, so only attachment metadata is inspected here.
        """
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

    def _modifier_groups(self, product, config, modifier_group_filter=None):
        groups = product.modifier_grp_ids.filtered(
            lambda group: not group.blend_config_id or group.blend_config_id == config
        ).sorted("id")
        result = []
        for group in groups:
            group_external_id = self._record_external_id(group)
            if modifier_group_filter and group_external_id != modifier_group_filter:
                continue
            group_id = self._numeric_api_id(
                group_external_id, "modifier_groups[].id"
            )
            configured_minimum = (
                group.blend_min_selection if group.blend_external_id else 0
            )
            configured_maximum = (
                group.blend_max_selection if group.blend_external_id else 1
            )
            minimum = max(int(configured_minimum or 0), 0)
            maximum = max(int(configured_maximum or 1), minimum, 1)
            required = (
                group.blend_is_required if group.blend_external_id else bool(minimum)
            )
            modifiers = []
            for option in group.modifier_ids.sorted("id"):
                option_id = self._numeric_api_id(
                    self._record_external_id(option), "modifiers[].id"
                )
                modifiers.append(
                    {
                        "id": option_id,
                        "status": bool(
                            group.blend_menu_active
                            and group.blend_is_available
                            and self._product_available(option, config)
                        ),
                        "name": self._localized(option, "name"),
                        "price": option.list_price,
                    }
                )
            if not any(modifier["status"] for modifier in modifiers):
                continue
            result.append(
                {
                    "id": group_id,
                    "name": self._localized(group, "name"),
                    "multiple": bool(maximum != 1),
                    "required": bool(required),
                    "min_selection": minimum,
                    "max_selection": maximum,
                    "modifiers": modifiers,
                }
            )
        return result

    def get_menu(self, *, page=1, per=25, filters=None, path=""):
        filters = filters or {}
        product_filter = self._canonical_id(filters.get("product_id"))
        category_filter = self._text(filters.get("category_id"))
        modifier_group_filter = self._text(filters.get("modifier_group_id"))
        config = self.configs[:1]
        request_payload = {"page": page, "per": per, "filter": filters}
        log, cached, started = self._start_log(config, "pos.menu.get", request_payload)
        if cached:
            return log.response_payload
        try:
            products_by_config = {
                branch.id: self._products_for_config(branch) for branch in self.configs
            }
            stock_service = BlendStockAvailabilityService(self.env)
            for branch in self.configs:
                products = products_by_config[branch.id]
                self._stock_availability[branch.id] = stock_service.stock_available(
                    products | products.modifier_grp_ids.modifier_ids, branch,
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
                        product, branch, modifier_group_filter or None
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
                    product_data = {
                        "id": self._numeric_api_id(product_id, "products[].id"),
                        "name": self._localized(product, "name"),
                        "description": self._localized(product, "public_description"),
                        "stock": any(item["is_stock"] for item in availability),
                        "status": bool(product.active and product.available_in_pos),
                        "price": product.list_price,
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
            total = len(ordered)
            start = (page - 1) * per
            selected = ordered[start : start + per]
            category_payload = []
            for category in selected:
                category_payload.append(
                    {
                        "id": category["id"],
                        "name": category["name"],
                        "products": list(category["products"].values()),
                    }
                )
            response = {
                "categories": category_payload,
                "pagination": {
                    "from": start + 1 if selected else 0,
                    "to": start + len(selected),
                    "per": per,
                    "total": total,
                    "current": page,
                    "path": path,
                },
            }
            self._finish_log(log, response, started)
            return response
        except Exception as error:
            self._fail_log(log, error, started)
            raise

    def _find_product(self, config, external_id, *, modifier=False):
        value = self._canonical_id(external_id)
        menu = self._products_for_config(config)
        if modifier:
            groups = menu.modifier_grp_ids.filtered(lambda group: not group.blend_config_id or group.blend_config_id == config)
            candidates = groups.modifier_ids.filtered(lambda product:
                (not product.blend_config_id or product.blend_config_id == config)
                and (not product.company_id or product.company_id == config.company_id))
        else:
            candidates = menu
        mapping = self._product_identity_map(candidates)
        if value in mapping:
            return mapping[value]
        if config.blend_finalize_on_accept:
            # Financial imports must never fall back to an arbitrary SKU/DB ID
            # outside the menu (or modifiers) exposed for this branch.
            return self.env["product.template"]
        model = self.env["product.template"].sudo().with_company(
            config.company_id
        ).with_context(active_test=False)
        domain = [
            ("use_as_modifier", "=", modifier),
            "|",
            ("company_id", "=", False),
            ("company_id", "=", config.company_id.id),
            "|",
            "&",
            ("blend_config_id", "=", config.id),
            ("blend_external_id", "=", value),
            "&",
            ("blend_config_id", "=", False),
            ("default_code", "=", value),
        ]
        product = model.search(domain, limit=2)
        if len(product) > 1:
            raise BlendAPIError("Ambiguous Blend product/SKU '%s'." % value, 422)
        if not product and value.isdigit():
            candidate = model.browse(int(value)).exists()
            if (
                candidate
                and candidate.use_as_modifier == modifier
                and (not candidate.blend_config_id or candidate.blend_config_id == config)
                and candidate.company_id in (self.env["res.company"], config.company_id)
            ):
                product = candidate
        return product

    def _partner(self, config, customer):
        if customer in (None, False):
            return self.env["res.partner"]
        if not isinstance(customer, dict):
            raise BlendAPIError("customer must be an object when provided.")
        name = self._text(customer.get("name"))
        phone = self._text(customer.get("phone"))
        if not name and not phone:
            return self.env["res.partner"]
        partner_model = self.env["res.partner"].sudo().with_company(config.company_id)
        partner = partner_model.browse()
        if phone:
            contact_fields = ["phone"]
            if "mobile" in partner_model._fields:
                contact_fields.append("mobile")
            contact_domain = [(contact_fields[0], "=", phone)]
            for field_name in contact_fields[1:]:
                contact_domain = ["|", (field_name, "=", phone), *contact_domain]
            partner = partner_model.search(
                [
                    "|",
                    ("company_id", "=", False),
                    ("company_id", "=", config.company_id.id),
                    *contact_domain,
                ],
                limit=1,
            )
        if not partner:
            partner = partner_model.create(
                {
                    "name": name or phone,
                    "phone": phone or False,
                    "company_id": config.company_id.id,
                }
            )
        elif name and partner.name != name:
            partner.write({"name": name})
        return partner

    def _validate_status(self, value):
        status = self._text(value).upper()
        if status not in self.ALLOWED_ORDER_STATUSES:
            raise BlendAPIError(
                "status must be one of PENDING, SCHEDULED, ACCEPTED, PREPARING, READY_TO_PICK_UP, "
                "ASSIGNED, AT_PICK_UP, ON_THE_WAY, COMPLETED, CANCELED, REJECTED, "
                "or TIME_OUT."
            )
        return status, self.ALLOWED_ORDER_STATUSES[status]

    @staticmethod
    def _validate_schedule_at(value):
        if not isinstance(value, str) or len(value) != 19:
            raise BlendAPIError("SCHEDULED orders require schedule_at in YYYY-MM-DD HH:MM:SS format.", 422)
        try:
            datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except ValueError as error:
            raise BlendAPIError("SCHEDULED orders require a valid schedule_at date and time.", 422) from error
        return value

    @staticmethod
    def _scheduled_note(note, schedule_at):
        heading = "⏰ Scheduled for: %s" % schedule_at
        return note if schedule_at in note else "%s\n%s" % (heading, note) if note else heading

    def _validate_modifiers(self, product, config, modifiers):
        if modifiers is None:
            modifiers = []
        if not isinstance(modifiers, list):
            raise BlendAPIError("products[].modifiers must be an array.")
        groups = product.modifier_grp_ids.filtered(
            lambda group: (not group.blend_config_id or group.blend_config_id == config)
            and group.blend_menu_active
            and group.blend_is_available
        )
        option_groups = {}
        for group in groups:
            for option in group.modifier_ids:
                option_groups.setdefault(self._record_external_id(option), []).append(group)
        prepared = []
        counts = {group.id: 0 for group in groups}
        for data in modifiers:
            if not isinstance(data, dict):
                raise BlendAPIError("Each modifier must be an object.")
            option_id = self._text(self._required(data, "id"))
            option = self._find_product(config, option_id, modifier=True)
            if not option:
                raise BlendAPIError("Modifier '%s' was not found." % option_id, 404)
            matching_groups = option_groups.get(option_id, [])
            if len(matching_groups) != 1:
                raise BlendAPIError(
                    "Modifier '%s' is not uniquely assigned to this product." % option_id
                )
            group = matching_groups[0]
            quantity = self._quantity(data.get("quantity"), "modifiers[].quantity")
            maximum_per_option = group.blend_max_per_quantity or quantity
            if quantity > maximum_per_option:
                raise BlendAPIError(
                    "Modifier '%s' exceeds max_per_quantity=%s."
                    % (option_id, maximum_per_option)
                )
            if not self._product_available(option, config):
                raise BlendAPIError("Modifier '%s' is unavailable." % option_id, 409)
            total = self._number(data.get("total"), "modifiers[].total", minimum=0)
            counts[group.id] += quantity
            prepared.append(
                {
                    "data": data,
                    "product": option,
                    "group": group,
                    "quantity": quantity,
                    "total": total,
                }
            )
        for group in groups:
            minimum = group.blend_min_selection if group.blend_external_id else 0
            maximum = group.blend_max_selection if group.blend_external_id else 1
            selected = counts[group.id]
            if selected < minimum or (maximum > 0 and selected > maximum):
                raise BlendAPIError(
                    "Modifier group '%s' requires between %s and %s selections."
                    % (self._record_external_id(group), minimum, maximum)
                )
        return prepared

    def create_order(self, payload, *, recover_fulfilled=False):
        if not isinstance(payload, dict):
            raise BlendAPIError("The request body must be a JSON object.")
        branch_id = self._required(payload, "branch_id")
        brand_id = self._required(payload, "brand_id")
        config = self._resolve_config(branch_id, brand_id)
        partner_reference = self._required_text(payload, "partner_reference")
        order_type = self._required_text(payload, "order_type")
        aggregator = self._required_text(payload, "aggregator")
        aggregator_order_id = self._required_text(payload, "aggregator_order_id")
        stable_key = "pos_order_create:%s:%s" % (config.id, partner_reference)
        log, cached, started = self._start_log(
            config,
            "pos.order.create",
            payload,
            stable_key=stable_key,
            external_reference=partner_reference,
        )
        if cached:
            return log.response_payload
        try:
            existing = self.env["pos.order"].sudo().search(
                [
                    ("config_id", "=", config.id),
                    ("blend_partner_reference", "=", partner_reference),
                ],
                limit=1,
            )
            if existing:
                response = {
                    "success": True,
                    "message": "Order created successfully",
                    "order_id": existing.uuid,
                }
                self._finish_log(log, response, started, existing)
                return response

            if error := config._blend_order_preset_error(order_type):
                raise BlendAPIError(error, 409)

            session = self.env["pos.session"].sudo().search(
                [("config_id", "=", config.id), ("state", "=", "opened")],
                order="id desc",
                limit=1,
            )
            if not session:
                raise BlendAPIError("No open POS session is available for this branch.", 409)
            schedule_model = self.env["product.template"].sudo()
            if not config.blend_available or not schedule_model._blend_schedule_visible(
                config.blend_schedule_json, config
            ):
                raise BlendAPIError("This Blend POS branch is currently unavailable.", 409)

            status, normalized_status = self._validate_status(
                self._required(payload, "status")
            )
            schedule_at = self._validate_schedule_at(payload.get("schedule_at")) if status == "SCHEDULED" else False
            total = self._number(self._required(payload, "total"), "total", minimum=0)
            subtotal = self._number(
                self._required(payload, "subtotal"), "subtotal", minimum=0
            )
            tax = self._number(self._required(payload, "tax"), "tax", minimum=0)
            discount = config.currency_id.round(subtotal + tax - total)
            if discount < 0:
                raise BlendAPIError(
                    "Invalid totals: total cannot exceed subtotal + tax.", 422
                )
            if discount > config.currency_id.round(subtotal):
                raise BlendAPIError(
                    "Invalid totals: the implied discount cannot exceed subtotal.",
                    422,
                )
            net_subtotal = config.currency_id.round(subtotal - discount)
            rounding = max(config.currency_id.rounding, 0.01)
            products = self._required(payload, "products")
            if not isinstance(products, list) or not products:
                raise BlendAPIError("products must be a non-empty array.")

            prepared_products = []
            products_total = 0.0
            for data in products:
                if not isinstance(data, dict):
                    raise BlendAPIError("Each product must be an object.")
                product_id = self._text(self._required(data, "id"))
                product = self._find_product(config, product_id, modifier=False)
                if not product:
                    raise BlendAPIError("Product '%s' was not found." % product_id, 404)
                if not self._product_available(product, config):
                    raise BlendAPIError("Product '%s' is unavailable." % product_id, 409)
                variant = product.product_variant_id
                if not variant:
                    raise BlendAPIError(
                        "Product '%s' must resolve to exactly one POS variant."
                        % product_id,
                        422,
                    )
                quantity = self._quantity(data.get("quantity"), "products[].quantity")
                line_total = self._number(data.get("total"), "products[].total", minimum=0)
                modifiers = self._validate_modifiers(
                    product, config, data.get("modifiers", [])
                )
                for modifier in modifiers:
                    modifier_variant = modifier["product"].product_variant_id
                    if not modifier_variant:
                        raise BlendAPIError(
                            "Modifier '%s' must resolve to exactly one POS variant."
                            % self._record_external_id(modifier["product"]),
                            422,
                        )
                    modifier["variant"] = modifier_variant
                modifier_total = sum(item["total"] * quantity for item in modifiers)
                # Product totals are extended; modifier totals are per parent item.
                products_total += line_total + modifier_total
                prepared_products.append(
                    {
                        "data": data,
                        "product": product,
                        "variant": variant,
                        "quantity": quantity,
                        "total": line_total,
                        "base_total": line_total,
                        "modifiers": modifiers,
                    }
                )
            # Existing payloads contain discounted gross lines. Also accept the
            # explicit header-discount shape; unexplained differences still fail.
            add_discount_line = (
                not config.currency_id.is_zero(discount)
                and config.currency_id.is_zero(products_total - subtotal - tax)
                and not config.currency_id.is_zero(products_total - total)
            )
            if abs(products_total - total) > rounding and not add_discount_line:
                raise BlendAPIError(
                    "The sum of products[].total and modifiers[].total multiplied by parent quantity "
                    "must equal total, or subtotal + tax when a header discount is provided."
                )

            with self.env.cr.savepoint():
                if config.blend_finalize_on_accept:
                    try:
                        session._blend_lock_accounting()
                    except UserError as error:
                        raise BlendAPIError(str(error), 409) from error
                partner = self._partner(config, payload.get("customer"))
                order = (
                    self.env["pos.order"]
                    .sudo()
                    .with_company(config.company_id)
                    .create(
                        {
                            "session_id": session.id,
                            "config_id": config.id,
                            "preset_id": config._blend_order_preset(order_type).id,
                            "partner_id": partner.id or False,
                            "floating_order_name": partner_reference,
                            "general_customer_note": self._scheduled_note(self._text(payload.get("note")), schedule_at) if schedule_at else self._text(payload.get("note")),
                            "blend_schedule_at": schedule_at,
                            "amount_tax": tax,
                            "amount_total": total,
                            "amount_paid": 0.0,
                            "amount_return": 0.0,
                            "blend_partner_reference": partner_reference,
                            "blend_order_type": order_type,
                            "blend_aggregator": aggregator,
                            "blend_aggregator_order_id": aggregator_order_id,
                            "blend_discount_amount": discount,
                            "blend_payload": payload,
                            "blend_recovered_fulfilled": recover_fulfilled,
                            "blend_status": normalized_status,
                            "blend_financial_required": bool(config.blend_finalize_on_accept),
                            "blend_financial_state": "pending" if config.blend_finalize_on_accept else False,
                        }
                    )
                )
                tax_ratio = (subtotal / products_total if products_total else 1.0) if add_discount_line else (
                    net_subtotal / total if total else 1.0
                )
                line_model = (
                    self.env["pos.order.line"]
                    .sudo()
                    .with_company(config.company_id)
                )
                for item in prepared_products:
                    base_total = item["base_total"]
                    quantity = item["quantity"]
                    parent_line = line_model.create(
                        {
                            "order_id": order.id,
                            "product_id": item["variant"].id,
                            "qty": quantity,
                            "price_unit": base_total / quantity,
                            "price_subtotal": config.currency_id.round(
                                base_total * tax_ratio
                            ),
                            "price_subtotal_incl": base_total,
                            "customer_note": self._text(item["data"].get("note")),
                            "tax_ids": [Command.clear()],
                            "price_type": "manual",
                            "blend_gross_total": base_total,
                        }
                    )
                    for modifier in item["modifiers"]:
                        modifier_total = modifier["total"] * quantity
                        modifier_quantity = modifier["quantity"] * quantity
                        line_model.create(
                            {
                                "order_id": order.id,
                                "product_id": modifier["variant"].id,
                                "qty": modifier_quantity,
                                "price_unit": modifier_total / modifier_quantity,
                                "price_subtotal": config.currency_id.round(
                                    modifier_total * tax_ratio
                                ),
                                "price_subtotal_incl": modifier_total,
                                "customer_note": self._text(
                                    modifier["data"].get("note")
                                ),
                                "tax_ids": [Command.clear()],
                                "price_type": "manual",
                                "parent_line_id": str(parent_line.id),
                                "modifier_selection_qty": modifier["quantity"],
                                "blend_gross_total": modifier_total,
                            }
                        )
                if add_discount_line:
                    order._blend_add_import_discount_line(discount)
                order._blend_prepare_imported_accounting()
                if status in self.TERMINAL_ORDER_STATUSES:
                    order.with_context(
                        blend_skip_status_sync=True
                    ).action_pos_order_cancel()
                order._blend_auto_accept()
                if not recover_fulfilled:
                    config.notify_synchronisation(session.id, 0)
                    config._blend_notify(
                        {
                            "kind": "order_created",
                            "branch_id": config.blend_branch_id,
                            "order_id": order.uuid,
                            "partner_reference": partner_reference,
                            "pos_order_id": order.id,
                        }
                    )
                response = {
                    "success": True,
                    "message": "Order created successfully",
                    "order_id": order.uuid,
                }
                self._finish_log(log, response, started, order)
            return response
        except Exception as error:
            self._fail_log(log, error, started)
            raise

    def update_order(self, payload):
        if not isinstance(payload, dict):
            raise BlendAPIError("The request body must be a JSON object.")
        config = self._resolve_config(
            self._required(payload, "branch_id"), self._required(payload, "brand_id")
        )
        order_reference = self._text(self._required(payload, "order_id"))
        status, normalized_status = self._validate_status(
            self._required(payload, "status")
        )
        stable_key = self.log_model.make_idempotency_key(
            payload, prefix="pos_order_update:%s" % config.id
        )
        log, cached, started = self._start_log(
            config,
            "pos.order.update",
            payload,
            stable_key=stable_key,
            external_reference=order_reference,
        )
        if cached:
            return log.response_payload
        try:
            order = self.env["pos.order"].sudo().with_company(config.company_id).search(
                [
                    ("config_id", "=", config.id),
                    ("company_id", "=", config.company_id.id),
                    ("blend_partner_reference", "!=", False),
                    "|",
                    ("uuid", "=", order_reference),
                    "|",
                    ("blend_partner_reference", "=", order_reference),
                    "|",
                    ("pos_reference", "=", order_reference),
                    ("name", "=", order_reference),
                ],
                limit=1,
            )
            if not order:
                raise BlendAPIError("Blend order '%s' was not found." % order_reference, 404)
            with self.env.cr.savepoint():
                order.flush_recordset()
                order.lock_for_update()
                order.invalidate_recordset()
                if (
                    order.state == "cancel"
                    and status not in self.TERMINAL_ORDER_STATUSES
                ):
                    raise BlendAPIError(
                        "The POS order is already finalized as cancelled.", 409
                    )
                values = {"blend_status": normalized_status}
                if status == "SCHEDULED":
                    schedule_at = self._validate_schedule_at(payload.get("schedule_at") or order.blend_schedule_at)
                    values["blend_schedule_at"] = schedule_at
                    if "note" in payload:
                        values["general_customer_note"] = self._scheduled_note(self._text(payload["note"]), schedule_at)
                    elif not order.general_customer_note:
                        values["general_customer_note"] = self._scheduled_note("", schedule_at)
                order.with_context(blend_skip_status_sync=True).write(values)
                if status in self.TERMINAL_ORDER_STATUSES and order.state == "draft":
                    order.with_context(
                        blend_skip_status_sync=True
                    ).action_pos_order_cancel()
                if status in self.TERMINAL_ORDER_STATUSES:
                    order._blend_mark_refund_review()
                if status == "SCHEDULED":
                    order._blend_auto_accept()
                order.message_post(
                    body="Blend POS status updated to <b>%s</b>." % status
                )
                config.notify_synchronisation(order.session_id.id, 0)
                config._blend_notify(
                    {
                        "kind": "order",
                        "branch_id": config.blend_branch_id,
                        "order_id": order.uuid,
                        "status": normalized_status,
                        "pos_order_id": order.id,
                    }
                )
                response = {
                    "success": True,
                    "message": "Order updated successfully",
                    "order_id": order.uuid,
                    "status": status,
                }
                self._finish_log(log, response, started, order)
            return response
        except Exception as error:
            self._fail_log(log, error, started)
            raise
