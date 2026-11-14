import "@pos_restaurant/app/services/pos_store";
import "@resta_kds/pos/pos_config_kds";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";
import {
    KitchenFlowTrace,
    getKitchenFlowTrace,
    getKitchenFlowTraceForOrders,
    registerKitchenFlowTrace,
    unregisterKitchenFlowTrace,
} from "@bs_pos_kitchen_flow/utils/kitchen_flow_trace";

const PRE_DISPLAY_PHASE = "bs_kitchen_display_first";
const PARALLEL_PREFLIGHT_PHASE = "bs_kitchen_parallel_preflight";

function printerDetails(printer) {
    return {
        printer_name: printer?.config?.name || "",
        printer_type: printer?.config?.printer_type || "",
        printer_address:
            printer?.config?.epson_printer_ip ||
            printer?.config?.proxy_ip ||
            printer?.config?.iot_device_id?.name ||
            "",
    };
}

function printStatus(result) {
    return result?.successful === false ? "error" : "success";
}

function printResultDetails(result) {
    return {
        successful: result?.successful !== false,
        warning_code: result?.warningCode || "",
        message: result?.message?.body || "",
    };
}

function hasKitchenScreen(config) {
    // Resta kitchen screens (resta_kds) linked to this point of sale
    return (config.restaKdsCategories?.size || 0) > 0;
}

