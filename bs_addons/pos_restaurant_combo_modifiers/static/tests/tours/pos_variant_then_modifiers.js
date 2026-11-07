import * as Chrome from "@point_of_sale/../tests/pos/tours/utils/chrome_util";
import * as Dialog from "@point_of_sale/../tests/generic_helpers/dialog_util";
import * as ProductScreen from "@point_of_sale/../tests/pos/tours/utils/product_screen_util";
import * as ProductConfigurator from "@point_of_sale/../tests/pos/tours/utils/product_configurator_util";
import * as FloorScreen from "@pos_restaurant/../tests/tours/utils/floor_screen_util";
import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("pos_variant_then_modifiers", {
    steps: () => [
        Chrome.startPoS(),
        Dialog.confirm("Open Register"),
        FloorScreen.clickFloor("Modifier UAT Floor"),
        FloorScreen.clickTable("5"),
        ...ProductScreen.clickDisplayedProduct("UAT Variant Meal"),
        ...ProductConfigurator.pickRadio("Large"),
        Dialog.confirm("Add"),
        {
            content: "modifier wizard opens after choosing the variant",
            trigger: ".o_pcm_root_modifiers .o_pcm_group_card:contains('UAT Variant Meal Extras')",
        },
        {
            content: "select the variant meal modifier",
            trigger: ".o_pcm_root_modifiers .o_pcm_choice button:contains('Extra Cheese')",
            run: "click",
        },
        Dialog.confirm("Add to order"),
        {
            content: "chosen variant is the order parent",
            trigger: ".orderline.o_pcm_modifier_parent:contains('UAT Variant Meal')",
        },
        {
            content: "selected modifier is attached to the variant",
            trigger:
                ".order-container .orderline.o_pcm_modifier_parent:contains('UAT Variant Meal') + .orderline.o_pcm_modifier_line:contains('UAT Extra Cheese')",
        },
        Chrome.endTour(),
    ].flat(),
});
