from odoo import api, fields, models


class PosOrder(models.Model):
    _inherit = "pos.order"

    kitchen_cashier_name = fields.Char(
        string="Kitchen Cashier",
        compute="_compute_kitchen_header_data",
    )
    kitchen_table_name = fields.Char(
        string="Kitchen Table",
        compute="_compute_kitchen_header_data",
    )
    kitchen_delivery_app = fields.Char(compute="_compute_kitchen_delivery_header")
    kitchen_delivery_ref = fields.Char(compute="_compute_kitchen_delivery_header")

    def _compute_kitchen_delivery_header(self):
        delivery_by_pos = {}
        if "bs.delivery.order" in self.env and self.ids:
            deliveries = self.env["bs.delivery.order"].sudo().search(
                [("pos_order_id", "in", self.ids)], order="id desc"
            )
            deliveries.fetch([
                "pos_order_id", "provider_id", "external_source",
                "external_reference", "external_order_id",
            ])
            for delivery in deliveries:
                delivery_by_pos.setdefault(delivery.pos_order_id.id, delivery)
        for order in self:
            delivery = delivery_by_pos.get(order.id)
            provider = delivery.provider_id if delivery else False
            source = delivery.external_source if delivery else False
            reference = (
                delivery.external_reference or delivery.external_order_id
                if delivery else False
            )
            if (not provider or provider.code == "blend") and "blend_aggregator" in order._fields:
                source = order.blend_aggregator or source
                reference = order.blend_aggregator_order_id or reference
            order.kitchen_delivery_app = (
                source if provider and provider.code == "blend" and source != "blend"
                else (provider.display_name if provider else source)
            )
            order.kitchen_delivery_ref = reference

    @api.depends("user_id.name", "table_id.table_number", "table_id.parent_id.table_number")
    def _compute_kitchen_header_data(self):
        for order in self:
            order.kitchen_cashier_name = order.user_id.display_name or ""
            table = order.table_id
            table_name = str(table.table_number) if table else ""
            if table and table.parent_id:
                table_name = f"{table_name} & {table.parent_id.table_number}"
            order.kitchen_table_name = table_name

    # ------------------------------------------------------------------ Resta kitchen screen (resta_kds)
    def _resta_kds_header(self):
        vals = super()._resta_kds_header()
        info = []
        if self.kitchen_delivery_app:
            info.append(self.kitchen_delivery_app + (" #%s" % self.kitchen_delivery_ref if self.kitchen_delivery_ref else ""))
        if self.kitchen_cashier_name:
            info.append(self.kitchen_cashier_name)
        if info:
            vals["info"] = " · ".join(info)
        if self.kitchen_table_name and "&" in self.kitchen_table_name:
            vals["table_label"] = self.kitchen_table_name
        return vals


class PosOrderLine(models.Model):
    _inherit = "pos.order.line"

    def _resta_kds_relevant(self, display):
        # a modifier line is shown under its dish (see _resta_kds_modifiers), not as a dish
        if self.is_modifier_line:
            return False
        return super()._resta_kds_relevant(display)

    def _resta_kds_modifiers(self):
        mods = super()._resta_kds_modifiers()
        for entry in self._modifier_kitchen_snapshot():
            kind = "instruction" if entry.get("kind") == "instruction" else (
                "remove" if entry.get("operation") == "remove" else "add")
            mods.append({"key": entry["key"], "name": entry.get("name") or "", "kind": kind,
                         "qty": entry.get("quantity") or 1})
        return mods

    def _modifier_kitchen_snapshot(self):
        self.ensure_one()
        selection_data = self.modifier_selection_data or {}
        if not isinstance(selection_data, dict):
            return []

        instruction_by_option = {}
        instruction_data = self.modifier_instruction_data or {}
        if isinstance(instruction_data, dict):
            for instruction in instruction_data.get("instructions", []):
                if not isinstance(instruction, dict):
                    continue
                try:
                    option_id = int(instruction.get("option_id"))
                except (TypeError, ValueError):
                    continue
                instruction_by_option[option_id] = instruction

        quantities = {}
        for raw_option_id, raw_quantity in selection_data.items():
            try:
                option_id = int(raw_option_id)
                quantity = float(raw_quantity)
            except (TypeError, ValueError):
                continue
            if quantity > 0:
                quantities[option_id] = quantity

        options = self.env["pos.modifier.option"].browse(quantities).exists()
        option_by_id = {option.id: option for option in options}
        entries = []
        for option_id, quantity in quantities.items():
            option = option_by_id.get(option_id)
            instruction = instruction_by_option.get(option_id, {})
            if option:
                group_line = self.product_id.product_tmpl_id.modifier_group_line_ids.filtered(
                    lambda line: line.modifier_group_id == option.group_id
                )[:1]
                if group_line:
                    option = group_line._effective_option(option)
                option_type = option.option_type
                name = option.kitchen_name or option.name
                operation = option.operation
                sequence = (
                    option.group_id.sequence,
                    option.group_id.id,
                    option.sequence,
                    option.id,
                )
            elif instruction:
                option_type = "instruction"
                name = instruction.get("kitchen_name") or instruction.get("name") or ""
                operation = instruction.get("operation") or "note"
                sequence = (0, 0, 0, option_id)
            else:
                continue
            entries.append({
                "key": f"{option_type}:{option_id}",
                "option_id": option_id,
                "name": name,
                "kind": option_type,
                "operation": operation,
                "quantity": quantity,
                "_sequence": sequence,
            })

        entries.sort(key=lambda entry: entry["_sequence"])
        for entry in entries:
            entry.pop("_sequence")
        return entries
