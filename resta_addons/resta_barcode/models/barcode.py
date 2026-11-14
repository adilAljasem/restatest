"""Resta barcode app (Odoo Community): receive, transfer and count stock by scanning.

Every scan goes through the server, so two devices working on the same transfer stay in
sync and the stock rules of Odoo (reservations, backorders, lots) keep applying.
"""
from odoo import _, api, models
from odoo.exceptions import UserError
from odoo.tools import float_compare

OPERATIONS = [("incoming", "استلام بضاعة", "fa-truck"), ("internal", "تحويل بين المخازن", "fa-exchange"),
              ("outgoing", "صرف / تسليم", "fa-sign-out")]


class RestaBarcode(models.AbstractModel):
    _name = "resta.barcode"
    _description = "Resta barcode app"

    # ------------------------------------------------------------------ lookups
    @api.model
    def _find_product(self, code):
        """(product, quantity per scan) for a product or packaging barcode."""
        code = (code or "").strip()
        if not code:
            return self.env["product.product"], 0.0
        product = self.env["product.product"].search([("barcode", "=", code)], limit=1)
        if product:
            return product, 1.0
        packaging = self.env["product.uom"].search([("barcode", "=", code)], limit=1)
        if packaging:
            qty = packaging.uom_id._compute_quantity(1.0, packaging.product_id.uom_id)
            return packaging.product_id, qty
        product = self.env["product.product"].search([("default_code", "=", code)], limit=1)
        return product, 1.0 if product else 0.0

    @api.model
    def _types(self):
        return self.env["stock.picking.type"].search([("company_id", "in", self.env.companies.ids),
                                                      ("active", "=", True)])

    # ------------------------------------------------------------------ home
    @api.model
    def get_home(self):
        types = self._types()
        counts = {}
        for group in self.env["stock.picking"]._read_group(
                [("picking_type_id", "in", types.ids), ("state", "in", ("confirmed", "waiting", "assigned"))],
                ["picking_type_id"], ["__count"]):
            counts[group[0].id] = group[1]
        operations = []
        for code, label, icon in OPERATIONS:
            todo = sum(counts.get(t.id, 0) for t in types if t.code == code)
            operations.append({"code": code, "label": label, "icon": icon, "todo": todo})
        return {"operations": operations, "user": self.env.user.name, "company": self.env.company.name,
                "can_count": self.env.user.has_group("stock.group_stock_user")}

    @api.model
    def get_pickings(self, code, search=""):
        domain = [("picking_type_id", "in", self._types().filtered(lambda t: t.code == code).ids),
                  ("state", "in", ("confirmed", "waiting", "assigned"))]
        if search:
            domain += ["|", "|", ("name", "ilike", search), ("partner_id.name", "ilike", search),
                       ("origin", "ilike", search)]
        pickings = self.env["stock.picking"].search(domain, order="scheduled_date, id", limit=80)
        return [{"id": p.id, "name": p.name, "partner": p.partner_id.display_name or "", "origin": p.origin or "",
                 "date": str(p.scheduled_date.date()) if p.scheduled_date else "",
                 "from": p.location_id.display_name, "to": p.location_dest_id.display_name, "state": p.state,
                 "lines": len(p.move_ids)} for p in pickings]

    # ------------------------------------------------------------------ transfer
    def _picking_ui(self, picking):
        moves = []
        for m in picking.move_ids.filtered(lambda m: m.state != "cancel").sorted(lambda m: (m.picked, m.id)):
            done = m.quantity if m.picked else 0.0
            moves.append({
                "id": m.id, "product": m.product_id.display_name, "barcode": m.product_id.barcode or "",
                "uom": m.product_uom.name, "demand": m.product_uom_qty, "done": done,
                "complete": float_compare(done, m.product_uom_qty, precision_rounding=m.product_uom.rounding) >= 0,
                "tracking": m.product_id.tracking,
                "lots": [{"name": l.lot_id.name or l.lot_name, "qty": l.quantity}
                         for l in m.move_line_ids if (l.lot_id or l.lot_name) and m.picked],
            })
        return {"id": picking.id, "name": picking.name, "state": picking.state,
                "type": picking.picking_type_id.code, "type_name": picking.picking_type_id.display_name,
                "partner": picking.partner_id.display_name or "", "origin": picking.origin or "",
                "from": picking.location_id.display_name, "to": picking.location_dest_id.display_name,
                "moves": moves, "all_done": bool(moves) and all(m["complete"] for m in moves),
                "any_done": any(m["done"] for m in moves)}

    @api.model
    def get_picking(self, picking_id):
        picking = self.env["stock.picking"].browse(int(picking_id)).exists()
        if not picking:
            raise UserError(_("الحركة غير موجودة."))
        return self._picking_ui(picking)

    @api.model
    def open_by_barcode(self, code):
        """A scanned transfer reference (WH/IN/00012) opens it directly."""
        picking = self.env["stock.picking"].search([("name", "=", (code or "").strip()),
                                                    ("state", "not in", ("done", "cancel"))], limit=1)
        return picking.id or False

    def _add_on_move(self, move, qty, lot_name=None):
        if not move.picked:
            # first scan: start from zero, not from the reserved quantity
            move.write({"quantity": 0.0, "picked": True})
        if lot_name and move.product_id.tracking != "none":
            line = move.move_line_ids.filtered(lambda l: (l.lot_id.name or l.lot_name) == lot_name)[:1]
            if line:
                line.quantity += qty
            else:
                vals = {"move_id": move.id, "product_id": move.product_id.id, "quantity": qty,
                        "product_uom_id": move.product_uom.id, "location_id": move.location_id.id,
                        "location_dest_id": move.location_dest_id.id, "picking_id": move.picking_id.id,
                        "picked": True}
                lot = self.env["stock.lot"].search([("product_id", "=", move.product_id.id), ("name", "=", lot_name)],
                                                   limit=1)
                if lot:
                    vals["lot_id"] = lot.id
                elif move.picking_type_id.use_create_lots:
                    vals["lot_name"] = lot_name
                else:
                    raise UserError(_("رقم التشغيلة %s غير موجود.", lot_name))
                self.env["stock.move.line"].create(vals)
            move.picked = True
        else:
            move.quantity = move.quantity + qty
            move.picked = True

    @api.model
    def scan_picking(self, picking_id, code, lot_name=None):
        picking = self.env["stock.picking"].browse(int(picking_id)).exists()
        if not picking or picking.state in ("done", "cancel"):
            raise UserError(_("الحركة منتهية."))
        product, qty = self._find_product(code)
        if not product:
            return {"ok": False, "message": _("الباركود %s غير معروف.", code), "picking": self._picking_ui(picking)}
        if product.tracking != "none" and not lot_name:
            return {"ok": False, "need_lot": True, "product": product.display_name, "code": code,
                    "message": _("أدخل رقم التشغيلة / الصلاحية لـ %s", product.display_name),
                    "picking": self._picking_ui(picking)}
        moves = picking.move_ids.filtered(lambda m: m.product_id == product and m.state != "cancel")
        move = moves.filtered(lambda m: float_compare(m.quantity if m.picked else 0.0, m.product_uom_qty,
                                                      precision_rounding=m.product_uom.rounding) < 0)[:1] or moves[:1]
        if not move:
            if picking.picking_type_id.code != "incoming":
                return {"ok": False, "message": _("%s ليس في هذه الحركة.", product.display_name),
                        "picking": self._picking_ui(picking)}
            move = self.env["stock.move"].create({
                "product_id": product.id, "product_uom_qty": 0.0, "product_uom": product.uom_id.id,
                "picking_id": picking.id, "location_id": picking.location_id.id,
                "location_dest_id": picking.location_dest_id.id, "company_id": picking.company_id.id,
            })
            move._action_confirm()
        self._add_on_move(move, qty, lot_name)
        ui = self._picking_ui(picking)
        row = next(m for m in ui["moves"] if m["id"] == move.id)
        over = row["done"] > row["demand"] and row["demand"]
        return {"ok": True, "move_id": move.id, "picking": ui,
                "message": _("%(p)s: %(d)s من %(t)s", p=product.display_name, d=row["done"], t=row["demand"]),
                "warning": bool(over)}

    @api.model
    def set_quantity(self, move_id, qty):
        move = self.env["stock.move"].browse(int(move_id)).exists()
        if not move or move.picking_id.state in ("done", "cancel"):
            raise UserError(_("الحركة منتهية."))
        qty = max(float(qty or 0.0), 0.0)
        move.write({"quantity": qty, "picked": bool(qty)})
        return self._picking_ui(move.picking_id)

    @api.model
    def fill_all(self, picking_id):
        """«Everything as expected»: every line gets its full demand."""
        picking = self.env["stock.picking"].browse(int(picking_id))
        for move in picking.move_ids.filtered(lambda m: m.state != "cancel" and m.product_id.tracking == "none"):
            move.write({"quantity": move.product_uom_qty, "picked": True})
        return self._picking_ui(picking)

    @api.model
    def validate(self, picking_id, backorder=True):
        picking = self.env["stock.picking"].browse(int(picking_id)).exists()
        if not picking:
            raise UserError(_("الحركة غير موجودة."))
        scanned = picking.move_ids.filtered(lambda m: m.picked and m.quantity)
        if not scanned:
            raise UserError(_("لم يُمسح أي صنف بعد."))
        # lines never scanned do not leave the warehouse: they go to the backorder (or are dropped)
        picking.move_ids.filtered(lambda m: not m.picked and m.state not in ("done", "cancel")).write(
            {"quantity": 0.0})
        ctx = {"skip_backorder": True}
        if not backorder:
            ctx["picking_ids_not_to_backorder"] = picking.ids
        res = picking.with_context(**ctx).button_validate()
        if picking.state != "done":
            return {"ok": False, "message": _("لم تكتمل: %s", (res or {}).get("name", "")) if isinstance(res, dict)
                    else _("لم تكتمل الحركة.")}
        backorders = self.env["stock.picking"].search([("backorder_id", "=", picking.id)])
        return {"ok": True, "message": _("تم اعتماد %s", picking.name),
                "backorder": backorders[:1].name or False}

    # ------------------------------------------------------------------ inventory count
    @api.model
    def get_locations(self):
        locations = self.env["stock.location"].search([("usage", "=", "internal"),
                                                       ("company_id", "in", self.env.companies.ids)])
        return [{"id": l.id, "name": l.complete_name, "barcode": l.barcode or ""} for l in locations]

    @api.model
    def find_location(self, code):
        location = self.env["stock.location"].search([("barcode", "=", (code or "").strip()),
                                                      ("usage", "=", "internal")], limit=1)
        return location.id or False

    def _count_ui(self, location):
        quants = self.env["stock.quant"].search([("location_id", "=", location.id),
                                                ("inventory_quantity_set", "=", True)], order="write_date desc")
        return {"location": {"id": location.id, "name": location.complete_name},
                "lines": [{"id": q.id, "product": q.product_id.display_name, "uom": q.product_uom_id.name,
                           "counted": q.inventory_quantity, "system": q.quantity,
                           "lot": q.lot_id.name or "", "diff": q.inventory_quantity - q.quantity} for q in quants]}

    @api.model
    def get_count(self, location_id):
        return self._count_ui(self.env["stock.location"].browse(int(location_id)))

    @api.model
    def scan_count(self, location_id, code, lot_name=None, qty=None):
        location = self.env["stock.location"].browse(int(location_id))
        product, per_scan = self._find_product(code)
        if not product:
            return {"ok": False, "message": _("الباركود %s غير معروف.", code), **self._count_ui(location)}
        if product.tracking != "none" and not lot_name:
            return {"ok": False, "need_lot": True, "code": code, "product": product.display_name,
                    "message": _("أدخل رقم التشغيلة لـ %s", product.display_name), **self._count_ui(location)}
        lot = self.env["stock.lot"]
        if lot_name:
            lot = lot.search([("product_id", "=", product.id), ("name", "=", lot_name)], limit=1) or \
                lot.create({"product_id": product.id, "name": lot_name, "company_id": location.company_id.id})
        Quant = self.env["stock.quant"].with_context(inventory_mode=True)
        quant = Quant.search([("product_id", "=", product.id), ("location_id", "=", location.id),
                              ("lot_id", "=", lot.id or False)], limit=1)
        if not quant:
            quant = Quant.create({"product_id": product.id, "location_id": location.id, "lot_id": lot.id or False,
                                  "inventory_quantity": 0.0})
        start = quant.inventory_quantity if quant.inventory_quantity_set else 0.0
        new_qty = float(qty) if qty not in (None, False, "") else start + per_scan
        quant.with_context(inventory_mode=True).write({"inventory_quantity": new_qty, "inventory_quantity_set": True,
                                                      "user_id": self.env.user.id})
        return {"ok": True, "message": _("%(p)s: %(q)s", p=product.display_name, q=new_qty),
                **self._count_ui(location)}

    @api.model
    def set_count(self, quant_id, qty):
        quant = self.env["stock.quant"].browse(int(quant_id))
        quant.with_context(inventory_mode=True).write({"inventory_quantity": max(float(qty), 0.0),
                                                      "inventory_quantity_set": True, "user_id": self.env.user.id})
        return self._count_ui(quant.location_id)

    @api.model
    def apply_count(self, location_id):
        location = self.env["stock.location"].browse(int(location_id))
        quants = self.env["stock.quant"].search([("location_id", "=", location.id),
                                                ("inventory_quantity_set", "=", True)])
        if not quants:
            raise UserError(_("لم يُعدّ أي صنف."))
        quants.with_context(inventory_name=_("جرد %s", location.complete_name)).action_apply_inventory()
        return {"ok": True, "message": _("تم اعتماد الجرد (%s صنف).", len(quants))}

    @api.model
    def clear_count(self, quant_id):
        quant = self.env["stock.quant"].browse(int(quant_id))
        location = quant.location_id
        quant.action_set_inventory_quantity_to_zero() if hasattr(quant, "action_set_inventory_quantity_to_zero") \
            else quant.write({"inventory_quantity_set": False})
        return self._count_ui(location)
