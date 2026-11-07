"""Use the product Blend intake already resolved at the Core line boundary."""

from odoo import models

from odoo.addons.bs_pos_delivery_core.exceptions import DeliveryBusinessError


class BlendCoreOrderService(models.AbstractModel):
    _inherit = "bs.delivery.order.service"

    def _line_values(self, connection, store, line, sequence):
        """Fall back to the Blend-resolved product when no Core mapping exists.

        Core only resolves explicit product mappings, while Blend intake also
        accepts ``blend_external_id``, modifier options, combo items and Odoo
        IDs (the IDs exposed by ``/api/menu``). The intake records the product
        it validated per line in ``bs_blend_resolved_products``; that context
        is only set server-side by the authenticated Blend intake.
        """
        resolved = self.env.context.get("bs_blend_resolved_products") or {}
        product_id = resolved.get(line.get("external_line_id"))
        if connection.provider_id.code != "blend" or not product_id:
            return super()._line_values(connection, store, line, sequence)
        try:
            return super()._line_values(connection, store, line, sequence)
        except DeliveryBusinessError as error:
            if error.code != "product_not_found":
                raise
        product = self.env["product.product"].sudo().browse(product_id).exists()
        if not product:
            raise DeliveryBusinessError(
                "The delivery product identifier is unknown.", code="product_not_found",
            )
        return {
            "sequence": sequence * 10,
            "external_line_id": line["external_line_id"],
            "external_product_id": line["external_product_id"],
            "external_sku": line["external_product_id"],
            "name": line["name"],
            "product_id": product.id,
            "quantity": line["quantity"],
            "price_unit": line["price_unit"],
            "discount_amount": line["discount_amount"],
            "vat_amount": line["vat_amount"],
            "price_subtotal": line["price_subtotal"],
            "customer_note": line.get("note"),
        }

