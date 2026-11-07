/** @odoo-module **/

import { ProductListPage } from "@pos_self_order/app/pages/product_list_page/product_list_page";
import { ProductPage } from "@pos_self_order/app/pages/product_page/product_page";
import { patch } from "@web/core/utils/patch";
import {
    filterProductCatalog,
    isVisibleInProductCatalog,
} from "@pos_hide_products/shared/product_visibility";

patch(ProductListPage.prototype, {
    getProducts(category) {
        return filterProductCatalog(super.getProducts(...arguments));
    },
});

patch(ProductPage.prototype, {
    isProductAvailable() {
        return (
            super.isProductAvailable(...arguments) &&
            isVisibleInProductCatalog(this.props.productTemplate)
        );
    },
});
