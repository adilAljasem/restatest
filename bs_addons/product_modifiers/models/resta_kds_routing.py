from odoo import models


class PosOrderLine(models.Model):
    """On the Resta kitchen screen a modifier goes with its dish: same screen, shown under it."""
    _inherit = "pos.order.line"

    def _resta_modifier_parent(self):
        self.ensure_one()
        lines = self.order_id.lines
        if self.modifier_parent_uuid:
            parent = lines.filtered(lambda l: l.uuid == self.modifier_parent_uuid)[:1]
            if parent:
                return parent
        reference = self.parent_line_id
        if not reference:
            return self.browse()
        return lines.filtered(lambda l: l != self and (l.uuid == reference or str(l.id) == str(reference)))[:1]

    def _resta_kds_route_products(self):
        parent = self._resta_modifier_parent()
        if parent:
            return parent._resta_kds_route_products()
        return super()._resta_kds_route_products()

    def _resta_kds_parent_uuid(self):
        parent = self._resta_modifier_parent()
        return parent.uuid if parent else super()._resta_kds_parent_uuid()
