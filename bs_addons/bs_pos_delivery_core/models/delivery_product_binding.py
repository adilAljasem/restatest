from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from ..exceptions import DeliveryBusinessError


class DeliveryProductBinding(models.Model):
    """Connection-scoped product identifier used by provider adapters."""

    _name = "bs.delivery.product.binding"
    _description = "POS Delivery Product Binding"
    _order = "connection_id, external_product_id, id"
    _check_company_auto = True

    connection_id = fields.Many2one(
        "bs.delivery.connection",
        required=True,
        index=True,
        ondelete="cascade",
        check_company=True,
    )
    store_binding_id = fields.Many2one(
        "bs.delivery.store.binding",
        string="Delivery Store",
        index=True,
        ondelete="cascade",
        check_company=True,
        help="Optional store-specific override; leave empty for the connection default.",
    )
    provider_id = fields.Many2one(
        related="connection_id.provider_id",
        store=True,
        index=True,
        readonly=True,
    )
    company_id = fields.Many2one(
        related="connection_id.company_id",
        store=True,
        index=True,
        readonly=True,
    )
    product_id = fields.Many2one(
        "product.product",
        required=True,
        index=True,
        ondelete="restrict",
        check_company=True,
    )
    external_product_id = fields.Char(required=True, index=True)
    external_variant_id = fields.Char(index=True)
    external_name = fields.Char(
        string="Platform Name",
        help="Compatibility display name; English name is preferred when available.",
    )
    external_name_en = fields.Char(string="Platform Name (English)")
    external_name_ar = fields.Char(string="Platform Name (Arabic)")
    platform_price = fields.Monetary(
        string="Platform Price",
        currency_field="currency_id",
        default=0.0,
    )
    currency_id = fields.Many2one(
        "res.currency",
        related="company_id.currency_id",
        store=True,
        readonly=True,
    )
    last_sync_date = fields.Datetime(readonly=True, copy=False)
    active = fields.Boolean(default=True)

    _external_product_global_unique = models.UniqueIndex(
        "(connection_id, external_product_id) WHERE store_binding_id IS NULL",
        "A global external product can only be mapped once per delivery connection.",
    )
    _external_product_store_unique = models.UniqueIndex(
        "(connection_id, store_binding_id, external_product_id) "
        "WHERE store_binding_id IS NOT NULL",
        "An external product can only be mapped once per delivery store.",
    )
    _platform_price_non_negative = models.Constraint(
        "CHECK(platform_price >= 0)",
        "The platform price cannot be negative.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        return super().create([self._normalize_values(values) for values in vals_list])

    def write(self, vals):
        return super().write(self._normalize_values(vals))

    @api.model
    def _normalize_values(self, values):
        values = dict(values)
        for field_name in ("external_product_id", "external_variant_id"):
            if field_name in values:
                values[field_name] = str(values[field_name] or "").strip() or False
        for field_name in ("external_name", "external_name_en", "external_name_ar"):
            if field_name in values:
                values[field_name] = str(values[field_name] or "").strip() or False
        if values.get("external_name_en") and not values.get("external_name"):
            values["external_name"] = values["external_name_en"]
        elif values.get("external_name") and "external_name_en" not in values:
            values["external_name_en"] = values["external_name"]
        return values

    @api.constrains("external_product_id")
    def _check_external_product_id(self):
        for binding in self:
            if not binding.external_product_id:
                raise ValidationError(_("External Product ID cannot be empty."))

    @api.constrains("connection_id", "store_binding_id")
    def _check_store_connection(self):
        for binding in self:
            if (
                binding.store_binding_id
                and binding.store_binding_id.connection_id != binding.connection_id
            ):
                raise ValidationError(
                    _("The product mapping store belongs to another connection.")
                )

    @api.model
    def resolve_product(self, connection, external_ids, store_binding=None):
        connection.ensure_one()
        normalized = []
        for external_id in external_ids or []:
            value = str(external_id or "").strip()
            if value and value not in normalized:
                normalized.append(value)
        if not normalized:
            return self.env["product.product"]
        domain = [
            ("connection_id", "=", connection.id),
            ("external_product_id", "in", normalized),
            ("active", "=", True),
        ]
        binding = self.browse()
        if store_binding:
            store_binding.ensure_one()
            if store_binding.connection_id != connection:
                raise ValidationError(_("The delivery store belongs to another connection."))
            binding = self.search(
                domain + [("store_binding_id", "=", store_binding.id)],
                order="id",
                limit=1,
            )
        if not binding:
            binding = self.search(
                domain + [("store_binding_id", "=", False)],
                order="id",
                limit=1,
            )
        return binding.product_id

    @api.model
    def resolve_order_product(
        self, connection, store_binding, reference, *, identity_kind="external",
        modifier=False, template_field=None,
    ):
        """Resolve a connector-declared product identity for an authenticated store."""
        connection.ensure_one()
        store_binding.ensure_one()
        config = store_binding.pos_config_id
        if (
            not connection.active
            or not store_binding.active
            or store_binding.connection_id != connection
            or store_binding.company_id != connection.company_id
            or config.company_id != connection.company_id
        ):
            raise DeliveryBusinessError(_("The delivery product belongs to another connection or branch."), code="product_scope_error")
        value = str(reference or "").strip()
        if not value:
            raise DeliveryBusinessError(_("A delivery product identifier is required."), code="product_not_found")
        if identity_kind == "external":
            product = self.env["product.product"]
            if modifier:
                product = self._external_mapping_product(
                    self.env["bs.delivery.modifier.binding"], "external_modifier_id",
                    connection, store_binding, value,
                )
            if not product:
                product = self._external_mapping_product(
                    self, "external_product_id", connection, store_binding, value,
                )
        elif identity_kind == "odoo_variant":
            if not value.isdecimal():
                raise DeliveryBusinessError(_("The Odoo product identifier is invalid."), code="product_not_found")
            product = self.env["product.product"].browse(int(value)).exists()
        elif identity_kind == "odoo_template":
            if not value.isdecimal():
                raise DeliveryBusinessError(_("The Odoo product identifier is invalid."), code="product_not_found")
            template = self.env["product.template"].browse(int(value)).exists()
            product = template.product_variant_id
        elif identity_kind == "template_field":
            Template = self.env["product.template"]
            field = Template._fields.get(template_field or "")
            if not field or field.type != "char":
                raise DeliveryBusinessError(_("The product reference field is invalid."), code="product_identity_error")
            templates = Template.search([
                (template_field, "=", value),
                ("company_id", "in", [False, connection.company_id.id]),
            ], limit=2)
            if len(templates) > 1:
                raise DeliveryBusinessError(_("The product identifier matches multiple products."), code="product_identity_conflict")
            product = templates.product_variant_id
        elif identity_kind in ("barcode", "default_code"):
            products = self.env["product.product"].search([
                (identity_kind, "=", value),
                ("company_id", "in", [False, connection.company_id.id]),
            ], limit=2)
            if len(products) > 1:
                raise DeliveryBusinessError(_("The product identifier matches multiple products."), code="product_identity_conflict")
            product = products
        else:
            raise DeliveryBusinessError(_("The delivery product identity type is invalid."), code="product_identity_error")
        if not product:
            raise DeliveryBusinessError(_("The delivery product identifier is unknown."), code="product_not_found")
        if (
            not product.active
            or not product.sale_ok
            or (product.company_id and product.company_id != connection.company_id)
            or (
                not modifier
                and (
                    not product.available_in_pos
                    or (
                        config.limit_categories
                        and not (product.pos_categ_ids & config.iface_available_categ_ids)
                    )
                )
            )
        ):
            raise DeliveryBusinessError(_("The delivery product is unavailable in this POS branch."), code="product_unavailable")
        return product

    @api.model
    def _external_mapping_product(self, model, field_name, connection, store_binding, value):
        model = model.with_context(active_test=False)
        domain = [("connection_id", "=", connection.id), (field_name, "=", value)]
        mapping = model.search(
            domain + [("store_binding_id", "=", store_binding.id)], limit=1,
        ) or model.search(domain + [("store_binding_id", "=", False)], limit=1)
        if mapping and not mapping.active:
            raise DeliveryBusinessError(
                _("The delivery product mapping is disabled for this branch."),
                code="product_unavailable",
            )
        return mapping.product_id

    @api.model
    def resolve_binding_for_product(self, connection, product, store_binding=None):
        """Return the most specific active mapping for an Odoo product.

        A store override always wins over the connection-wide mapping.  Keeping
        this lookup in the binding model avoids connector bridges inventing
        local/default-code identifiers when mirroring a POS order.
        """
        connection.ensure_one()
        product.ensure_one()
        domain = [
            ("connection_id", "=", connection.id),
            ("product_id", "=", product.id),
            ("active", "=", True),
        ]
        if store_binding:
            store_binding.ensure_one()
            if store_binding.connection_id != connection:
                raise ValidationError(_("The delivery store belongs to another connection."))
            binding = self.search(
                domain + [("store_binding_id", "=", store_binding.id)],
                order="id",
                limit=1,
            )
            if binding:
                return binding
        return self.search(
            domain + [("store_binding_id", "=", False)],
            order="id",
            limit=1,
        )
