import { describe, expect, test } from "@odoo/hoot";
import {
    KitchenFlowTrace,
    getKitchenFlowTrace,
    registerKitchenFlowTrace,
    roundMilliseconds,
    unregisterKitchenFlowTrace,
} from "@bs_pos_kitchen_flow/utils/kitchen_flow_trace";

function fakeClock() {
    let tick = 0;
    let wall = 0;
    return {
        now: () => tick,
        wallNow: () => new Date(Date.UTC(2026, 8, 1, 10, 0, 0, wall)),
        advance(milliseconds) {
            tick += milliseconds;
            wall += milliseconds;
        },
    };
}

describe("@bs_pos_kitchen_flow/unit/kitchen_flow_trace", () => {
    test("records exact nested operation timings and payload summary", () => {
        const clock = fakeClock();
        const trace = new KitchenFlowTrace(
            {
                transactionUuid: "trace-1",
                configId: 7,
                sessionId: 8,
                orderUuid: "order-1",
                orderReference: "Order 701",
                priority: "display_first",
            },
            clock
        );
        const outer = trace.begin("submit", "Submit", { parentCode: "" });
        clock.advance(12.3456);
        const printer = trace.begin("printer", "Printer", { category: "printer" });
        clock.advance(20.1111);
        trace.end(printer, "success", { printer_name: "Kitchen" });
        clock.advance(2);
        trace.end(outer);
        trace.finish();

        const payload = trace.toPayload();
        expect(payload.total_duration_ms).toBe(34.457);
        expect(payload.steps).toHaveLength(2);
        expect(payload.steps[1].duration_ms).toBe(20.111);
        expect(payload.steps[1].parent_code).toBe("submit");
        expect(payload.client_summary.printer_count).toBe(1);
        expect(payload.status).toBe("success");
    });

    test("an operation error produces a warning but a submit error produces a failure", () => {
        const clock = fakeClock();
        const warningTrace = new KitchenFlowTrace({ transactionUuid: "trace-2" }, clock);
        const step = warningTrace.begin("printer", "Printer");
        clock.advance(10);
        warningTrace.end(step, "error", undefined, new Error("Printer timeout"));
        warningTrace.finish();
        expect(warningTrace.status).toBe("warning");
        expect(warningTrace.toPayload().steps[0].error_message).toBe("Printer timeout");

        const failedTrace = new KitchenFlowTrace({ transactionUuid: "trace-3" }, clock);
        clock.advance(5);
        failedTrace.finish("failed", new Error("Offline"));
        expect(failedTrace.status).toBe("failed");
        expect(failedTrace.errorMessage).toBe("Offline");
    });

    test("registers one active trace per order and rounds to microsecond precision", () => {
        const trace = new KitchenFlowTrace({ transactionUuid: "trace-4" }, fakeClock());
        registerKitchenFlowTrace("order-4", trace);
        expect(getKitchenFlowTrace("order-4")).toBe(trace);
        unregisterKitchenFlowTrace("order-4", trace);
        expect(getKitchenFlowTrace("order-4")).toBe(undefined);
        expect(roundMilliseconds(1.23456)).toBe(1.235);
    });
});

