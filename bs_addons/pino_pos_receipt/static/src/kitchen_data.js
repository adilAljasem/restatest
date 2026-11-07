/** Shared presentation data for cashier tickets and durable Blend snapshots. */
export function buildPinoKitchenData(config, data, now = new Date()) {
    const number = String(data.tracking_number ?? '').trim();
    const clock = `${now.getHours() % 12 || 12}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')} ${now.getHours() < 12 ? 'ص' : 'م'}`;
    return {
        enabled: Boolean(config.pino_kitchen_design),
        showLatin: Boolean(config.pino_show_latin_name),
        title: config.pino_kitchen_title || 'المطبخ – KITCHEN',
        trackingNumber: number ? number.slice(-3).padStart(3, '0') : '',
        presetName: data.preset_name || '',
        tableNumber: data.table_number || '',
        orderTime: data.order_time || clock,
        printTime: clock,
        cashier: data.employee_name || '',
        reprint: Boolean(data.reprint),
        statusLabel: '',
        totalQty: 0,
    };
}

export function prepareBlendKitchenReceipt(data, config, now = new Date()) {
    if (!data.blend_kitchen || !config.pino_kitchen_design) {
        return data;
    }
    const result = structuredClone(data);
    result.pino = buildPinoKitchenData(config, {
        ...result,
        order_time: result.blend_kitchen.order_time,
        table_number: result.blend_kitchen.table_number,
    }, now);
    result.pino.blendReference = result.blend_kitchen.reference;
    result.general_customer_note = result.blend_kitchen.general_note || '';
    const lines = result.changes.data;
    const byUuid = new Map(lines.map(line => [line.uuid, line]));
    const visited = new Set();
    const ordered = [];
    const parent = line => line.modifier_parent_uuid || line.combo_parent_uuid;
    const append = line => {
        if (visited.has(line)) {
            return;
        }
        visited.add(line);
        line.basic_name = line.product_name_ar || line.basic_name;
        if ('blend_line_customer_note' in line) {
            line.customer_note = line.blend_line_customer_note;
        }
        ordered.push(line);
        for (const child of lines) {
            if (line.uuid && parent(child) === line.uuid) {
                append(child);
            }
        }
    };
    lines.filter(line => !byUuid.has(parent(line))).forEach(append);
    lines.forEach(append);
    result.changes.data = ordered;
    return result;
}
