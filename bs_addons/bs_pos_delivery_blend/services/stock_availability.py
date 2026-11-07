"""Branch-local availability for Blend.

Odoo stock never closes an item or modifier on Blend: restaurant stock is
not received item by item, so on-hand quantities are routinely zero or
negative. Availability is controlled only by the explicit switches (Delivery
Platforms on the product, Available per product option, branch pause and
schedule, and Blend's own availability events).
"""


class BlendStockAvailabilityService:
    def __init__(self, env):
        self.env = env

    def stock_available(self, templates, config):
        return {product.id: True for product in templates}
