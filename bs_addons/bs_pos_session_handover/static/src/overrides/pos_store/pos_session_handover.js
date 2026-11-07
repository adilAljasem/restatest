import { ClosePosPopup } from "@point_of_sale/app/components/popups/closing_popup/closing_popup";
import { OpeningControlPopup } from "@point_of_sale/app/components/popups/opening_control_popup/opening_control_popup";
import { PosStore } from "@point_of_sale/app/services/pos_store";
import { ask, makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { TableHandoverPopup } from "@bs_pos_session_handover/overrides/table_handover_popup/table_handover_popup";
import { AlertDialog, ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";

const { DateTime } = luxon;

function getErrorMessage(error) {
    return error?.data?.message || error?.message || _t("The table handover could not be completed.");
}

patch(PosStore.prototype, {
    pinoGetTableName(table) {
        return table?.getName?.() || table?.table_number?.toString() || _t("current table");
    },

    pinoGetHandoverScopeTable() {
        return this.selectedTable || this.getOrder()?.table_id || null;
    },

    pinoOrderMatchesHandoverScope(order, table = null, tableIds = null) {
        if (!order.table_id) {
            return false;
        }
        if (table) {
            return order.table_id.id === table.id;
        }
        if (tableIds) {
            return tableIds.has(order.table_id.id);
        }
        return true;
    },

    /**
     * Opener / closer ownership (Foodics-like).
     * The owner of a table order is its server session. Odoo rewrites
     * ``session_id`` locally on every order received through live sync, so we
     * rely on ``pino_owner_session_id`` (plain integer computed on the server).
     * New local orders (not synced yet) always belong to this cashier.
     */
    pinoIsOwnOrder(order) {
        if (!order.isSynced) {
            return true;
        }
        const ownerId = order.pino_owner_session_id || order.raw?.pino_owner_session_id;
        if (ownerId) {
            return ownerId === this.session.id;
        }
        const sessionId = order.raw?.session_id ?? order.session_id?.id;
        return !sessionId || sessionId === this.session.id;
    },

    pinoGetOrderOpenerName(order) {
        return order?.pino_opened_by_name || order?.getCashierName?.() || "";
    },

    pinoGetCurrentEmployee() {
        const cashier = this.getCashier?.();
        return cashier?.model?.name === "hr.employee" ? cashier : null;
    },

    getSyncAllOrdersContext(orders, options = {}) {
        const context = super.getSyncAllOrdersContext(...arguments);
        return {
            pino_current_session_id: this.session.id,
            pino_current_employee_id: this.pinoGetCurrentEmployee()?.id || false,
            ...context,
        };
    },

    /**
     * "Bill" button (print before payment): remember who printed it so the
     * table shows it and changes color until the order is paid.
     */
    async printReceipt(options = {}) {
        const result = await super.printReceipt(...arguments);
        const order = options.order || this.getOrder();
        if (options.printBillActionTriggered && this.config.module_pos_restaurant && order?.table_id) {
            await this.pinoMarkBillPrinted(order);
        }
        return result;
    },

    async pinoMarkBillPrinted(order) {
        try {
            if (typeof order.id !== "number") {
                await this.syncAllOrders({ orders: [order], throw: true });
            }
            if (typeof order.id !== "number") {
                return;
            }
            const records = await this.data.call(
                "pos.order",
                "pino_mark_bill_printed",
                [[order.id], this.session.id, this.pinoGetCurrentEmployee()?.id || false],
                { context: { device_identifier: this.device.identifier } }
            );
            const vals = records?.[0];
            if (vals) {
                order.pino_bill_printed = vals.pino_bill_printed;
                order.pino_bill_print_count = vals.pino_bill_print_count;
                order.pino_bill_printed_by_name = vals.pino_bill_printed_by_name;
                order.pino_bill_printed_at = vals.pino_bill_printed_at;
            }
        } catch (error) {
            console.warn("Could not record the printed bill", error);
        }
    },

    pinoGetBillPrintedOrder(table) {
        return table?.getOrders?.().find((order) => order.pino_bill_printed) || null;
    },

    pinoFormatBillTime(order) {
        const value = order?.pino_bill_printed_at;
        if (!value) {
            return "";
        }
        try {
            const dt = typeof value === "string"
                ? luxon.DateTime.fromSQL(value, { zone: "utc" }).toLocal()
                : value;
            return dt.toFormat("HH:mm");
        } catch {
            return "";
        }
    },

    async pay() {
        const order = this.getOrder();
        if (order && this.config.module_pos_restaurant && order.table_id && !this.pinoIsOwnOrder(order)) {
            const takenPayments = order.payment_ids.filter((p) => typeof p.id === "number");
            if (takenPayments.length) {
                this.dialog.add(AlertDialog, {
                    title: _t("Table Handover"),
                    body: _t(
                        "This table already has a payment taken by %s. Only that cashier can complete it.",
                        this.pinoGetOrderOpenerName(order)
                    ),
                });
                return;
            }
        }
        return await super.pay(...arguments);
    },

    pinoGetOwnOpenOrders() {
        return this.getOpenOrders().filter((order) => this.pinoIsOwnOrder(order));
    },

    pinoGetTableOrdersForHandover(table = null, tableIds = null) {
        if (!this.config.module_pos_restaurant) {
            return [];
        }
        return this.pinoGetOwnOpenOrders().filter(
            (order) =>
                this.pinoOrderMatchesHandoverScope(order, table, tableIds) &&
                order.lines.length &&
                !order.payment_ids.length
        );
    },

    pinoGetPaidDraftTableOrders(table = null, tableIds = null) {
        if (!this.config.module_pos_restaurant) {
            return [];
        }
        return this.pinoGetOwnOpenOrders().filter(
            (order) =>
                this.pinoOrderMatchesHandoverScope(order, table, tableIds) &&
                order.lines.length &&
                order.payment_ids.length
        );
    },

    pinoRemoveLocalHandoverOrders(orderIds) {
        const transferredIds = new Set(orderIds);
        const localOrders = this.models["pos.order"].filter((order) => transferredIds.has(order.id));
        const currentOrderRemoved = localOrders.some(
            (order) => order.uuid === this.selectedOrderUuid
        );
        for (const order of localOrders) {
            this.removePendingOrder(order);
            this.data.localDeleteCascade(order);
        }
        if (currentOrderRemoved) {
            // `selectedTable` is a getter derived from the selected order in Odoo 19,
            // so dropping the order is what releases the table.
            this.setOrder(undefined);
            this.showDefault?.();
        }
        this.computeTableCount?.();
    },

    pinoApplyClaimedHandoverOrders(orderIds) {
        const claimedIds = new Set(orderIds || []);
        const cashier = this.getCashier?.();
        const isEmployeeCashier = cashier?.model?.name === "hr.employee";
        const localOrders = this.models["pos.order"].filter((order) => claimedIds.has(order.id));
        for (const order of localOrders) {
            const vals = {
                session_id: this.session,
                pino_is_session_transferred: true,
                pino_handover_state: "done",
                pino_handover_to_session_id: this.session,
                pino_new_session_cashier_name: cashier?.name || "",
            };
            if (isEmployeeCashier) {
                vals.employee_id = cashier;
                vals.pino_new_session_cashier_id = cashier;
            }
            order.update(vals);
        }
        this.computeTableCount?.();
    },

    pinoGetOrderTotal(order) {
        // `amount_total` is only stamped when the order is pushed, so prefer the
        // live computed value when it is available.
        const liveTotal = order.priceIncl;
        return typeof liveTotal === "number" ? liveTotal : order.amount_total || 0;
    },

    pinoGetOrdersTotal(orders) {
        return orders.reduce((total, order) => total + this.pinoGetOrderTotal(order), 0);
    },

    pinoGetOrderLabel(orderCount) {
        return orderCount === 1 ? _t("1 unpaid order") : _t("%s unpaid orders", orderCount);
    },

    pinoGetBlockedLabel(blockedCount) {
        return blockedCount === 1 ? _t("1 blocked") : _t("%s blocked", blockedCount);
    },

    pinoGetTableHandoverScopes() {
        const scopeTable = this.pinoGetHandoverScopeTable();
        const scopesByTableId = new Map();
        const getScope = (table) => {
            if (!table?.id) {
                return null;
            }
            if (!scopesByTableId.has(table.id)) {
                scopesByTableId.set(table.id, {
                    table,
                    orders: [],
                    blockedOrders: [],
                });
            }
            return scopesByTableId.get(table.id);
        };

        for (const order of this.pinoGetTableOrdersForHandover()) {
            getScope(order.table_id)?.orders.push(order);
        }
        for (const order of this.pinoGetPaidDraftTableOrders()) {
            getScope(order.table_id)?.blockedOrders.push(order);
        }

        return [...scopesByTableId.values()]
            .map((scope) => {
                const orderCount = scope.orders.length;
                const blockedCount = scope.blockedOrders.length;
                const amount = this.pinoGetOrdersTotal(scope.orders);
                return {
                    id: scope.table.id,
                    table: scope.table,
                    tableName: _t("Table %s", this.pinoGetTableName(scope.table)),
                    orderCount,
                    blockedCount,
                    amount,
                    formattedAmount: this.env.utils.formatCurrency(amount),
                    orderLabel: this.pinoGetOrderLabel(orderCount),
                    blockedLabel: blockedCount ? this.pinoGetBlockedLabel(blockedCount) : "",
                    isBlocked: Boolean(blockedCount),
                    isCurrent: Boolean(scopeTable && scope.table.id === scopeTable.id),
                };
            })
            .sort((left, right) => {
                const leftIsCurrent = scopeTable && left.table.id === scopeTable.id;
                const rightIsCurrent = scopeTable && right.table.id === scopeTable.id;
                if (leftIsCurrent !== rightIsCurrent) {
                    return leftIsCurrent ? -1 : 1;
                }
                return left.tableName.localeCompare(right.tableName, undefined, { numeric: true });
            });
    },

    pinoGetAllHandoverScope(tableScopes) {
        const orderCount = tableScopes.reduce((total, scope) => total + scope.orderCount, 0);
        const blockedCount = tableScopes.reduce((total, scope) => total + scope.blockedCount, 0);
        const amount = tableScopes.reduce((total, scope) => total + scope.amount, 0);
        return {
            orderCount,
            blockedCount,
            amount,
            formattedAmount: this.env.utils.formatCurrency(amount),
        };
    },

    async pinoGetHandoverTargets() {
        try {
            return await this.data.call("pos.session", "pino_get_handover_targets", [
                this.session.id,
            ]);
        } catch (error) {
            console.warn(error);
            return [];
        }
    },

    async pinoPrepareTableHandover({
        table = null,
        tables = null,
        manual = false,
        source = manual ? "manual" : "closing",
        targetSession = null,
    } = {}) {
        const tableIds = tables?.length
            ? new Set(tables.map((selectedTable) => selectedTable.id))
            : null;
        const blockedOrders = this.pinoGetPaidDraftTableOrders(table, tableIds);
        if (blockedOrders.length) {
            this.dialog.add(AlertDialog, {
                title: _t("Table Handover"),
                body: _t(
                    "Some open table orders already have payment lines. Validate or remove those payments before transferring or closing the session."
                ),
            });
            return false;
        }

        const orders = this.pinoGetTableOrdersForHandover(table, tableIds);
        if (!orders.length) {
            if (manual) {
                this.dialog.add(AlertDialog, {
                    title: _t("Table Handover"),
                    body: table
                        ? _t("This table has no unpaid open orders to transfer.")
                        : _t("There are no unpaid open table orders to transfer."),
                });
            }
            return true;
        }

        if (manual && targetSession) {
            const confirmed = await ask(this.dialog, {
                title: _t("Table Handover"),
                body: _t(
                    "Hand over %s open order(s) to %s now?",
                    orders.length,
                    targetSession.label
                ),
                confirmLabel: _t("Transfer"),
                cancelLabel: _t("Cancel"),
            });
            if (!confirmed) {
                return false;
            }
        } else if (manual) {
            let body = _t("Transfer %s open table order(s) to the next POS session?", orders.length);
            if (table) {
                body = _t(
                    "Transfer %s open order(s) from table %s to the next POS session?",
                    orders.length,
                    this.pinoGetTableName(table)
                );
            } else if (tables?.length) {
                body = _t(
                    "Transfer %s open order(s) from %s selected table(s) to the next POS session?",
                    orders.length,
                    tables.length
                );
            }
            const confirmed = await ask(this.dialog, {
                title: _t("Table Handover"),
                body,
                confirmLabel: _t("Transfer"),
                cancelLabel: _t("Cancel"),
            });
            if (!confirmed) {
                return false;
            }
        }

        try {
            await this.syncAllOrders({ orders, throw: true });
            const syncedOrderIds = orders
                .map((order) => order.id)
                .filter((orderId) => typeof orderId === "number");

            if (!syncedOrderIds.length) {
                return true;
            }

            const result = await this.data.call("pos.session", "pino_prepare_table_handover", [
                this.session.id,
                syncedOrderIds,
                source,
                targetSession?.id || false,
            ]);

            if (!result.successful) {
                this.dialog.add(AlertDialog, {
                    title: _t("Table Handover"),
                    body: result.message || _t("The open table orders could not be transferred."),
                });
                return false;
            }

            if (result.target_session_id) {
                // The tables stay on the shared floor, they just change owner.
                const handedIds = new Set(result.order_ids);
                for (const order of this.models["pos.order"].filter((o) => handedIds.has(o.id))) {
                    order.pino_owner_session_id = result.target_session_id;
                }
                if (handedIds.has(this.getOrder()?.id)) {
                    this.setOrder(undefined);
                    this.showDefault?.();
                }
                await this.deviceSync?.readDataFromServer?.();
                if (manual) {
                    this.notification.add(result.message, { type: "success" });
                }
                return true;
            }
            this.pinoRemoveLocalHandoverOrders(result.order_ids);
            if (manual) {
                this.notification.add(
                    _t(
                        "%s open table order(s) were prepared for the next POS session.",
                        result.count
                    ),
                    { type: "success" }
                );
            }
            return true;
        } catch (error) {
            console.warn(error);
            this.dialog.add(AlertDialog, {
                title: _t("Table Handover"),
                body: getErrorMessage(error),
            });
            return false;
        }
    },

    async pinoPrepareTableHandoverManually() {
        const tableScopes = this.pinoGetTableHandoverScopes();
        if (!tableScopes.length) {
            this.dialog.add(AlertDialog, {
                title: _t("Table Handover"),
                body: _t("There are no unpaid open table orders to transfer."),
            });
            return true;
        }

        const selectedScope = await makeAwaitable(this.dialog, TableHandoverPopup, {
            title: _t("Transfer Open Tables"),
            scopes: tableScopes,
            allScope: this.pinoGetAllHandoverScope(tableScopes),
            targets: await this.pinoGetHandoverTargets(),
        });
        if (!selectedScope) {
            return false;
        }

        return await this.pinoPrepareTableHandover({
            tables: selectedScope.tables,
            manual: true,
            source: "manual",
            targetSession: selectedScope.target,
        });
    },

    async pinoPrepareTableHandoverBeforeClosing() {
        const tableScopes = this.pinoGetTableHandoverScopes();
        if (!tableScopes.length) {
            return true;
        }

        const selectedScope = await makeAwaitable(this.dialog, TableHandoverPopup, {
            title: _t("Open Tables Before Closing"),
            scopes: tableScopes,
            allScope: this.pinoGetAllHandoverScope(tableScopes),
            isClosing: true,
            targets: await this.pinoGetHandoverTargets(),
        });
        if (!selectedScope) {
            return false;
        }

        return await this.pinoPrepareTableHandover({
            tables: selectedScope.tables,
            manual: false,
            source: "closing",
            targetSession: selectedScope.target,
        });
    },

    async pinoShowOpeningHandoverNotification() {
        if (!this.config.module_pos_restaurant) {
            return;
        }
        try {
            const result = await this.data.call(
                "pos.session",
                "pino_get_handover_opening_notification",
                [this.session.id]
            );
            this.pinoApplyClaimedHandoverOrders(result.order_ids);
            if (result.count) {
                this.notification.add(
                    _t(
                        "%s transferred table order(s) were received from the previous POS session.",
                        result.count
                    ),
                    { type: "success" }
                );
            }
        } catch (error) {
            console.warn(error);
        }
    },
});

patch(ClosePosPopup.prototype, {
    async closeSession() {
        const handoverReady = await this.pos.pinoPrepareTableHandoverBeforeClosing?.();
        if (handoverReady === false) {
            return;
        }
        return await super.closeSession(...arguments);
    },

    // Same as core, but "Cancel Orders" only cancels orders of THIS session.
    // Core cancels every local draft order, which (with a shared floor) would
    // cancel the open tables of the other cashiers.
    async handleClosingError(response) {
        if (!this.pos.config.module_pos_restaurant) {
            return await super.handleClosingError(...arguments);
        }
        this.dialog.add(ConfirmationDialog, {
            title: response.title || _t("Error"),
            body: response.message,
            confirmLabel: _t("Review Orders"),
            cancelLabel: _t("Cancel Orders"),
            confirm: () => {
                if (!response.redirect) {
                    this.props.close();
                    this.pos.navigate("TicketScreen");
                }
            },
            cancel: async () => {
                if (!response.redirect) {
                    const now = DateTime.now();
                    const ordersDraft = this.pos
                        .pinoGetOwnOpenOrders()
                        .filter((o) => !(o.preset_time && o.preset_time > now));
                    await this.pos.deleteOrders(ordersDraft, response.open_order_ids);
                    this.closeSession();
                }
            },
            dismiss: async () => {},
        });

        if (response.redirect) {
            this.pos.router.close();
        }
    },
});

patch(OpeningControlPopup.prototype, {
    async confirm() {
        await super.confirm(...arguments);
        await this.pos.pinoShowOpeningHandoverNotification?.();
    },
});
