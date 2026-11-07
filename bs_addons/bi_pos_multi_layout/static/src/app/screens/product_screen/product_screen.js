import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import { patch } from "@web/core/utils/patch";
import { onMounted, onPatched, useState } from "@odoo/owl";

patch(ProductScreen.prototype, {
    setup() {
        super.setup(...arguments);
        this.biLayoutState = useState({ padsVisible: true });
        onMounted(() => this.applyPadsVisibility());
        onPatched(() => this.applyPadsVisibility());
    },

    applyPadsVisibility() {
        if (!this.pos.config.button_layout) {
            return;
        }
        const visible = this.biLayoutState.padsVisible;
        document.querySelectorAll(".product-screen .leftpane > .pads").forEach((el) => {
            el.classList.toggle("d-none", !visible);
        });
    },

    click_actions() {
        this.biLayoutState.padsVisible = !this.biLayoutState.padsVisible;
        this.applyPadsVisibility();
    },
});
