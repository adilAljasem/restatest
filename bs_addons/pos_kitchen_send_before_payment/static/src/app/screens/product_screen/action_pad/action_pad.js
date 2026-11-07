import { patch } from "@web/core/utils/patch";
import { ActionpadWidget } from "@point_of_sale/app/screens/product_screen/action_pad/action_pad";
import { useTrackedAsync } from "@point_of_sale/app/hooks/hooks";

patch(ActionpadWidget.prototype, {
    setup() {
        super.setup();
        this.doKitchenSubmit = useTrackedAsync(() => this.pos.kitchenSubmitOrder());
    },
    get showKitchenSendButton() {
        const order = this.currentOrder;
        return Boolean(
            this.pos.kitchenSendEnabled &&
                this.pos.router.state.current === "ProductScreen" &&
                order &&
                !order.isRefund &&
                !order.isEmpty()
        );
    },
    get kitchenCategoryCount() {
        return this.pos.getKitchenCategoryCount(this.currentOrder);
    },
});
