/** @odoo-module **/

import { Order } from "@pos_enterprise/app/components/order/order";
import { PosPreparationState } from "@pos_enterprise/app/models/pos_preparation_state";
import { patch } from "@web/core/utils/patch";

function groupModifierStates(states) {
    const stateByLineUuid = new Map(
        states
            .filter((state) => state.prep_line_id.pos_order_line_uuid)
            .map((state) => [state.prep_line_id.pos_order_line_uuid, state])
    );
    const childrenByParent = new Map();

    for (const state of states) {
        const parentUuid = state.prep_line_id.modifier_parent_uuid;
        if (!parentUuid || !stateByLineUuid.has(parentUuid)) {
            continue;
        }
        if (!childrenByParent.has(parentUuid)) {
            childrenByParent.set(parentUuid, []);
        }
        childrenByParent.get(parentUuid).push(state);
    }

    const groupedStates = [];
    const addedLineIds = new Set();
    const appendStateAndChildren = (state) => {
        const line = state.prep_line_id;
        if (addedLineIds.has(line.id)) {
            return;
        }
        addedLineIds.add(line.id);
        groupedStates.push(state);
        for (const child of childrenByParent.get(line.pos_order_line_uuid) || []) {
            appendStateAndChildren(child);
        }
    };

    for (const state of states) {
        const line = state.prep_line_id;
        const hasVisibleParent =
            line.modifier_parent_uuid && stateByLineUuid.has(line.modifier_parent_uuid);
        if (!hasVisibleParent) {
            appendStateAndChildren(state);
        }
    }
    for (const state of states) {
        appendStateAndChildren(state);
    }
    return groupedStates;
}

patch(PosPreparationState.prototype, {
    get categories() {
        if (this.prep_line_id.routes_by_parent) {
            return this.prep_line_id.routing_category_ids || [];
        }
        return super.categories;
    },
});

patch(Order.prototype, {
    get orderlines() {
        return groupModifierStates(super.orderlines);
    },
});
