import * as Chrome from "@point_of_sale/../tests/pos/tours/utils/chrome_util";
import * as Dialog from "@point_of_sale/../tests/generic_helpers/dialog_util";
import * as ProductScreen from "@point_of_sale/../tests/pos/tours/utils/product_screen_util";
import * as RestaurantProductScreen from "@pos_restaurant/../tests/tours/utils/product_screen_util";
import * as FloorScreen from "@pos_restaurant/../tests/tours/utils/floor_screen_util";
import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("pos_combo_modifier_send_to_kitchen", {
    steps: () => [
        Chrome.startPoS(),
        Dialog.confirm("Open Register"),
        FloorScreen.clickFloor("Modifier UAT Floor"),
        FloorScreen.clickTable("5"),
        ...ProductScreen.clickDisplayedProduct("UAT Burger"),
        {
            content: "open the instruction group",
            trigger: "section:has(strong:contains('UAT Instructions')) > button",
            run: "click",
        },
        {
            content: "select no onion",
            trigger: ".o_pcm_choice button:contains('No Onion')",
            run: "click",
        },
        Dialog.confirm("Add to order"),
        ...RestaurantProductScreen.clickOrderButton(),
    ].flat(),
});
