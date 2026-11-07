from collections import defaultdict
from math import isfinite

from odoo import Command, _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_compare, float_is_zero, float_round, format_amount


class PosOrder(models.Model):
    """Extend the standard POS order without introducing a parallel order model."""

    _inherit = "pos.order"

    @api.model
    def _get_invoice_lines_values(self, line_values, pos_line, move_type):
        values = super()._get_invoice_lines_values(line_values, pos_line, move_type)
        if pos_line.is_modifier_line and pos_line.modifier_option_name:
            values["name"] = pos_line.modifier_option_name
        return values

    def _is_platform_delivery_order(self):
        """True for orders created from a delivery platform (Connect).

        Connect is optional, so it is detected through its import context or
        its delivery order model when that module is installed.
        """
        if self.env.context.get("bs_delivery_core_import"):
            return True
        if not self or "bs.delivery.order" not in self.env:
            return False
        origin = self.refunded_order_id if "refunded_order_id" in self._fields else self.browse()
        orders = (self | origin).filtered("id")
        return bool(orders and self.env["bs.delivery.order"].sudo().search_count(
            [("pos_order_id", "in", orders.ids)], limit=1,
        ))

    def _process_saved_order(self, draft):
        """Authoritatively validate modifiers before payment, stock, and invoicing."""
        self._validate_and_reprice_modifiers()
        return super()._process_saved_order(draft)

    def _validate_and_reprice_modifiers(self):
        for order in self:
            order.lines._validate_modifier_integrity()
            order._validate_refund_modifiers()
            for parent_line in order.lines.filtered(
                lambda line: not line.is_modifier_line
                and not line.refunded_orderline_id
            ):
                parent_line._validate_parent_modifier_configuration()
            order.lines.filtered("is_modifier_line")._onchange_amount_line_all()
            order._compute_prices()

    def _validate_refund_modifiers(self):
        self.ensure_one()
        for refund_parent in self.lines.filtered(
            lambda line: not line.is_modifier_line and line.refunded_orderline_id
        ):
            original_parent = refund_parent.refunded_orderline_id
            expected_original_children = original_parent.modifier_child_line_ids
            refund_children = refund_parent.modifier_child_line_ids
            refunded_original_children = refund_children.refunded_orderline_id
            if set(expected_original_children.ids) != set(refunded_original_children.ids):
                raise ValidationError(
                    _("A refund must include the product modifiers linked to its parent line.")
                )

            for refund_line in refund_children:
                original_line = refund_line.refunded_orderline_id
                if (
                    not original_line.is_modifier_line
                    or original_line.modifier_parent_line_id != original_parent
                ):
                    raise ValidationError(
                        _("A refunded modifier must reference the original modifier line.")
                    )
                expected_quantity = (
                    original_line.modifier_qty_per_parent * refund_parent.qty
                )
                rounding = refund_line.product_uom_id.rounding
                if not float_is_zero(
                    refund_line.modifier_qty_per_parent
                    - original_line.modifier_qty_per_parent,
                    precision_rounding=0.000001,
                ) or float_compare(
                    refund_line.qty,
                    expected_quantity,
                    precision_rounding=rounding,
                ):
                    raise ValidationError(
                        _("A refunded modifier quantity must follow its parent quantity.")
                    )
                refund_line.write({
                    "product_id": original_line.product_id.id,
                    "modifier_option_id": original_line.modifier_option_id.id,
                    "modifier_option_name": original_line.modifier_option_name,
                    "modifier_qty_per_parent": original_line.modifier_qty_per_parent,
                    "modifier_is_included": original_line.modifier_is_included,
                    "price_unit": original_line.price_unit,
                    "discount": original_line.discount,
                    "tax_ids": [Command.set(original_line.tax_ids.ids)],
                })

    @api.model
    def _process_order(self, order, existing_order):
        """Validate modifier links after Odoo resolves offline UUID relations."""
        order_id = super()._process_order(order, existing_order)
        self.browse(order_id).lines._validate_modifier_integrity()
        return order_id

    def _refund(self):
        """Reconnect copied modifier lines to their copied parents."""
        refund_orders = super()._refund()
        for refund_order in refund_orders:
            refund_by_original_id = {
                line.refunded_orderline_id.id: line
                for line in refund_order.lines
                if line.refunded_orderline_id
            }
            for refund_line in refund_order.lines.filtered(
                lambda line: line.refunded_orderline_id.modifier_parent_line_id
            ):
                original_parent = (
                    refund_line.refunded_orderline_id.modifier_parent_line_id
                )
                refund_parent = refund_by_original_id.get(original_parent.id)
                if not refund_parent:
                    raise ValidationError(
                        _("A refunded modifier line must have its refunded parent line.")
                    )
                refund_line.write({
                    "modifier_parent_line_id": refund_parent.id,
                    "is_modifier_line": True,
                })
            refund_order._validate_and_reprice_modifiers()
        return refund_orders


