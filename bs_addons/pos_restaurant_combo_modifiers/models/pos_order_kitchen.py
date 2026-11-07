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

    @api.model
    def _load_pos_preparation_data_fields(self):
        return super()._load_pos_preparation_data_fields() + [
            "name",
            "date_order",
            "kitchen_cashier_name",
            "kitchen_table_name",
            "kitchen_delivery_app",
            "kitchen_delivery_ref",
        ]

    @staticmethod
    def _modifier_entry_comparable(entry):
        return {key: value for key, value in entry.items() if key != "change_status"}

    def _modifier_display_delta(self, prep_line, snapshot):
        old_snapshot = prep_line.modifier_acknowledged_data or []
        old_by_key = {entry.get("key"): entry for entry in old_snapshot}
        new_by_key = {entry.get("key"): entry for entry in snapshot}
        display_data = []

        for entry in snapshot:
            display_entry = dict(entry)
            old_entry = old_by_key.get(entry.get("key"))
            if old_entry is None:
                display_entry["change_status"] = "added"
            elif self._modifier_entry_comparable(old_entry) != entry:
                display_entry["change_status"] = "updated"
            display_data.append(display_entry)

        for entry in old_snapshot:
            if entry.get("key") not in new_by_key:
                removed_entry = self._modifier_entry_comparable(entry)
                removed_entry["change_status"] = "removed"
                display_data.append(removed_entry)
        return display_data

    def _reactivate_modifier_prep_line(self, prep_line):
        state_lines = prep_line.combo_line_ids or prep_line
        states = self.env["pos.prep.state"].search([
            ("prep_line_id", "in", state_lines.ids),
        ])
        states.filtered(lambda state: not state.todo).write({"todo": True})

    def _sync_kitchen_modifiers(self, order_line_filter):
        changed = False
        category_ids = set()
        prep_orders = self.env["pos.prep.order"].search([("pos_order_id", "=", self.id)])
        prep_lines = prep_orders.prep_line_ids
        parent_lines = self.lines.filtered(
            lambda line: not line.is_modifier_line and order_line_filter(line.uuid)
        )

        for parent_line in parent_lines:
            matching_prep_lines = prep_lines.filtered(
                lambda prep_line: prep_line.pos_order_line_uuid == parent_line.uuid
            )
            if not matching_prep_lines:
                continue
            snapshot = parent_line._modifier_kitchen_snapshot()
            for prep_line in matching_prep_lines:
                if not prep_line.modifier_snapshot_initialized:
                    prep_line.write({
                        "modifier_snapshot_initialized": True,
                        "modifier_snapshot_data": snapshot,
                        "modifier_acknowledged_data": snapshot,
                        "modifier_display_data": snapshot,
                    })
                    continue
                if prep_line.modifier_snapshot_data == snapshot:
                    continue
                prep_line.write({
                    "modifier_snapshot_data": snapshot,
                    "modifier_display_data": self._modifier_display_delta(prep_line, snapshot),
                })
                self._reactivate_modifier_prep_line(prep_line)
                products = prep_line.combo_line_ids.product_id or prep_line.product_id
                category_ids.update(products.pos_categ_ids.ids)
                changed = True
        return changed, category_ids

    def _process_preparation_changes(self, options):
        self.ensure_one()
        inherited_filter = self.env.context.get("ppc_order_line_filter", lambda _uuid: True)
        modifier_uuids = set(self.lines.filtered("is_modifier_line").mapped("uuid"))

        def kitchen_line_filter(line_uuid):
            return line_uuid not in modifier_uuids and inherited_filter(line_uuid)

        bridge_order = self.with_context(ppc_order_line_filter=kitchen_line_filter)
        result = super(PosOrder, bridge_order)._process_preparation_changes(options)
        modifier_changed, modifier_category_ids = self._sync_kitchen_modifiers(inherited_filter)
        if modifier_changed:
            result["change"] = True
            result["sound"] = True
            result["category_ids"] = set(result.get("category_ids", [])) | modifier_category_ids
        return result


class PosOrderLine(models.Model):
    _inherit = "pos.order.line"

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
