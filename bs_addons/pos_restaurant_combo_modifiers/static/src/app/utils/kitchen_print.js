/** @odoo-module **/

export function kitchenInstructionSnapshot(data) {
    if (!Array.isArray(data?.instructions)) {
        return [];
    }
    return data.instructions.flatMap((instruction, index) => {
        const name = instruction?.kitchen_name || instruction?.name;
        const quantity = Number(instruction?.quantity);
        if (!name || !Number.isFinite(quantity) || quantity <= 0) {
            return [];
        }
        return [{
            key: `instruction:${instruction.option_id || index}`,
            kind: "instruction",
            name,
            operation: instruction.operation || "note",
            quantity,
        }];
    });
}

export function kitchenProductSnapshot(lines) {
    const products = new Map();
    for (const line of lines) {
        const optionId = line.modifier_option_id?.id || line.modifier_option_id;
        const quantity = Number(line.qty ?? line.quantity);
        if (!optionId || !Number.isFinite(quantity) || quantity <= 0) {
            continue;
        }
        const key = `option:${optionId}`;
        if (!products.has(key)) {
            products.set(key, {
                key,
                kind: "product",
                name: line.modifier_option_name || line.modifier_name || line.product_id?.name,
                operation: line.modifier_option_id?.operation || line.modifier_operation || "add",
                quantity: 0,
            });
        }
        const product = products.get(key);
        product.quantity = Math.round((product.quantity + quantity) * 1000) / 1000;
    }
    return [...products.values()];
}

export function changedKitchenInstructions(previous, current) {
    const previousByKey = new Map(previous.map((entry) => [entry.key, entry]));
    const currentByKey = new Map(current.map((entry) => [entry.key, entry]));
    const result = [];
    for (const entry of current) {
        const old = previousByKey.get(entry.key);
        if (!old) {
            result.push({ ...entry, change_status: "added" });
        } else if (old.name !== entry.name || old.quantity !== entry.quantity ||
                   old.operation !== entry.operation) {
            result.push({ ...entry, change_status: "updated" });
        }
    }
    for (const entry of previous) {
        if (!currentByKey.has(entry.key)) {
            result.push({ ...entry, change_status: "removed" });
        }
    }
    return result;
}

export function nestKitchenModifierChanges(rows, parentRowsByUuid, instructionsByUuid) {
    const result = rows
        .filter((row) => !row.modifier_parent_uuid)
        .map((row) => ({ ...row, modifier_print_details: [...(row.modifier_print_details || [])] }));
    const parentByUuid = new Map(result.map((row) => [row.uuid, row]));
    for (const row of rows.filter((item) => item.modifier_parent_uuid)) {
        let parent = parentByUuid.get(row.modifier_parent_uuid);
        if (!parent) {
            const parentData = parentRowsByUuid[row.modifier_parent_uuid];
            if (!parentData) {
                result.push(row);
                continue;
            }
            parent = { ...parentData, modifier_print_details: [] };
            result.push(parent);
            parentByUuid.set(parent.uuid, parent);
        }
        const key = `option:${row.modifier_option_id || row.uuid}`;
        let detail = parent.modifier_print_details.find((item) => item.key === key);
        if (!detail) {
            detail = {
                key,
                kind: "product",
                name: row.modifier_name || row.basic_name,
                operation: row.modifier_operation || "add",
                quantity: 0,
            };
            parent.modifier_print_details.push(detail);
        }
        detail.quantity = Math.round((detail.quantity + Math.abs(row.quantity)) * 1000) / 1000;
    }
    for (const parent of result) {
        parent.modifier_print_details?.push(...(instructionsByUuid[parent.uuid] || []));
    }
    return result;
}