patch(PosStore.prototype, {
    async submitOrder() {
        const order = this.getOrder();
        const orderUuid = order?.uuid;
        this.bsKitchenOrdersSending ||= new Set();

        if (orderUuid && this.bsKitchenOrdersSending.has(orderUuid)) {
            getKitchenFlowTrace(orderUuid)?.mark(
                "duplicate_submit_blocked",
                "Block duplicate kitchen submission",
                {
                    category: "client",
                    status: "warning",
                }
            );
            if (this.config.kitchen_flow_notifications) {
                this.notification.add(_t("The order is already being sent to the kitchen…"), {
                    type: "warning",
                });
            }
            return;
        }

        const priority = this.config.kitchen_flow_priority || "printer_first";
        const trace = new KitchenFlowTrace({
            configId: this.config.id,
            sessionId: this.session.id,
            orderServerId: order?.id,
            orderUuid,
            orderReference: order?.getName?.() || order?.name || "",
            deviceIdentifier: this.device?.identifier || "",
            priority,
        });
        const submitToken = trace.begin("submit_order", "Submit order to kitchen", {
            category: "client",
            details: { priority },
            parentCode: "",
        });
        registerKitchenFlowTrace(orderUuid, trace);
        if (orderUuid) {
            this.bsKitchenOrdersSending.add(orderUuid);
        }

        const closeSendingNotification = this.config.kitchen_flow_notifications
            ? this.notification.add(_t("Sending order to the kitchen…"), {
                  type: "info",
                  sticky: true,
              })
            : undefined;

        let result;
        let submitError;
        try {
            result = await super.submitOrder(...arguments);
            trace.end(submitToken, "success");
            if (this.config.kitchen_flow_notifications) {
                this.notification.add(_t("Order sent to the kitchen successfully."), {
                    type: "success",
                });
            }
            return result;
        } catch (error) {
            submitError = error;
            trace.end(submitToken, "error", undefined, error);
            if (this.config.kitchen_flow_notifications) {
                this.notification.add(_t("The order could not be sent to the kitchen."), {
                    type: "danger",
                });
            }
            throw error;
        } finally {
            closeSendingNotification?.();
            trace.metadata.orderServerId = order?.id;
            trace.finish(submitError ? "failed" : undefined, submitError);
            if (orderUuid) {
                this.bsKitchenOrdersSending.delete(orderUuid);
            }
            unregisterKitchenFlowTrace(orderUuid, trace);
            if (this.config.kitchen_flow_debug) {
                try {
                    await this.data.call("bs.pos.kitchen.flow.log", "create_from_ui", [
                        trace.toPayload(),
                    ]);
                } catch (logError) {
                    console.warn("Kitchen flow diagnostic could not be stored", logError);
                }
            }
        }
    },

    async sendOrderInPreparationUpdateLastChange(order, opts = {}) {
        const trace = getKitchenFlowTrace(order);
        if (!trace) {
            return await super.sendOrderInPreparationUpdateLastChange(...arguments);
        }
        return await trace.measure(
            "send_preparation_update",
            "Send preparation and update last change",
            () => super.sendOrderInPreparationUpdateLastChange(...arguments),
            { category: "kitchen" }
        );
    },

    async checkPreparationStateAndSentOrderInPreparation(order, opts = {}) {
        const trace = getKitchenFlowTrace(order);
        if (!trace) {
            return await super.checkPreparationStateAndSentOrderInPreparation(...arguments);
        }
        return await trace.measure(
            "check_preparation_state",
            "Check server preparation state",
            () => super.checkPreparationStateAndSentOrderInPreparation(...arguments),
            {
                category: "network",
                details: { order_was_synced: Boolean(order.isSynced) },
            }
        );
    },

    async sendOrderInPreparation(order, opts = {}) {
        const trace = getKitchenFlowTrace(order);
        if (!trace) {
            return await super.sendOrderInPreparation(...arguments);
        }

        const priority = this.config.kitchen_flow_priority || "printer_first";
        const hasDisplay = hasKitchenScreen(this.config);
        const canPreSendDisplay = hasDisplay && !opts.cancelled && priority !== "printer_first";
        const token = trace.begin("send_to_kitchen_tools", "Send to kitchen tools", {
            category: "kitchen",
            details: {
                priority,
                has_preparation_display: hasDisplay,
                physical_printer_count: this.unwatched.printers?.length || 0,
                cancelled: Boolean(opts.cancelled),
            },
        });

        try {
            if (canPreSendDisplay) {
                trace.runtime.preDisplayAttempted = true;
                if (priority === "parallel") {
                    const parallelReady = await this.bsPrepareParallelDisplaySend(
                        order,
                        opts,
                        trace
                    );
                    trace.runtime.preDisplayPromise = parallelReady
                        ? this.bsProcessPreparationDisplayInParallel(order, opts, trace)
                        : Promise.resolve(false);
                } else {
                    trace.runtime.preDisplayPromise = this.bsSendToPreparationDisplayFirst(
                        order,
                        opts,
                        trace
                    );
                    await trace.runtime.preDisplayPromise;
                }
            } else if (priority !== "printer_first" && opts.cancelled) {
                trace.mark(
                    "priority_fallback_cancelled",
                    "Use standard flow for a cancelled order",
                    {
                        category: "kitchen",
                        status: "skipped",
                        details: { requested_priority: priority },
                    }
                );
            }

            const result = await super.sendOrderInPreparation(...arguments);
            if (trace.runtime.preDisplayPromise) {
                await trace.runtime.preDisplayPromise;
            }
            trace.end(token, "success", {
                preparation_display_sent_first: Boolean(trace.runtime.preDisplaySucceeded),
            });
            return result;
        } catch (error) {
            trace.end(token, "error", undefined, error);
            throw error;
        }
    },

    async bsSendToPreparationDisplayFirst(order, opts, trace) {
        const token = trace.begin(
            "kitchen_display_pre_send",
            "Send to the kitchen screen before the printers",
            {
                category: "kitchen_display",
                details: { priority: this.config.kitchen_flow_priority },
            }
        );
        try {
            await this.syncAllOrders({
                orders: [order],
                force: true,
                throw: true,
                context: {
                    resta_kds_fire: true,
                    bs_kitchen_flow_phase: PRE_DISPLAY_PHASE,
                },
            });
            trace.runtime.preDisplaySucceeded = true;
            trace.end(token, "success");
            return true;
        } catch (error) {
            trace.runtime.preDisplaySucceeded = false;
            trace.end(token, "error", { fallback_to_standard_flow: true }, error);
            return false;
        }
    },

    async bsPrepareParallelDisplaySend(order, opts, trace) {
        const token = trace.begin(
            "parallel_display_preflight",
            "Synchronize order before parallel kitchen delivery",
            {
                category: "network",
                details: { priority: "parallel" },
            }
        );
        try {
            // Complete this synchronization before starting the parallel operations, so the
            // kitchen screen call below works on the order as it is on the POS now.
            await this.syncAllOrders({
                orders: [order],
                force: true,
                throw: true,
                context: { bs_kitchen_flow_phase: PARALLEL_PREFLIGHT_PHASE },
            });
            trace.end(token, "success", { order_server_id: order.id });
            return true;
        } catch (error) {
            trace.runtime.preDisplaySucceeded = false;
            trace.end(token, "error", { fallback_to_standard_flow: true }, error);
            return false;
        }
    },

    async bsProcessPreparationDisplayInParallel(order, opts, trace) {
        const token = trace.begin(
            "display_process_parallel",
            "Send to the kitchen screen in parallel with printing",
            {
                category: "kitchen_display",
                details: { order_server_id: order.id },
            }
        );
        try {
            await this.data.call("pos.order", "resta_kds_fire", [[order.id]]);
            trace.runtime.preDisplaySucceeded = true;
            trace.end(token, "success");
            return true;
        } catch (error) {
            trace.runtime.preDisplaySucceeded = false;
            trace.end(token, "error", { fallback_to_standard_flow: true }, error);
            return false;
        }
    },

    async syncAllOrders(options = {}) {
        const trace = getKitchenFlowTraceForOrders(options.orders);
        if (!trace) {
            return await super.syncAllOrders(...arguments);
        }

        const operationPhase = options.context?.bs_kitchen_flow_phase;
        let code = "sync_orders";
        let name = "Synchronize POS order";
        let category = "network";
        if (operationPhase === PRE_DISPLAY_PHASE) {
            code = "display_sync_first";
            name = "Send order to the kitchen screen first";
            category = "kitchen_display";
        } else if (operationPhase === PARALLEL_PREFLIGHT_PHASE) {
            code = "parallel_preflight_sync";
            name = "Synchronize order before parallel kitchen delivery";
        } else if (trace.runtime.preDisplayAttempted) {
            // the screen already has the dishes: this sync only stores the POS snapshot
            code = "final_snapshot_sync";
            name = "Synchronize final preparation snapshot";
            category = "snapshot";
        } else if (hasKitchenScreen(this.config)) {
            code = "display_sync";
            name = "Synchronize order with the kitchen screen";
            category = "kitchen_display";
        }

        const token = trace.begin(code, name, {
            category,
            details: {
                force: Boolean(options.force),
                phase: operationPhase || "standard",
                order_count: options.orders?.length || 0,
            },
        });
        try {
            const result = await super.syncAllOrders(options);
            const returnedError = result instanceof Error;
            trace.end(
                token,
                returnedError ? "error" : "success",
                { synced_order_count: Array.isArray(result) ? result.length : 0 },
                returnedError ? result : undefined
            );
            return result;
        } catch (error) {
            trace.end(token, "error", undefined, error);
            throw error;
        }
    },

    generateOrderChange(order, orderChange, categories, reprint = false) {
        const trace = getKitchenFlowTrace(order);
        if (!trace) {
            return super.generateOrderChange(...arguments);
        }
        const token = trace.begin("generate_printer_change", "Generate printer order changes", {
            category: "printer",
            details: { category_count: categories?.length || categories?.size || 0, reprint },
        });
        try {
            const result = super.generateOrderChange(...arguments);
            trace.end(token, "success", {
                new_line_count: result?.changes?.new?.length || 0,
                cancelled_line_count: result?.changes?.cancelled?.length || 0,
                note_update_count: result?.changes?.noteUpdate?.length || 0,
            });
            return result;
        } catch (error) {
            trace.end(token, "error", undefined, error);
            throw error;
        }
    },

    async generateReceiptsDataToPrint(orderData, changes, orderChange) {
        const trace = this.bsKitchenCurrentPrintTrace;
        if (!trace) {
            return await super.generateReceiptsDataToPrint(...arguments);
        }
        return await trace.measure(
            "render_kitchen_receipt_data",
            "Build kitchen receipt data",
            () => super.generateReceiptsDataToPrint(...arguments),
            {
                category: "printer",
                detailsFromResult: (result) => ({ receipt_count: result?.length || 0 }),
            }
        );
    },

    async printChanges(order, orderChange, reprint = false, printers = this.unwatched.printers) {
        const trace = getKitchenFlowTrace(order);
        if (!trace) {
            return await super.printChanges(...arguments);
        }
        const token = trace.begin("print_kitchen_changes", "Print kitchen changes", {
            category: "printer",
            details: {
                printer_count: printers?.length || printers?.size || 0,
                change_batch_count: orderChange?.length || 0,
                reprint,
            },
        });
        const previousTrace = this.bsKitchenCurrentPrintTrace;
        this.bsKitchenCurrentPrintTrace = trace;
        try {
            const result = await super.printChanges(...arguments);
            trace.end(token, result ? "success" : "error", { any_receipt_printed: Boolean(result) });
            return result;
        } catch (error) {
            trace.end(token, "error", undefined, error);
            throw error;
        } finally {
            this.bsKitchenCurrentPrintTrace = previousTrace;
        }
    },

    async printOrderChanges(data, printer) {
        const trace = this.bsKitchenCurrentPrintTrace;
        if (!trace) {
            return await super.printOrderChanges(...arguments);
        }
        return await trace.measure(
            "print_kitchen_receipt",
            `Print kitchen receipt: ${printer?.config?.name || "printer"}`,
            () => super.printOrderChanges(...arguments),
            {
                category: "printer",
                details: printerDetails(printer),
                statusFromResult: printStatus,
                detailsFromResult: printResultDetails,
            }
        );
    },
});
