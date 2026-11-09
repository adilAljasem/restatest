import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("resta_growth_test_owner_badge", {
    steps: () => [
        { trigger: ".o_growth_journey .o_growth_road" },
        { trigger: ".o_growth_btn", run: "click" },
        { trigger: ".o_growth_panel .o_growth_conds .o_growth_cond" },
    ],
});

registry.category("web_tour.tours").add("resta_growth_test_no_badge", {
    steps: () => [
        { trigger: ".o_main_navbar, .o_web_client .o_navbar, .o_web_client" },
        {
            trigger: "body",
            run() {
                if (document.querySelector(".o_growth_btn")) {
                    throw new Error("the growth badge must not be visible to a cashier");
                }
            },
        },
    ],
});
