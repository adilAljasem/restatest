import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("BilingualPreparationDisplayTour", {
    steps: () => [
        {
            content: "Arabic and English product names are visible on the preparation display",
            trigger:
                '.o_pdis_product-name:contains("صينية مستندات"):contains("Letter Tray")',
        },
    ],
});

