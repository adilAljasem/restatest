const activeTraces = new Map();

function roundMilliseconds(value) {
    return Math.round(Math.max(0, Number(value) || 0) * 1000) / 1000;
}

function errorText(error) {
    if (!error) {
        return "";
    }
    return String(error.message || error);
}

function makeTransactionId(orderUuid) {
    if (globalThis.crypto?.randomUUID) {
        return globalThis.crypto.randomUUID();
    }
    return `${orderUuid || "order"}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export class KitchenFlowTrace {
    constructor(metadata = {}, clock = {}) {
        this.metadata = metadata;
        this.now = clock.now || (() => performance.now());
        this.wallNow = clock.wallNow || (() => new Date());
        this.transactionUuid = metadata.transactionUuid || makeTransactionId(metadata.orderUuid);
        this.startedTick = this.now();
        this.startedAt = this.wallNow();
        this.completedAt = null;
        this.totalDurationMs = 0;
        this.status = "success";
        this.errorMessage = "";
        this.steps = [];
        this.openSteps = [];
        this.sequence = 0;
        this.runtime = {};
    }

    begin(code, name, options = {}) {
        const token = {
            sequence: ++this.sequence,
            code,
            name,
            parent_code:
                options.parentCode === undefined
                    ? this.openSteps.at(-1)?.code || ""
                    : options.parentCode,
            category: options.category || "client",
            status: "info",
            started_at: this.wallNow().toISOString(),
            completed_at: null,
            duration_ms: 0,
            details: options.details || {},
            error_message: "",
            _startedTick: this.now(),
        };
        this.steps.push(token);
        this.openSteps.push(token);
        return token;
    }

    end(token, status = "success", details = undefined, error = undefined) {
        if (!token || token.completed_at) {
            return token;
        }
        token.completed_at = this.wallNow().toISOString();
        token.duration_ms = roundMilliseconds(this.now() - token._startedTick);
        token.status = status;
        if (details !== undefined) {
            token.details = { ...(token.details || {}), ...(details || {}) };
        }
        token.error_message = errorText(error);
        const position = this.openSteps.lastIndexOf(token);
        if (position !== -1) {
            this.openSteps.splice(position, 1);
        }
        if (status === "error" && this.status === "success") {
            this.status = "warning";
        }
        return token;
    }

    async measure(code, name, callback, options = {}) {
        const token = this.begin(code, name, options);
        try {
            const result = await callback();
            const status = options.statusFromResult?.(result) || "success";
            const details = options.detailsFromResult?.(result);
            this.end(token, status, details);
            return result;
        } catch (error) {
            this.end(token, "error", undefined, error);
            throw error;
        }
    }

    mark(code, name, options = {}) {
        const token = this.begin(code, name, options);
        return this.end(token, options.status || "info", options.details);
    }

    finish(status = undefined, error = undefined) {
        const finalTick = this.now();
        const finalWall = this.wallNow();
        for (const token of [...this.openSteps]) {
            this.end(token, error ? "error" : "success");
        }
        this.completedAt = finalWall;
        this.totalDurationMs = roundMilliseconds(finalTick - this.startedTick);
        if (status) {
            this.status = status;
        }
        if (error) {
            this.status = "failed";
            this.errorMessage = errorText(error);
        } else if (this.steps.some((step) => step.status === "error")) {
            this.status = "warning";
        }
        return this;
    }

    toPayload() {
        const orderServerId = Number(this.metadata.orderServerId);
        return {
            name: this.metadata.orderReference
                ? `${this.metadata.orderReference} / ${this.transactionUuid.slice(0, 8)}`
                : this.transactionUuid,
            transaction_uuid: this.transactionUuid,
            config_id: this.metadata.configId,
            session_id: this.metadata.sessionId,
            order_server_id: Number.isInteger(orderServerId) && orderServerId > 0 ? orderServerId : 0,
            order_uuid: this.metadata.orderUuid || "",
            order_reference: this.metadata.orderReference || "",
            device_identifier: this.metadata.deviceIdentifier || "",
            priority: this.metadata.priority || "printer_first",
            status: this.status,
            started_at: this.startedAt.toISOString(),
            completed_at: (this.completedAt || this.wallNow()).toISOString(),
            total_duration_ms: this.totalDurationMs,
            error_message: this.errorMessage,
            client_summary: {
                step_count: this.steps.length,
                error_count: this.steps.filter((step) => step.status === "error").length,
                printer_count: this.steps.filter((step) => step.category === "printer").length,
                browser_language: globalThis.navigator?.language || "",
            },
            steps: this.steps.map(({ _startedTick, ...step }) => step),
        };
    }
}

export function registerKitchenFlowTrace(orderUuid, trace) {
    if (orderUuid) {
        activeTraces.set(orderUuid, trace);
    }
}

export function unregisterKitchenFlowTrace(orderUuid, trace) {
    if (activeTraces.get(orderUuid) === trace) {
        activeTraces.delete(orderUuid);
    }
}

export function getKitchenFlowTrace(orderOrUuid) {
    const uuid = typeof orderOrUuid === "string" ? orderOrUuid : orderOrUuid?.uuid;
    return uuid ? activeTraces.get(uuid) : undefined;
}

export function getKitchenFlowTraceForOrders(orders = []) {
    for (const order of orders || []) {
        const trace = getKitchenFlowTrace(order);
        if (trace) {
            return trace;
        }
    }
    return undefined;
}

export { roundMilliseconds };

