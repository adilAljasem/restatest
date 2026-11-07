import { expect, test } from "@odoo/hoot";
import { definePosModels } from "@point_of_sale/../tests/unit/data/generate_model_definitions";
import { getFilledOrder, setupPosEnv } from "@point_of_sale/../tests/unit/utils";
import "@bs_pos_payment_clearing/app/models/pos_order";

definePosModels();

test("the last split payment method sets the order customer", async () => {
    const store = await setupPosEnv();
    const order = await getFilledOrder(store);
    const [firstPartner, lastPartner] = store.models["res.partner"].getAll().slice(0, 2);
    const cash = store.models["pos.payment.method"].get(1);
    const card = store.models["pos.payment.method"].get(2);
    const other = store.models["pos.payment.method"].get(3);

    cash.split_transactions = true;
    cash.partner_id = firstPartner;
    card.split_transactions = true;
    card.partner_id = lastPartner;
    other.split_transactions = false;

    expect(order.addPaymentline(cash).status).toBe(true);
    expect(order.getPartner()).toBe(firstPartner);
    expect(order.addPaymentline(card).status).toBe(true);
    expect(order.getPartner()).toBe(lastPartner);
    expect(order.addPaymentline(other).status).toBe(true);
    expect(order.getPartner()).toBe(lastPartner);
});

test("customer pricing is applied before the payment amount is chosen", async () => {
    const store = await setupPosEnv();
    store.config.use_presets = false;
    const order = await getFilledOrder(store);
    const originalTotal = order.totalDue;
    const pricelist = store.models["product.pricelist"].get(3);
    const partner = store.models["res.partner"].getAll()[0];
    const cash = store.models["pos.payment.method"].get(1);

    store.config.update({
        available_pricelist_ids: [...store.config.available_pricelist_ids, pricelist],
    });
    partner.property_product_pricelist = pricelist;
    cash.split_transactions = true;
    cash.partner_id = partner;

    const result = order.addPaymentline(cash);
    expect(order.getPartner()).toBe(partner);
    expect(order.totalDue).not.toBe(originalTotal);
    expect(result.data.amount).toBe(order.totalDue);
});
