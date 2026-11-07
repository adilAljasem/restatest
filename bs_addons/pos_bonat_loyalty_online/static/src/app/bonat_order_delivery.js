/** @odoo-module **/

function orderEventDate(order) {
    const value = order.date_order;
    let millis = NaN;
    if (typeof value?.toMillis === "function") {
        millis = value.toMillis();
    } else if (value instanceof Date) {
        millis = value.getTime();
    } else if (typeof value === "string") {
        // Odoo's server datetime format is a timezone-free UTC SQL string.
        millis = Date.parse(value.includes("T") ? value : value.replace(" ", "T") + "Z");
    }
    return Number.isFinite(millis) ? new Date(millis) : new Date();
}

// The order event is built only after POS sync has assigned a database ID.
export function buildBonatOrderPayload(order, pos) {
    const lines = order.getOrderlines();
    const config = order.config_id || pos.config;
    const session = order.session_id || pos.session;
    const branchName = config.name || pos.config.name;
    const sessionId = session.id || pos.session.id;
    const sessionName = session.display_name || session.name ||
        pos.session.display_name || pos.session.name;
    const customer = order.getPartner() || {};
    let customerPhone = "";
    let dialCode = "";
    if (customer.phone) {
        const digits = customer.phone.replace(/\D/g, "");
        if (digits.length === 12) {
            customerPhone = "9665" + digits.slice(4);
            dialCode = "9665";
        } else if (digits.length === 10) {
            customerPhone = "05" + digits.slice(2);
            dialCode = "05";
        } else if (digits.length === 9) {
            customerPhone = "5" + digits.slice(1);
            dialCode = "5";
        }
    }
    const now = orderEventDate(order);
    const payload = {
        timestamp: Math.floor(now.getTime() / 1000),
        event: "order.created",
        business: {
            name: order.get_bonat_merchant_name(),
            reference: order.get_bonat_merchant_id(),
        },
        order: {
            order_id: order.id,
            branch: { id: branchName, name: branchName },
            pos: { id: String(sessionId), name: sessionName },
            taxes: lines.flatMap((line) =>
                (line.tax_ids || []).map((tax) => ({
                    id: tax.id,
                    name: tax.name,
                    rate: tax.amount,
                }))
            ),
            products: lines.map((line) => {
                const product = line.product_id;
                return {
                    product: {
                        category: {
                            id: product.categ_id?.id ?? product.categ_id?.[0] ??
                                product.pos_categ_ids?.[0]?.id ?? null,
                            name: product.categ_id?.name ?? product.categ_id?.[1] ??
                                product.pos_categ_ids?.[0]?.name ?? "Unknown",
                        },
                        id: product.id,
                        name: product.display_name || "Unnamed Product",
                        price: line.price_unit || 0,
                    },
                    quantity: line.getQuantity?.() ?? 0,
                    unit_price: line.price_unit || 0,
                    total_price: line.displayPrice || 0,
                };
            }),
            quantity: lines.reduce((sum, line) => sum + (line.getQuantity?.() ?? 0), 0),
            subtotal_price: Number.parseFloat(Number(order.priceExcl || 0).toFixed(2)),
            amount_tax: Number.parseFloat(Number(order.amountTaxes || 0).toFixed(2)),
            total_price: Number.parseFloat(Number(order.priceIncl || 0).toFixed(2)),
            created_at: now.toISOString(),
            updated_at: now.toISOString(),
        },
    };
    if (customer.id) {
        payload.order.customer = {
            id: String(customer.id),
            name: customer.name || "Guest",
            dial_code: Number.parseInt(dialCode || ""),
            phone: customerPhone,
            email: customer.email || "",
        };
    }
    return payload;
}

const inFlight = new Set();
const syncedStates = new Set(["paid", "done", "invoiced"]);

function odooDateTime(date) {
    return date.toISOString().slice(0, 19).replace("T", " ");
}

function safeError(value) {
    return String(value || "")
        .replace(/[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}/g, "[email]")
        .replace(/\+?\d[\d\s()-]{7,}\d/g, "[phone]")
        .slice(0, 2000);
}

function requestSummary(payload, order) {
    return JSON.stringify({
        timestamp: payload.timestamp,
        event: payload.event,
        order_id: payload.order.order_id,
        order_uuid: order.uuid,
        business_reference: payload.business.reference,
        branch_id: payload.order.branch.id,
        pos_id: payload.order.pos.id,
        products: payload.order.products.map((item) => ({
            id: item.product.id,
            quantity: item.quantity,
            total_price: item.total_price,
        })),
        total_price: payload.order.total_price,
    });
}

// Best effort delivery: a Bonat outage must never block Odoo checkout.
export async function deliverBonatOrder({ order, pos, call }) {
    if (!order.bonatDeliveryPending || !pos.bonat?.enabled || !syncedStates.has(order.state) ||
        !Number.isInteger(order.id) || order.id <= 0 || !order.uuid || !pos.bonatApi) {
        return false;
    }
    const key = `${pos.config.id || ""}:${order.uuid}`;
    if (inFlight.has(key)) {
        return false;
    }
    inFlight.add(key);
    try {
        const [saved] = await call("pos.order", "read", [
            [order.id], ["uuid", "state", "x_bonat_sent_at", "x_bonat_attempt_count"],
        ]);
        if (!saved || saved.uuid !== order.uuid || !syncedStates.has(saved.state) ||
            saved.x_bonat_sent_at) {
            return false;
        }
        const payload = buildBonatOrderPayload(order, pos);
        const attemptNumber = (saved.x_bonat_attempt_count || 0) + 1;
        const deliveryKey = `${payload.business.reference}:${order.id}`;
        const started = Date.now();
        let result;
        try {
            result = await pos.bonatApi.orderCreated(payload);
        } catch (error) {
            result = { success: false, error: safeError(error?.message || error) };
        }
        const attemptedAt = odooDateTime(new Date());
        const success = result?.success === true;
        const values = {
            x_bonat_last_result: success ? "sent" : "failed",
            x_bonat_last_attempt_at: attemptedAt,
            x_bonat_attempt_count: attemptNumber,
        };
        if (success) {
            values.x_bonat_sent_at = attemptedAt;
        }
        try {
            await call("pos.order", "write", [[order.id], values]);
        } catch (error) {
            console.error("[bonat] unable to save order delivery state:", error);
        }
        try {
            await call("x_bonat_order_attempt", "create", [[{
                x_order_id: order.id,
                x_attempted_at: attemptedAt,
                x_attempt_number: attemptNumber,
                x_delivery_key: deliveryKey,
                x_request: requestSummary(payload, order),
                x_response: JSON.stringify({
                    code: result?.response?.code ?? null,
                    error: safeError(result?.error),
                }),
                x_http_status: result?.httpStatus || 0,
                x_result: success ? "sent" : "failed",
                x_duration_ms: Date.now() - started,
            }]]);
        } catch (error) {
            console.error("[bonat] unable to save order attempt:", error);
        }
        return success;
    } finally {
        inFlight.delete(key);
    }
}
