import { describe, expect, test } from "@odoo/hoot";
import { definePosModels } from "@point_of_sale/../tests/unit/data/generate_model_definitions";
import { setupPosEnv } from "@point_of_sale/../tests/unit/utils";
import { PartnerList } from "@point_of_sale/app/screens/partner_list/partner_list";
import { patchWithCleanup } from "@web/../tests/web_test_helpers";
import { getPartnerCreationDefaults } from "../../src/app/partner_search_autofill";

definePosModels();

describe("POS customer search autofill", () => {
    test("uses a phone search as both the customer name and phone", () => {
        expect(getPartnerCreationDefaults("  +966 50-123-4567  ")).toEqual({
            default_name: "+966 50-123-4567",
            default_phone: "+966 50-123-4567",
        });
        expect(getPartnerCreationDefaults("٠٥٠١٢٣٤٥٦٧")).toEqual({
            default_name: "٠٥٠١٢٣٤٥٦٧",
            default_phone: "٠٥٠١٢٣٤٥٦٧",
        });
    });

    test("uses a name search only as the customer name", () => {
        expect(getPartnerCreationDefaults("  Ahmed Ali  ")).toEqual({
            default_name: "Ahmed Ali",
        });
        expect(getPartnerCreationDefaults("Customer 123")).toEqual({
            default_name: "Customer 123",
        });
    });

    test("does not set defaults for an empty search", () => {
        expect(getPartnerCreationDefaults("   ")).toEqual({});
        expect(getPartnerCreationDefaults(null)).toEqual({});
    });

    test("passes the phone defaults to the real POS partner creation flow", async () => {
        const store = await setupPosEnv();
        let creationContext;
        patchWithCleanup(store, {
            async editPartner() {
                creationContext = this.editPartnerContext();
                return undefined;
            },
        });
        const partnerList = {
            pos: store,
            state: { query: "0501234567" },
        };

        await PartnerList.prototype.editPartner.call(partnerList);

        expect(creationContext).toEqual({
            default_name: "0501234567",
            default_phone: "0501234567",
        });
        expect(store.editPartnerContext()).toEqual({});
    });

    test("never applies the search term while editing an existing customer", async () => {
        const store = await setupPosEnv();
        let editContext;
        const existingPartner = store.models["res.partner"].get(1);
        patchWithCleanup(store, {
            async editPartner() {
                editContext = this.editPartnerContext();
                return undefined;
            },
        });
        const partnerList = {
            pos: store,
            state: { query: "0501234567" },
        };

        await PartnerList.prototype.editPartner.call(partnerList, existingPartner);

        expect(editContext).toEqual({});
    });
});
