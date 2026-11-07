import { describe, expect, test } from "@odoo/hoot";
import { renderToElement } from "@web/core/utils/render";
import { prepareBlendKitchenReceipt } from "@pino_pos_receipt/kitchen_data";

function renderKitchenLine(line) {
    return renderToElement("pino_pos_receipt.KotLine", {
        line: {
            quantity: 1,
            basic_name: "إضافة صدر دجاج مشوي",
            attribute_value_names: [],
            note: "",
            customer_note: "",
            ...line,
        },
        p: { showLatin: false },
    });
}

describe("Pino kitchen receipt", () => {
    test("Blend snapshots render the same Pino kitchen template and subordinate lines", () => {
        const data = prepareBlendKitchenReceipt({
            tracking_number: "41003",
            blend_kitchen: { version: 2, reference: "hunger: 123", order_time: "11:02" },
            changes: { title: "NEW", data: [{
                uuid: "child", quantity: 1, basic_name: "Chicken",
                product_name_ar: "دجاج", pino_latin: "Chicken", isModifierChild: true,
                attribute_value_names: [], customer_note: "No salt",
            }] },
        }, { pino_kitchen_design: true, pino_show_latin_name: true });
        const element = renderToElement("point_of_sale.OrderChangeReceipt", { data });
        expect(element.classList.contains("pino-kot")).toBe(true);
        expect(element.textContent).toInclude("003");
        expect(element.textContent).toInclude("hunger: 123");
        expect(element.textContent).toInclude("دجاج");
        expect(element.textContent).toInclude("Chicken");
        expect(element.querySelector(".pino-kot-line-modifier")).not.toBe(null);
    });
    test("modifier lines are visually grouped under their parent", () => {
        const parent = renderKitchenLine({
            basic_name: "باستا ألفريدو",
            isModifierChild: false,
        });
        const modifier = renderKitchenLine({
            isModifierChild: true,
            modifier_parent_uuid: "parent-line",
        });

        expect(parent.classList.contains("pino-kot-line-sub")).toBe(false);
        expect(parent.classList.contains("pino-kot-line-modifier")).toBe(false);
        expect(modifier.classList.contains("pino-kot-line-sub")).toBe(true);
        expect(modifier.classList.contains("pino-kot-line-modifier")).toBe(true);
        expect(modifier.querySelector(".pino-kot-name-ar").textContent).toBe(
            "إضافة صدر دجاج مشوي"
        );
    });

    test("combo children keep the existing subordinate style", () => {
        const comboChild = renderKitchenLine({
            combo_parent_uuid: "combo-parent",
            isModifierChild: false,
        });

        expect(comboChild.classList.contains("pino-kot-line-sub")).toBe(true);
        expect(comboChild.classList.contains("pino-kot-line-modifier")).toBe(false);
    });
});