class PosOrderLine(models.Model):
    """Extend standard order lines, including Odoo's native combo relationships."""

    _inherit = "pos.order.line"

    modifier_parent_line_id = fields.Many2one(
        comodel_name="pos.order.line",
        string="Modifier Parent",
        ondelete="cascade",
        index="btree_not_null",
    )
    modifier_child_line_ids = fields.One2many(
        comodel_name="pos.order.line",
        inverse_name="modifier_parent_line_id",
        string="Modifier Lines",
    )
    modifier_option_id = fields.Many2one(
        comodel_name="pos.modifier.option",
        string="Modifier Option",
        ondelete="restrict",
        index="btree_not_null",
    )
    modifier_option_name = fields.Char(
        string="Modifier Name at Sale",
        copy=True,
        help="Historical option name kept unchanged for receipts and invoices.",
    )
    is_modifier_line = fields.Boolean(
        string="Is Modifier Line",
        default=False,
        index=True,
    )
    modifier_qty_per_parent = fields.Float(
        string="Modifier Quantity per Parent",
        digits="Product Unit",
        default=0,
    )
    modifier_is_included = fields.Boolean(
        string="Included Modifier Quantity",
        default=False,
        help="Marks the quantity slice covered by the modifier group's included allowance.",
    )
    modifier_configuration_key = fields.Char(
        string="Modifier Configuration Key",
        help="Stable offline key used to prevent incompatible POS lines from merging.",
    )
    modifier_instruction_data = fields.Json(
        string="Modifier Instruction Data",
        help="Structured preparation instructions; no artificial products are created.",
    )
    modifier_selection_data = fields.Json(
        string="Modifier Selection Data",
        help="Technical snapshot used to restore modifier choices when editing a POS line.",
    )
    combo_item_extra_price = fields.Monetary(
        string="Combo Choice Extra Price",
        currency_field="currency_id",
        default=0.0,
        copy=True,
        help="Historical combo choice surcharge used for receipt breakdowns.",
    )
    combo_modifier_parent_product_name = fields.Char(
        string="Parent Product",
        compute="_compute_combo_modifier_backend_details",
    )
    modifier_instruction_summary = fields.Text(
        string="Instructions",
        compute="_compute_combo_modifier_backend_details",
    )

    @api.model
    def _load_pos_data_fields(self, config):
        return super()._load_pos_data_fields(config) + [
            "modifier_parent_line_id", "modifier_child_line_ids", "modifier_option_id",
            "modifier_option_name",
            "is_modifier_line", "modifier_qty_per_parent",
            "modifier_is_included",
            "modifier_configuration_key", "modifier_instruction_data",
            "modifier_selection_data",
            "combo_item_extra_price",
        ]

    @api.depends(
        "modifier_parent_line_id.full_product_name",
        "combo_parent_id.full_product_name",
        "modifier_instruction_data",
    )
    def _compute_combo_modifier_backend_details(self):
        for line in self:
            parent = line.modifier_parent_line_id or line.combo_parent_id
            line.combo_modifier_parent_product_name = (
                parent.full_product_name or parent.product_id.display_name
                if parent
                else ""
            )
            instructions = []
            instruction_data = line.modifier_instruction_data or {}
            if isinstance(instruction_data, dict):
                for instruction in instruction_data.get("instructions", []):
                    if not isinstance(instruction, dict):
                        continue
                    name = instruction.get("name") or instruction.get("kitchen_name") or ""
                    group_name = instruction.get("group_name") or ""
                    quantity = instruction.get("quantity", 1)
                    label = f"{group_name}: {name}" if group_name else name
                    if quantity != 1:
                        label = f"{label} ×{quantity}"
                    if label:
                        instructions.append(label)
            line.modifier_instruction_summary = "\n".join(instructions)

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        for line in lines.filtered(
            lambda record: record.is_modifier_line and not record.modifier_option_name
        ):
            line.modifier_option_name = line.modifier_option_id.name
        return lines

    def _prepare_tax_base_line_values(self):
        lines = self
        if self.env.context.get("invoicing"):
            lines = self.filtered(
                lambda line: not line.is_modifier_line
                or (
                    not line.modifier_is_included
                    and not line.currency_id.is_zero(line.price_unit)
                )
            )
        return super(PosOrderLine, lines)._prepare_tax_base_line_values()

    @api.model
    def _is_field_accepted(self, field):
        if field in ("modifier_parent_line_id", "modifier_child_line_ids"):
            return False
        return super()._is_field_accepted(field)

    def _validate_modifier_integrity(self):
        for line in self:
            if line.is_modifier_line and not line.modifier_parent_line_id:
                raise ValidationError(_("A modifier line must have a parent line."))
            if line.is_modifier_line and not line.modifier_option_id:
                raise ValidationError(_("A modifier line must identify its modifier option."))
            if line.modifier_parent_line_id and not line.is_modifier_line:
                raise ValidationError(
                    _("A line linked to a modifier parent must be marked as a modifier line.")
                )
        return True

    def _modifier_selection_quantities(self):
        self.ensure_one()
        quantities = {}
        data = self.modifier_selection_data or {}
        if not isinstance(data, dict):
            raise ValidationError(_("Modifier selection data is malformed."))
        for raw_option_id, raw_quantity in data.items():
            try:
                option_id = int(raw_option_id)
                quantity = float(raw_quantity)
            except (TypeError, ValueError):
                raise ValidationError(_("Modifier selection data is malformed.")) from None
            if not isfinite(quantity) or quantity <= 0 or not float_is_zero(
                quantity - round(quantity, 3), precision_rounding=0.0000001
            ):
                raise ValidationError(
                    _("Modifier quantities must be positive with at most three decimal places.")
                )
            quantities[option_id] = quantity
        return quantities

    def _modifier_instruction_quantities(self):
        self.ensure_one()
        data = self.modifier_instruction_data or {}
        if not isinstance(data, dict) or not isinstance(data.get("instructions", []), list):
            raise ValidationError(_("Modifier instruction data is malformed."))
        quantities = defaultdict(float)
        for instruction in data.get("instructions", []):
            if not isinstance(instruction, dict):
                raise ValidationError(_("Modifier instruction data is malformed."))
            try:
                option_id = int(instruction["option_id"])
                quantity = float(instruction["quantity"])
            except (KeyError, TypeError, ValueError):
                raise ValidationError(_("Modifier instruction data is malformed.")) from None
            quantities[option_id] += quantity
        return dict(quantities)

    def _expected_modifier_unit_price(self):
        self.ensure_one()
        option = self.modifier_option_id
        parent = self.modifier_parent_line_id
        if parent:
            group_line = parent.product_id.product_tmpl_id.modifier_group_line_ids.filtered(
                lambda line: line.modifier_group_id == option.group_id
            )[:1]
            if group_line:
                option = group_line._effective_option(option)
        order = self.order_id
        if self.modifier_is_included or option.price_mode == "included":
            return 0.0
        if option.price_mode == "fixed_extra":
            return option.currency_id._convert(
                option.extra_price,
                order.currency_id,
                order.company_id,
                order.date_order,
            )
        if option.price_mode == "product_pricelist":
            quantity = abs(self.qty)
            if order.pricelist_id:
                return order.pricelist_id._get_product_price(
                    self.product_id,
                    quantity,
                    currency=order.currency_id,
                    date=order.date_order,
                )
            return self.product_id.currency_id._convert(
                self.product_id.lst_price,
                order.currency_id,
                order.company_id,
                order.date_order,
            )
        raise ValidationError(_("Unsupported modifier price mode."))

    def _validate_parent_modifier_configuration(self):
        self.ensure_one()
        product_template = self.product_id.product_tmpl_id
        group_lines = product_template.modifier_group_line_ids.filtered(
            lambda line: line.modifier_group_id.active
        ).sorted(lambda line: (line.sequence, line.id))
        instruction_data = self.modifier_instruction_data or {}
        has_instruction_configuration = bool(
            not isinstance(instruction_data, dict)
            or instruction_data.get("instructions")
        )
        has_configuration = bool(
            self.modifier_child_line_ids
            or self.modifier_selection_data
            or has_instruction_configuration
        )
        if not product_template.allow_modifiers:
            if has_configuration:
                raise ValidationError(
                    _(
                        "Modifiers are not enabled for product %(product)s.",
                        product=product_template.display_name,
                    )
                )
            return
        if not group_lines:
            if has_configuration:
                raise ValidationError(
                    _(
                        "No modifier groups are configured for product %(product)s.",
                        product=product_template.display_name,
                    )
                )
            return

        selection_quantities = self._modifier_selection_quantities()
        instruction_quantities = self._modifier_instruction_quantities()
        # A delivery platform order was already placed and paid by the
        # customer: the cashier-screen rules (limits, options closed in the
        # branch) must not stop it from reaching the cashier and kitchen.
        platform_order = self.order_id._is_platform_delivery_order()
        allowed_by_id = {}
        for group_line in group_lines:
            settings_by_option = {
                line.option_id.id: line for line in group_line.option_line_ids
            }
            options = (
                group_line.modifier_group_id.with_context(active_test=False).option_ids
                if platform_order
                else group_line.modifier_group_id.option_ids.filtered("active")
            )
            for option in options:
                settings = settings_by_option.get(option.id)
                if not settings or settings.available or platform_order:
                    allowed_by_id[option.id] = settings or option
        unknown_option_ids = set(selection_quantities) - set(allowed_by_id)
        if unknown_option_ids:
            raise ValidationError(_("The order contains an unavailable modifier option."))

        child_lines_by_option = defaultdict(lambda: self.env["pos.order.line"])
        for child_line in self.modifier_child_line_ids:
            option = child_line.modifier_option_id
            if option.id not in allowed_by_id or allowed_by_id[option.id].option_type != "product":
                raise ValidationError(
                    _("Instruction modifiers cannot create product order lines.")
                )
            if child_line.product_id != allowed_by_id[option.id].product_id:
                raise ValidationError(
                    _("A modifier order line must use the product configured on its option.")
                )
            expected_quantity = child_line.modifier_qty_per_parent * self.qty
            if child_line.modifier_qty_per_parent <= 0 or float_compare(
                child_line.qty,
                expected_quantity,
                precision_rounding=child_line.product_uom_id.rounding,
            ):
                raise ValidationError(
                    _("A modifier quantity must follow its parent quantity.")
                )
            child_lines_by_option[option.id] |= child_line

        selected_instruction_quantities = {
            option_id: quantity
            for option_id, quantity in selection_quantities.items()
            if allowed_by_id[option_id].option_type == "instruction"
        }
        if instruction_quantities != selected_instruction_quantities:
            raise ValidationError(
                _("Structured modifier instructions do not match the selected options.")
            )

        for group_line in group_lines:
            group = group_line.modifier_group_id
            group_options = group.with_context(active_test=False).option_ids.filtered(
                lambda option: (option.active or platform_order)
                and option.id in allowed_by_id
            ).sorted(
                lambda option: (option.sequence, option.id)
            )
            total_quantity = sum(
                selection_quantities.get(option.id, 0) for option in group_options
            )
            if (total_quantity < group.min_qty or total_quantity > group.max_qty) \
                    and not platform_order:
                raise ValidationError(
                    _(
                        "Modifier group %(group)s does not satisfy its quantity rules.",
                        group=group.display_name,
                    )
                )

            included_remaining = group.included_qty
            for option in group_options:
                settings = allowed_by_id[option.id]
                selected_quantity = selection_quantities.get(option.id, 0)
                if selected_quantity > settings.max_qty and not platform_order:
                    raise ValidationError(
                        _(
                            "Modifier option %(option)s exceeds its maximum quantity.",
                            option=option.display_name,
                        )
                    )
                if settings.option_type == "instruction":
                    continue
                if selected_quantity and not float_is_zero(
                    selected_quantity - float_round(
                        selected_quantity,
                        precision_rounding=settings.product_id.uom_id.rounding,
                    ),
                    precision_rounding=0.000001,
                ):
                    raise ValidationError(
                        _("The modifier quantity is incompatible with its product unit of measure.")
                    )
                option_lines = child_lines_by_option.get(
                    option.id, self.env["pos.order.line"]
                )
                actual_quantity = sum(option_lines.mapped("modifier_qty_per_parent"))
                if not float_is_zero(
                    actual_quantity - selected_quantity,
                    precision_rounding=0.000001,
                ):
                    raise ValidationError(
                        _("Product modifier lines do not match the selected quantities.")
                    )
                expected_included = min(selected_quantity, included_remaining)
                included_remaining -= expected_included
                actual_included = sum(
                    option_lines.filtered("modifier_is_included").mapped(
                        "modifier_qty_per_parent"
                    )
                )
                if not float_is_zero(
                    actual_included - expected_included,
                    precision_rounding=0.000001,
                ):
                    raise ValidationError(
                        _("Included modifier quantities do not match the group allowance.")
                    )

        expected_tax_cache = {}
        for child_line in self.modifier_child_line_ids:
            expected_price = child_line._expected_modifier_unit_price()
            if float_compare(
                child_line.price_unit,
                expected_price,
                precision_rounding=self.order_id.currency_id.rounding,
            ):
                currency = self.order_id.currency_id
                raise ValidationError(
                    _(
                        "Modifier price mismatch for %(modifier)s (%(modifier_id)s) "
                        "on %(product)s.\n"
                        "Order unit price: %(order_price)s\n"
                        "Current configured unit price: %(current_price)s\n"
                        "Difference: %(difference)s",
                        modifier=(child_line.modifier_option_name
                                  or child_line.modifier_option_id.name),
                        modifier_id="posmod-%s" % child_line.modifier_option_id.id,
                        product=self.product_id.display_name,
                        order_price=format_amount(self.env, child_line.price_unit, currency),
                        current_price=format_amount(self.env, expected_price, currency),
                        difference=format_amount(
                            self.env,
                            abs(currency.round(child_line.price_unit) - currency.round(expected_price)),
                            currency,
                        ),
                    )
                )
            expected_taxes = expected_tax_cache.setdefault(
                child_line.product_id.id,
                child_line.product_id.taxes_id.filtered_domain(
                    self.env["account.tax"]._check_company_domain(
                        self.order_id.company_id
                    )
                ),
            )
            if set(child_line.tax_ids.ids) != set(expected_taxes.ids):
                raise ValidationError(
                    _("Modifier taxes do not match the modifier product taxes.")
                )
            if not float_is_zero(
                child_line.discount - self.discount,
                precision_rounding=0.000001,
            ):
                raise ValidationError(
                    _("Paid modifiers must follow the parent product discount.")
                )
            child_line.write({
                "price_unit": expected_price,
                "discount": self.discount,
                "tax_ids": [Command.set(expected_taxes.ids)],
            })

    def _prepare_refund_data(self, refund_order, PosPackOperationLot):
        values = super()._prepare_refund_data(refund_order, PosPackOperationLot)
        # The copied parent belongs to the original order. It is reconnected by
        # pos.order._refund after all copied refund lines have been created.
        values.update({
            "modifier_parent_line_id": False,
            "modifier_child_line_ids": [Command.clear()],
            "is_modifier_line": False,
        })
        return values

    @api.constrains("modifier_parent_line_id", "is_modifier_line", "order_id")
    def _check_modifier_parent(self):
        for line in self.filtered("modifier_parent_line_id"):
            if line.modifier_parent_line_id == line:
                raise ValidationError(_("An order line cannot be its own modifier."))
            if line.modifier_parent_line_id.order_id != line.order_id:
                raise ValidationError(
                    _("A modifier line and its parent must belong to the same order.")
                )
            if line.modifier_parent_line_id.modifier_parent_line_id:
                raise ValidationError(_("Modifier lines cannot contain nested modifiers."))
            if not line.is_modifier_line:
                raise ValidationError(
                    _("A line linked to a modifier parent must be marked as a modifier line.")
                )
