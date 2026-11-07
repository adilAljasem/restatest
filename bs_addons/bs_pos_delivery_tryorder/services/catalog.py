"""Modifier catalogue prices and branch-local availability for TryOrder."""

from ..models.tryorder_intake import TryOrderRequestError


class TryOrderModifierCatalog:
    def __init__(self, store, pricelist, products):
        self.store = store
        self.config = store.pos_config_id
        self.company = store.company_id
        self.pricelist = pricelist
        # The caller has authenticated the connection and selected its store.
        # Read stock only for that company's branch, including hidden POS extras.
        products = products.sudo().with_company(self.company).filtered(self.product_in_catalog)
        self.prices = pricelist._get_products_price(products, 1.0) if pricelist else {}
        self.stock = self._stock_available(products)
        self.branch_available = bool(
            store.active and self.config.active and not store.is_paused
            and (not store.work_time_enabled or store.is_in_delivery_work_time())
        )

    def product_in_catalog(self, product):
        return bool(
            product and product.active and product.product_tmpl_id.active
            and product.sale_ok and product.delivery_available
            and (not product.company_id or product.company_id == self.company)
        )

    def _stock_available(self, products):
        """Odoo stock never closes a TryOrder modifier.

        Restaurant stock is not received item by item, so on-hand quantities
        are routinely zero or negative. Only the explicit switches apply:
        Delivery Platforms, per-product Available, branch pause and work time.
        """
        return {product.id: True for product in products}

    def price(self, option):
        if option.price_mode == "fixed_extra":
            return float(option.extra_price or 0.0)
        if option.price_mode == "product_pricelist" and option.product_id:
            if not self.pricelist:
                raise TryOrderRequestError(
                    "Configure an active menu pricelist on this TryOrder connection.", 409,
                )
            return float(self.prices[option.product_id.id])
        return 0.0

    def available(self, option):
        if option.option_type == "instruction":
            return True
        return bool(self.branch_available and self.stock.get(option.product_id.id, False))
