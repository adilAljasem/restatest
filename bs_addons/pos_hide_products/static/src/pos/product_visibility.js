/** @odoo-module **/

import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { patch } from "@web/core/utils/patch";
import {
    filterProductCatalog,
    isVisibleInProductCatalog,
} from "@pos_hide_products/shared/product_visibility";

patch(PosStore.prototype, {
    filterExcludedProducts(products) {
        return super.filterExcludedProducts(filterProductCatalog(products));
    },
});

patch(ProductScreen.prototype, {
    async _getProductByBarcode(code) {
        const product = await super._getProductByBarcode(...arguments);
        return isVisibleInProductCatalog(product?.product_tmpl_id) ? product : undefined;
    },
});
