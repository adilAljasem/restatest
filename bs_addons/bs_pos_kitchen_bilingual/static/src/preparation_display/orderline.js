import { Orderline } from "@pos_enterprise/app/components/orderline/orderline";
import { patch } from "@web/core/utils/patch";
import {
    buildPreparationAttributeData,
    formatBilingualText,
} from "@bs_pos_kitchen_bilingual/utils/bilingual_names";

patch(Orderline.prototype, {
    get preparation_line() {
        const preparationLine = super.preparation_line;
        const product = preparationLine?.product_id;
        if (product) {
            product.display_name = formatBilingualText(
                product.kitchen_name_ar,
                product.kitchen_name_en,
                product.display_name
            );
        }
        return preparationLine;
    },

    get attributeData() {
        const customValues =
            this.prepDisplay.data.models["product.attribute.custom.value"] || [];
        return buildPreparationAttributeData(this.preparation_line, customValues);
    },
});

