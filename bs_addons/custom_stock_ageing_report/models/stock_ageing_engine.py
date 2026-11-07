from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, time, timedelta

import pytz

from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import format_amount, formatLang


class StockAgeingEngine(models.AbstractModel):
    _name = "stock.ageing.engine"
    _description = "Stock Ageing FIFO Calculation Engine"

    _TRACKED_USAGES = ("internal", "transit")
    _EPSILON = 1e-9

    def _build_periods(self, period_length, period_count):
        """Return mutually exclusive, exhaustive age buckets.

        ``period_count`` includes the final open-ended bucket. For example,
        length=30 and count=4 produces 0-30, 31-60, 61-90 and >90.
        """
        if period_length <= 0:
            raise ValidationError(_("Period length must be greater than zero."))
        if period_count < 2:
            raise ValidationError(_("At least two ageing buckets are required."))

        periods = []
        for index in range(period_count - 1):
            start = 0 if index == 0 else index * period_length + 1
            end = (index + 1) * period_length
            periods.append({
                "index": index,
                "min_days": start,
                "max_days": end,
                "label": _("%(start)s-%(end)s", start=start, end=end),
            })
        threshold = (period_count - 1) * period_length
        periods.append({
            "index": period_count - 1,
            "min_days": threshold + 1,
            "max_days": None,
            "label": _("> %(days)s", days=threshold),
        })
        return periods

    def _bucket_index(self, age_days, period_length, period_count):
        age_days = max(0, age_days)
        for index in range(period_count - 1):
            if age_days <= (index + 1) * period_length:
                return index
        return period_count - 1

    def _cutoff_datetime(self, wizard):
        """Exclusive UTC cutoff corresponding to midnight after report_date."""
        timezone = pytz.timezone(self.env.user.tz or "UTC")
        local_cutoff = timezone.localize(
            datetime.combine(wizard.report_date + timedelta(days=1), time.min)
        )
        return local_cutoff.astimezone(pytz.UTC).replace(tzinfo=None)

    def _local_date(self, value):
        timezone = pytz.timezone(self.env.user.tz or "UTC")
        utc_value = pytz.UTC.localize(fields.Datetime.to_datetime(value))
        return utc_value.astimezone(timezone).date()

    def _check_company_access(self, wizard):
        if wizard.company_id not in self.env.companies:
            raise AccessError(_("You are not allowed to report on company %s.", wizard.company_id.display_name))

    def _get_target_locations(self, wizard):
        Location = self.env["stock.location"].with_context(active_test=False)
        company_domain = [
            ("usage", "=", "internal"),
            "|",
            ("company_id", "=", False),
            ("company_id", "=", wizard.company_id.id),
        ]

        warehouse_ids = wizard.warehouse_ids if wizard.report_based_on == "warehouse" else self.env["stock.warehouse"]
        location_ids = wizard.location_ids if wizard.report_based_on == "location" else Location

        warehouse_locations = Location
        if warehouse_ids:
            warehouse_locations = Location.search(
                company_domain + [("id", "child_of", warehouse_ids.view_location_id.ids)]
            )

        selected_locations = Location
        if location_ids:
            selected_locations = Location.search(
                company_domain + [("id", "child_of", location_ids.ids)]
            )

        if warehouse_ids:
            locations = warehouse_locations
        elif location_ids:
            locations = selected_locations
        else:
            locations = Location.search(company_domain)

        if not locations:
            raise ValidationError(_("The selected filters do not contain any internal stock location."))
        return locations

    def _get_filtered_products(self, wizard):
        domain = [
            ("is_storable", "=", True),
            "|",
            ("company_id", "=", False),
            ("company_id", "=", wizard.company_id.id),
        ]
        if wizard.filter_by == "product" and wizard.product_ids:
            domain.append(("id", "in", wizard.product_ids.ids))
        if wizard.filter_by == "category" and wizard.category_ids:
            domain.append(("categ_id", "child_of", wizard.category_ids.ids))
        return self.env["product.product"].with_context(active_test=False).search(domain)

    def _get_candidate_products(self, wizard, target_locations, cutoff):
        products = self._get_filtered_products(wizard)
        if not products:
            return products, {}

        MoveLine = self.env["stock.move.line"].with_context(active_test=False)
        common_domain = [
            ("state", "=", "done"),
            ("company_id", "=", wizard.company_id.id),
            ("date", "<", cutoff),
            ("product_id", "in", products.ids),
        ]
        incoming = {
            (product.id, location.id): quantity
            for product, location, quantity in MoveLine._read_group(
                common_domain + [("location_dest_id", "in", target_locations.ids)],
                ["product_id", "location_dest_id"],
                ["quantity_product_uom:sum"],
            )
        }
        outgoing = {
            (product.id, location.id): quantity
            for product, location, quantity in MoveLine._read_group(
                common_domain + [("location_id", "in", target_locations.ids)],
                ["product_id", "location_id"],
                ["quantity_product_uom:sum"],
            )
        }

        location_balances = {
            key: incoming.get(key, 0.0) - outgoing.get(key, 0.0)
            for key in set(incoming) | set(outgoing)
        }
        balances = defaultdict(float)
        candidate_ids = set()
        products_by_id = {product.id: product for product in products}
        for (product_id, _location_id), quantity in location_balances.items():
            product = products_by_id[product_id]
            if not product.uom_id.is_zero(quantity):
                candidate_ids.add(product_id)
            balances[product_id] += quantity
        candidate_ids = sorted(candidate_ids)
        return products.browse(candidate_ids), {
            product_id: balances[product_id] for product_id in candidate_ids
        }

    def _empty_location_state(self):
        return {"layers": [], "deficits": []}

    def _new_portion(self, uid, layer_date, quantity, unit_cost, sequence):
        return {
            "uid": uid,
            "date": layer_date,
            "qty": quantity,
            "unit_cost": unit_cost,
            "sequence": sequence,
        }

    def _copy_portion(self, portion, quantity=None):
        copied = dict(portion)
        if quantity is not None:
            copied["qty"] = quantity
        return copied

    def _insert_layer(self, state, portion):
        if portion["qty"] <= self._EPSILON:
            return
        for layer in state["layers"]:
            if (
                layer["uid"] == portion["uid"]
                and layer["date"] == portion["date"]
                and abs(layer["unit_cost"] - portion["unit_cost"]) <= self._EPSILON
            ):
                layer["qty"] += portion["qty"]
                return
        keys = [(layer["date"], layer["sequence"]) for layer in state["layers"]]
        position = bisect_right(keys, (portion["date"], portion["sequence"]))
        state["layers"].insert(position, self._copy_portion(portion))

    def _add_portions(self, state, portions):
        """Add stock, first clearing any historical negative-stock deficit."""
        for original in portions:
            portion = self._copy_portion(original)
            while portion["qty"] > self._EPSILON and state["deficits"]:
                deficit = state["deficits"][0]
                cleared = min(portion["qty"], deficit["qty"])
                portion["qty"] -= cleared
                deficit["qty"] -= cleared
                if deficit["qty"] <= self._EPSILON:
                    state["deficits"].pop(0)
            self._insert_layer(state, portion)

    def _consume_layers(
        self,
        state,
        quantity,
        event_date,
        fallback_cost,
        preferred_uids,
        next_sequence,
    ):
        """Consume preferred return layers first, then the oldest FIFO layers."""
        remaining = quantity
        portions = []

        for uid in preferred_uids:
            if remaining <= self._EPSILON:
                break
            index = 0
            while index < len(state["layers"]) and remaining > self._EPSILON:
                layer = state["layers"][index]
                if layer["uid"] != uid:
                    index += 1
                    continue
                taken = min(layer["qty"], remaining)
                portions.append(self._copy_portion(layer, taken))
                layer["qty"] -= taken
                remaining -= taken
                if layer["qty"] <= self._EPSILON:
                    state["layers"].pop(index)
                else:
                    index += 1

        while remaining > self._EPSILON and state["layers"]:
            layer = state["layers"][0]
            taken = min(layer["qty"], remaining)
            portions.append(self._copy_portion(layer, taken))
            layer["qty"] -= taken
            remaining -= taken
            if layer["qty"] <= self._EPSILON:
                state["layers"].pop(0)

        if remaining > self._EPSILON:
            sequence = next_sequence()
            synthetic = self._new_portion(
                "negative:%s" % sequence,
                event_date,
                remaining,
                fallback_cost,
                sequence,
            )
            portions.append(synthetic)
            state["deficits"].append(self._copy_portion(synthetic))
        return portions

    def _slice_trace(self, trace, offset, quantity):
        remaining = quantity
        skipped = offset
        portions = []
        for layer in trace:
            available = layer["qty"]
            if skipped >= available - self._EPSILON:
                skipped -= available
                continue
            available -= skipped
            start_offset = skipped
            skipped = 0.0
            taken = min(available, remaining)
            if taken > self._EPSILON:
                portions.append(self._copy_portion(layer, taken))
                remaining -= taken
            if remaining <= self._EPSILON:
                break
            if start_offset:
                start_offset = 0.0
        return portions, remaining

    def _move_unit_cost(self, move, cutoff, company, cache):
        if move.id in cache:
            return cache[move.id]
        valued_quantity = move._get_valued_qty()
        if move.restrict_partner_id and move.restrict_partner_id != company.partner_id:
            unit_cost = 0.0
        elif move.is_in and valued_quantity:
            historical_value = move.with_company(company)._get_value(
                at_date=cutoff - timedelta(microseconds=1)
            )
            unit_cost = historical_value / valued_quantity
        elif move.is_out and valued_quantity:
            unit_cost = move.value / valued_quantity
        else:
            unit_cost = move.product_id.with_company(company).standard_price
        cache[move.id] = unit_cost
        return unit_cost

    def _simulate_product_batch(self, wizard, product_ids, cutoff, tracked_location_ids, inventories):
        Move = self.env["stock.move"].with_context(active_test=False)
        MoveLine = self.env["stock.move.line"].with_context(active_test=False)
        company = wizard.company_id

        return_moves = Move.search_fetch(
            [
                ("state", "=", "done"),
                ("company_id", "=", company.id),
                ("date", "<", cutoff),
                ("product_id", "in", product_ids),
                ("origin_returned_move_id", "!=", False),
            ],
            ["origin_returned_move_id"],
        )
        trace_required_for = set(return_moves.origin_returned_move_id.ids)

        lines = MoveLine.search_fetch(
            [
                ("state", "=", "done"),
                ("company_id", "=", company.id),
                ("date", "<", cutoff),
                ("product_id", "in", product_ids),
                ("quantity_product_uom", "!=", 0),
                "|",
                ("location_id", "in", list(tracked_location_ids)),
                ("location_dest_id", "in", list(tracked_location_ids)),
            ],
            [
                "product_id",
                "date",
                "quantity_product_uom",
                "location_id",
                "location_dest_id",
                "move_id",
            ],
            order="product_id, date, id",
        )
        moves = lines.move_id
        moves.fetch([
            "origin_returned_move_id",
            "value",
            "is_in",
            "is_out",
            "restrict_partner_id",
            "product_id",
            "date",
            "move_line_ids",
        ])

        movement_traces = defaultdict(list)
        entry_traces = defaultdict(list)
        returned_offsets = defaultdict(float)
        cost_cache = {}
        sequence = 0

        def next_sequence():
            nonlocal sequence
            sequence += 1
            return sequence

        for line in lines:
            quantity = line.quantity_product_uom
            source_id = line.location_id.id
            destination_id = line.location_dest_id.id
            if quantity < 0:
                quantity = abs(quantity)
                source_id, destination_id = destination_id, source_id
            if quantity <= self._EPSILON or source_id == destination_id:
                continue

            source_tracked = source_id in tracked_location_ids
            destination_tracked = destination_id in tracked_location_ids
            if not source_tracked and not destination_tracked:
                continue

            move = line.move_id
            product_id = line.product_id.id
            event_date = self._local_date(line.date)
            fallback_cost = self._move_unit_cost(move, cutoff, company, cost_cache)
            original_id = move.origin_returned_move_id.id
            reference_portions = []
            reference_missing = quantity
            if original_id:
                original_trace = movement_traces.get(original_id) or entry_traces.get(original_id) or []
                reference_portions, reference_missing = self._slice_trace(
                    original_trace,
                    returned_offsets[original_id],
                    quantity,
                )
                returned_offsets[original_id] += quantity

            if source_tracked:
                source_state = inventories[(product_id, source_id)]
                consumed = self._consume_layers(
                    source_state,
                    quantity,
                    event_date,
                    fallback_cost,
                    [portion["uid"] for portion in reference_portions],
                    next_sequence,
                )
                if move.id in trace_required_for:
                    movement_traces[move.id].extend(self._copy_portion(portion) for portion in consumed)
                if destination_tracked:
                    self._add_portions(inventories[(product_id, destination_id)], consumed)
                continue

            # External -> tracked. A linked return restores the original layer's
            # age and cost. Any unmatched quantity uses the original move date,
            # never the return date, when an origin is available.
            incoming = [self._copy_portion(portion) for portion in reference_portions]
            if reference_missing > self._EPSILON:
                layer_date = event_date
                if original_id:
                    layer_date = self._local_date(move.origin_returned_move_id.date)
                new_sequence = next_sequence()
                incoming.append(self._new_portion(
                    "%s:%s:%s" % (move.id, line.id, new_sequence),
                    layer_date,
                    reference_missing,
                    fallback_cost,
                    new_sequence,
                ))
            self._add_portions(inventories[(product_id, destination_id)], incoming)
            if move.id in trace_required_for:
                entry_traces[move.id].extend(self._copy_portion(portion) for portion in incoming)

    def _simulate(self, wizard, products, cutoff):
        Location = self.env["stock.location"].with_context(active_test=False)
        tracked_locations = Location.search([
            ("usage", "in", self._TRACKED_USAGES),
            "|",
            ("company_id", "=", False),
            ("company_id", "=", wizard.company_id.id),
        ])
        tracked_location_ids = set(tracked_locations.ids)
        inventories = defaultdict(self._empty_location_state)

        batch_size = 200
        product_ids = products.ids
        for offset in range(0, len(product_ids), batch_size):
            self._simulate_product_batch(
                wizard,
                product_ids[offset:offset + batch_size],
                cutoff,
                tracked_location_ids,
                inventories,
            )
        return inventories

    def _filter_names(self, records, empty_label):
        if not records:
            return empty_label
        names = records.mapped("display_name")
        if len(names) <= 8:
            return ", ".join(names)
        return _("%(names)s and %(count)s more", names=", ".join(names[:8]), count=len(names) - 8)

    def _format_report_data(self, data, products_by_id):
        currency = self.env["res.currency"].browse(data["currency_id"])
        for line in data["lines"]:
            product = products_by_id[line["product_id"]]
            line["total_qty_display"] = formatLang(self.env, line["total_qty"], dp="Product Unit")
            line["total_value_display"] = format_amount(self.env, line["total_value"], currency)
            for bucket in line["buckets"]:
                bucket["quantity_display"] = formatLang(self.env, bucket["quantity"], dp="Product Unit")
                bucket["value_display"] = format_amount(self.env, bucket["value"], currency)

        totals = data["totals"]
        totals["quantity_display"] = formatLang(self.env, totals["quantity"], dp="Product Unit")
        totals["value_display"] = format_amount(self.env, totals["value"], currency)
        for bucket in totals["buckets"]:
            bucket["quantity_display"] = formatLang(self.env, bucket["quantity"], dp="Product Unit")
            bucket["value_display"] = format_amount(self.env, bucket["value"], currency)

    def _get_report_data(self, wizard):
        wizard.ensure_one()
        self._check_company_access(wizard)
        target_locations = self._get_target_locations(wizard)
        cutoff = self._cutoff_datetime(wizard)
        products, expected_balances = self._get_candidate_products(wizard, target_locations, cutoff)
        periods = self._build_periods(wizard.period_length, wizard.period_count)
        inventories = self._simulate(wizard, products, cutoff) if products else {}

        locations_by_id = {location.id: location for location in target_locations}
        products_by_id = {product.id: product for product in products}
        grouped = {}
        for (product_id, location_id), state in inventories.items():
            if location_id not in locations_by_id or product_id not in products_by_id:
                continue
            location = locations_by_id[location_id]
            warehouse = location.warehouse_id
            key = (product_id, warehouse.id or 0)
            if key not in grouped:
                grouped[key] = {
                    "product_id": product_id,
                    "warehouse_id": warehouse.id or False,
                    "warehouse": warehouse.display_name or _("Locations without warehouse"),
                    "buckets": [{"quantity": 0.0, "value": 0.0} for period in periods],
                }

            signed_layers = list(state["layers"])
            signed_layers += [dict(deficit, qty=-deficit["qty"]) for deficit in state["deficits"]]
            for layer in signed_layers:
                age_days = (wizard.report_date - layer["date"]).days
                bucket_index = self._bucket_index(age_days, wizard.period_length, wizard.period_count)
                grouped[key]["buckets"][bucket_index]["quantity"] += layer["qty"]
                grouped[key]["buckets"][bucket_index]["value"] += layer["qty"] * layer["unit_cost"]

        lines = []
        calculated_by_product = defaultdict(float)
        for (product_id, warehouse_id), row in grouped.items():
            product = products_by_id[product_id]
            total_qty = sum(bucket["quantity"] for bucket in row["buckets"])
            if product.uom_id.is_zero(total_qty):
                continue
            total_value = sum(bucket["value"] for bucket in row["buckets"])
            row.update({
                "product": product.name,
                "default_code": product.default_code or "",
                "uom": product.uom_id.display_name,
                "total_qty": total_qty,
                "total_value": total_value,
            })
            calculated_by_product[product_id] += total_qty
            lines.append(row)

        for product in products:
            expected = expected_balances.get(product.id, 0.0)
            calculated = calculated_by_product.get(product.id, 0.0)
            if not product.uom_id.is_zero(expected - calculated):
                raise UserError(_(
                    "Stock ageing reconciliation failed for %(product)s: expected %(expected)s, got %(calculated)s.",
                    product=product.display_name,
                    expected=expected,
                    calculated=calculated,
                ))

        lines.sort(key=lambda row: (row["warehouse"], row["product"], row["default_code"]))
        totals = {
            "quantity": sum(line["total_qty"] for line in lines),
            "value": sum(line["total_value"] for line in lines),
            "buckets": [],
        }
        for period in periods:
            totals["buckets"].append({
                "quantity": sum(line["buckets"][period["index"]]["quantity"] for line in lines),
                "value": sum(line["buckets"][period["index"]]["value"] for line in lines),
            })

        data = {
            "company": wizard.company_id.display_name,
            "company_id": wizard.company_id.id,
            "currency_id": wizard.company_id.currency_id.id,
            "currency": wizard.company_id.currency_id.name,
            "report_date": fields.Date.to_string(wizard.report_date),
            "period_length": wizard.period_length,
            "period_count": wizard.period_count,
            "periods": periods,
            "lines": lines,
            "totals": totals,
            "filters": {
                "filter_by_key": wizard.filter_by,
                "report_based_on_key": wizard.report_based_on,
                "filter_by": dict(wizard._fields["filter_by"]._description_selection(self.env)).get(
                    wizard.filter_by, wizard.filter_by
                ),
                "report_based_on": dict(
                    wizard._fields["report_based_on"]._description_selection(self.env)
                ).get(wizard.report_based_on, wizard.report_based_on),
                "warehouses": self._filter_names(
                    wizard.warehouse_ids if wizard.report_based_on == "warehouse" else self.env["stock.warehouse"],
                    _("All warehouses"),
                ),
                "locations": self._filter_names(
                    wizard.location_ids if wizard.report_based_on == "location" else self.env["stock.location"],
                    _("All internal locations"),
                ),
                "products": self._filter_names(
                    wizard.product_ids if wizard.filter_by == "product" else self.env["product.product"],
                    _("All storable products"),
                ),
                "categories": self._filter_names(
                    wizard.category_ids if wizard.filter_by == "category" else self.env["product.category"],
                    _("All product categories"),
                ),
            },
            "valuation_method": _(
                "Analytical FIFO. Outgoing quantities consume the oldest available layers. "
                "Internal transfers carry the original layer date and unit cost, and linked returns "
                "restore their original layer when it is identifiable. Each bucket value equals its "
                "remaining quantity multiplied by the incoming stock move's historical Odoo 19 unit "
                "value at the report cutoff. Negative stock is assigned to the bucket in which the "
                "deficit arose. This report does not post or alter inventory valuation."
            ),
            "generated_at": fields.Datetime.to_string(fields.Datetime.now()),
            "quantity_total_note": _("Grand quantities can mix different units of measure."),
        }
        self._format_report_data(data, products_by_id)
        return data
