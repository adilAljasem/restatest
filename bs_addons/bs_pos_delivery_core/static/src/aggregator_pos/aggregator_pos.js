/** @odoo-module **/

import {
    Component,
    onMounted,
    onWillUnmount,
    useExternalListener,
    useRef,
    useState,
} from "@odoo/owl";
import { Navbar } from "@point_of_sale/app/components/navbar/navbar";
import { changesToOrder } from "@point_of_sale/app/models/utils/order_change";
import { SelectionPopup } from "@point_of_sale/app/components/popups/selection_popup/selection_popup";
import { TextInputPopup } from "@point_of_sale/app/components/popups/text_input_popup/text_input_popup";
import { PartnerList } from "@point_of_sale/app/screens/partner_list/partner_list";
import { usePos } from "@point_of_sale/app/hooks/pos_hook";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { patch } from "@web/core/utils/patch";
import { sprintf } from "@web/core/utils/strings";

import {
    fullOrderKitchenChanges,
    normalizeBoardState,
    normalizeNonNegativeInteger,
    orderAgeSeconds,
    orderNotificationKey,
    parseDeliveryDate,
    printKitchenReceipts,
    providerInitials,
    safeProviderColor,
} from "@bs_pos_delivery_core/aggregator_pos/aggregator_pos_utils";

const BUS_CHANNEL = "BS_DELIVERY_POS_BOARD";
const DEFAULT_POLLING_INTERVAL_MINUTES = 2;
const MIN_POLLING_INTERVAL_MS = 5_000;
const MAX_POLLING_INTERVAL_MS = 30 * 60_000;
const MAX_NOTIFIED_ORDERS = 200;
// Safety net only: kitchen tickets are normally printed from the realtime
// notification within a second.  This cheap, indexed check recovers a ticket
// whose notification was lost (websocket reconnect, sleeping tablet) without
// waiting for the board's 1-2 minute refresh interval.
const KITCHEN_PRINT_SWEEP_MS = 10_000;
const INITIAL_ALERT_MAX_AGE_SECONDS = 15 * 60;
const INITIAL_ALERT_LIMIT = 5;

const LANES = Object.freeze([
    {
        key: "received",
        label: _t("Received"),
        nextState: "accepted",
        nextLabel: _t("Accept"),
    },
    {
        key: "accepted",
        label: _t("Accepted"),
        nextState: "preparing",
        nextLabel: _t("Start preparing"),
    },
    {
        key: "preparing",
        label: _t("Preparing"),
        nextState: "ready",
        nextLabel: _t("Mark ready"),
    },
    {
        key: "ready",
        label: _t("Ready"),
        nextState: "picked_up",
        nextLabel: _t("Picked up"),
    },
    {
        key: "picked_up",
        label: _t("Picked up"),
        nextState: "delivered",
        nextLabel: _t("Mark delivered"),
    },
]);

function emptyBoard() {
    return Object.fromEntries(LANES.map((lane) => [lane.key, []]));
}

function errorMessage(error) {
    return (
        error?.data?.message ||
        error?.message ||
        error?.cause?.message ||
        _t("The delivery service is temporarily unavailable.")
    );
}

/**
 * Generic POS delivery hub.
 *
 * Expected RPC contracts:
 *
 *   bs.delivery.provider.get_pos_provider_badges(
 *       pos_config_id=<number>,
 *   ) -> { providers: [{ id, code, name, logo_url, color,
 *                         open_count, received_count, paused }] }
 *
 *   bs.delivery.order.get_pos_board_data(
 *       provider_id=<number>, pos_config_id=<number>,
 *   ) -> { provider: {...}, orders: [{ id, state, name, provider_reference,
 *                                      customer_name, driver_name,
 *                                      driver_eta_minutes, received_at,
 *                                      age_display, customer_note, is_prepaid,
 *                                      total_amount, amount_display,
 *                                      currency_code, currency_symbol }] }
 *
 *   bs.delivery.order.action_pos_transition(
 *       <order_id>, <target_state>, reason=<string>,
 *       provider_id=<number>, pos_config_id=<number>,
 *   ) -> true or a fresh board payload.
 *
 * The normalizers below deliberately accept an array or a wrapped response so
 * an empty/migrating backend never breaks POS startup.
 */
export class AggregatorPosHub extends Component {
    static template = "bs_pos_delivery_core.AggregatorPosHub";
    static props = {};

    setup() {
        this.pos = usePos();
        this.orm = useService("orm");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        this.closeButtonRef = useRef("closeButton");

        this.state = useState({
            providers: [],
            selectedProviderId: false,
            boardProvider: false,
            board: emptyBoard(),
            isOpen: false,
            loadingProviders: false,
            loadingBoard: false,
            boardError: false,
            transitioning: {},
            updatedAt: false,
            pollingIntervalMinutes: DEFAULT_POLLING_INTERVAL_MINUTES,
            rejectionReasons: [],
            offline: this._networkIsOffline(),
        });

        this._mounted = false;
        this._providerRequest = 0;
        this._boardRequest = 0;
        this._providerTimer = false;
        this._boardTimer = false;
        this._providerRefreshQueued = false;
        this._boardRefreshQueued = false;
        this._snapshotInitialized = new Set();
        this._providerSnapshotInitialized = false;
        this._genericNotificationAt = new Map();
        this._notifiedOrderKeys = this._readNotifiedOrderKeys();
        this._kitchenPrintClaimKey = this._createKitchenPrintClaimKey();
        this._kitchenPrintInFlight = new Set();
        this._kitchenPrintCompleted = new Set();
        this._kitchenPrintRefreshPromise = null;
        this._kitchenPrintSweepQueued = false;
        this._kitchenSweepTimer = false;
        // Automatic kitchen tickets and cashier receipts run one at a time.
        // Fired together they hit the printer at the same moment (a busy
        // printer fails the ticket) and lock the same delivery order row in
        // the database (serialization failures).
        this._autoPrintChain = Promise.resolve();
        this._cashierReceiptClaimKey = this._createKitchenPrintClaimKey();
        this._cashierReceiptRefreshPromise = null;
        this._cashierReceiptInFlight = new Set();

        useExternalListener(window, "keydown", (event) => {
            if (event.key === "Escape" && this.state.isOpen) {
                this.closeBoard();
            }
        });
        useExternalListener(window, "online", () => {
            this.state.offline = false;
            void this._processPendingKitchenPrints();
            void this.refreshProviders({ silent: true });
            void this._processPendingCashierReceipts();
            if (this.state.isOpen) {
                void this.refreshBoard({ silent: true });
            }
        });
        useExternalListener(window, "offline", () => {
            this.state.offline = true;
        });
        useExternalListener(document, "visibilitychange", () => {
            if (!document.hidden && !this._isOffline()) {
                void this._processPendingKitchenPrints();
                void this.refreshProviders({ silent: true });
                void this._processPendingCashierReceipts();
                if (this.state.isOpen) {
                    void this.refreshBoard({ silent: true });
                }
            }
        });

        onMounted(() => this._onMounted());
        onWillUnmount(() => this._onWillUnmount());
    }

    get posConfigId() {
        return this.pos.config?.id || this.pos.config_id || false;
    }

    get visibleProviders() {
        return this.state.providers.filter((provider) => provider.active !== false);
    }

    get selectedProvider() {
        const selectedId = String(this.state.selectedProviderId || "");
        if (
            this.state.boardProvider &&
            String(this.state.boardProvider.id || "") === selectedId
        ) {
            return this.state.boardProvider;
        }
        return (
            this.state.providers.find((provider) => String(provider.id) === selectedId) || false
        );
    }

    get boardLanes() {
        return LANES.map((lane) => ({
            ...lane,
            orders: this.state.board[lane.key] || [],
        }));
    }

    get updatedAtLabel() {
        if (!this.state.updatedAt) {
            return "";
        }
        return this.state.updatedAt.toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
        });
    }

    get pollingIntervalLabel() {
        return sprintf(
            _t("auto-refresh %s min"),
            String(this.state.pollingIntervalMinutes)
        );
    }

    _onMounted() {
        this._mounted = true;
        this._connectRealtimeChannel();
        this._restartPollingTimers();
        this._kitchenSweepTimer = window.setInterval(() => {
            // Runs even when the tab is hidden: a kitchen tablet often is.
            if (!this._isOffline()) {
                void this._processPendingKitchenPrints();
            }
        }, KITCHEN_PRINT_SWEEP_MS);
        void this._processPendingKitchenPrints();
        void this.refreshProviders({ silent: true });
        void this._processPendingCashierReceipts();
    }

    _restartPollingTimers() {
        window.clearInterval(this._providerTimer);
        window.clearInterval(this._boardTimer);
        const interval = this._pollingIntervalMs();
        this._providerTimer = window.setInterval(() => {
            if (!document.hidden && !this._isOffline()) {
                void this.refreshProviders({ silent: true });
                void this._processPendingCashierReceipts();
            }
        }, interval);
        this._boardTimer = window.setInterval(() => {
            if (this.state.isOpen && !document.hidden && !this._isOffline()) {
                void this.refreshBoard({ silent: true });
            }
        }, interval);
    }

    _pollingIntervalMs() {
        const minutes = Number(this.state.pollingIntervalMinutes);
        const milliseconds = (Number.isFinite(minutes) && minutes > 0
            ? minutes
            : DEFAULT_POLLING_INTERVAL_MINUTES) * 60_000;
        return Math.min(
            MAX_POLLING_INTERVAL_MS,
            Math.max(MIN_POLLING_INTERVAL_MS, milliseconds)
        );
    }

    _updatePollingInterval(value) {
        const minutes = Number(value);
        if (!Number.isFinite(minutes) || minutes <= 0) {
            return;
        }
        if (minutes !== this.state.pollingIntervalMinutes) {
            this.state.pollingIntervalMinutes = minutes;
            if (this._mounted) {
                this._restartPollingTimers();
            }
        }
    }

    _onWillUnmount() {
        this._mounted = false;
        window.clearInterval(this._providerTimer);
        window.clearInterval(this._boardTimer);
        window.clearInterval(this._kitchenSweepTimer);
        this._kitchenPrintSweepQueued = false;
        this._providerRefreshQueued = false;
        this._boardRefreshQueued = false;
        this.pos.__bsDeliveryBoardSubscribers?.delete(this);
    }

    _connectRealtimeChannel() {
        if (!this.pos.__bsDeliveryBoardSubscribers) {
            this.pos.__bsDeliveryBoardSubscribers = new Set();
        }
        this.pos.__bsDeliveryBoardSubscribers.add(this);

        if (
            this.pos.__bsDeliveryBoardChannelConnected ||
            typeof this.pos.data?.connectWebSocket !== "function"
        ) {
            return;
        }
        this.pos.data.connectWebSocket(BUS_CHANNEL, (payload) => {
            for (const subscriber of this.pos.__bsDeliveryBoardSubscribers || []) {
                subscriber._onRealtimePayload(payload);
            }
        });
        this.pos.__bsDeliveryBoardChannelConnected = true;
    }

    _onRealtimePayload(payload = {}) {
        if (!this._mounted) {
            return;
        }
        const eventType = String(
            payload.event || payload.event_type || payload.type || ""
        ).toLowerCase();
        const providerId =
            payload.provider_id || payload.provider?.id || payload.order?.provider_id || false;

        if (["new_order", "order_created", "received"].includes(eventType)) {
            const orders = payload.orders?.length ? payload.orders : [payload.order || payload];
            for (const order of orders) {
                this._notifyIncomingOrder(order, providerId);
            }
        }
        if (payload.auto_print || payload.order?.auto_print) {
            // Print straight from the notification: the payload already holds
            // the job, so the ticket starts printing without waiting for a
            // pending-jobs round trip.  The server-side claim keeps it to one
            // ticket even when several POS devices receive the same message.
            const directJob = payload.kitchen_job || (
                payload.order_id && payload.pos_order_id ? payload : false
            );
            if (directJob) {
                void this._processKitchenPrintJob(directJob);
            }
            void this._processPendingKitchenPrints();
        }
        if (payload.cashier_receipt_print) {
            void this._processPendingCashierReceipts();
        }

        void this.refreshProviders({ silent: true });
        const isSelectedProvider =
            !providerId || String(providerId) === String(this.state.selectedProviderId || "");
        if (this.state.isOpen && isSelectedProvider) {
            void this.refreshBoard({ silent: true });
        }
    }

    async refreshProviders({ silent = false } = {}) {
        if (this.state.loadingProviders) {
            this._providerRefreshQueued = true;
            return;
        }
        if (!this.posConfigId || this._isOffline()) {
            return;
        }
        const requestId = ++this._providerRequest;
        this.state.loadingProviders = true;
        try {
            const response = await this.orm.call(
                "bs.delivery.provider",
                "get_pos_provider_badges",
                [],
                { pos_config_id: this.posConfigId }
            );
            if (!this._mounted || requestId !== this._providerRequest) {
                return;
            }
            const values = Array.isArray(response) ? response : response?.providers || [];
            this.state.providers = values
                .map((provider, index) => this._normalizeProvider(provider, index))
                .filter((provider) => provider.id);
            if (!Array.isArray(response)) {
                this._updatePollingInterval(response?.polling_interval_minutes);
                if (Array.isArray(response?.recent_received_orders)) {
                    this._processProviderPollSnapshot(response.recent_received_orders);
                }
            }
            void this._processPendingKitchenPrints();

            if (
                this.state.isOpen &&
                !this.state.providers.some(
                    (provider) => String(provider.id) === String(this.state.selectedProviderId)
                )
            ) {
                this.closeBoard();
            }
        } catch (error) {
            if (!silent) {
                this.notification.add(errorMessage(error), {
                    title: _t("Delivery platforms"),
                    type: "warning",
                });
            }
        } finally {
            if (this._mounted && requestId === this._providerRequest) {
                this.state.loadingProviders = false;
                const refreshQueued = this._providerRefreshQueued;
                this._providerRefreshQueued = false;
                if (refreshQueued) {
                    void this.refreshProviders({ silent: true });
                }
            }
        }
    }

    async openProvider(provider) {
        if (!provider?.id) {
            return;
        }
        if (!provider.connected) {
            this.notification.add(
                _t("Connect this platform to this Point of Sale to open its orders."),
                { title: provider.name, type: "warning" }
            );
            return;
        }
        this.state.selectedProviderId = provider.id;
        this.state.boardProvider = provider;
        this.state.board = emptyBoard();
        this.state.boardError = false;
        this.state.isOpen = true;
        window.setTimeout(() => this.closeButtonRef.el?.focus(), 0);
        await this.refreshBoard();
    }

    closeBoard() {
        this.state.isOpen = false;
        this.state.loadingBoard = false;
        this.state.boardError = false;
        this.state.selectedProviderId = false;
        this.state.boardProvider = false;
        this.state.board = emptyBoard();
        this.state.rejectionReasons = [];
        this._boardRefreshQueued = false;
        this._boardRequest += 1;
    }

    onBackdropClick(event) {
        if (event.target === event.currentTarget) {
            this.closeBoard();
        }
    }

    async refreshBoard({ silent = false } = {}) {
        const providerId = this.state.selectedProviderId;
        if (this.state.loadingBoard) {
            this._boardRefreshQueued = true;
            return;
        }
        if (
            !providerId ||
            !this.posConfigId ||
            this._isOffline()
        ) {
            if (this._isOffline() && !silent) {
                this.state.boardError = _t("Reconnect to the network to refresh delivery orders.");
            }
            return;
        }

        const requestId = ++this._boardRequest;
        this.state.loadingBoard = true;
        if (!silent) {
            this.state.boardError = false;
        }
        try {
            const response = await this.orm.call(
                "bs.delivery.order",
                "get_pos_board_data",
                [],
                {
                    provider_id: providerId,
                    pos_config_id: this.posConfigId,
                }
            );
            if (
                !this._mounted ||
                !this.state.isOpen ||
                requestId !== this._boardRequest ||
                String(providerId) !== String(this.state.selectedProviderId)
            ) {
                return;
            }
            this._applyBoardResponse(response, providerId);
        } catch (error) {
            if (
                this._mounted &&
                requestId === this._boardRequest &&
                String(providerId) === String(this.state.selectedProviderId)
            ) {
                this.state.boardError = errorMessage(error);
                if (!silent) {
                    this.notification.add(this.state.boardError, {
                        title: _t("Delivery orders"),
                        type: "danger",
                    });
                }
            }
        } finally {
            if (this._mounted && requestId === this._boardRequest) {
                this.state.loadingBoard = false;
                const refreshQueued = this._boardRefreshQueued;
                this._boardRefreshQueued = false;
                if (
                    refreshQueued &&
                    this.state.isOpen &&
                    String(providerId) === String(this.state.selectedProviderId)
                ) {
                    void this.refreshBoard({ silent: true });
                }
            }
        }
    }

    _applyBoardResponse(response, providerId) {
        const values = Array.isArray(response) ? response : response?.orders || [];
        const board = emptyBoard();
        for (const value of values) {
            const order = this._normalizeOrder(value);
            if (order?.state && board[order.state]) {
                board[order.state].push(order);
            }
        }

        if (response?.provider) {
            const existingIndex = this.state.providers.findIndex(
                (provider) => String(provider.id) === String(providerId)
            );
            this.state.boardProvider = this._normalizeProvider(
                { ...(this.state.providers[existingIndex] || {}), ...response.provider },
                Math.max(existingIndex, 0)
            );
        }
        this.state.rejectionReasons = Array.isArray(response?.rejection_reasons)
            ? response.rejection_reasons
                  .map((reason) => ({
                      ...reason,
                      id: normalizeNonNegativeInteger(reason.id, 0),
                      name: String(reason.name || reason.code || "").trim(),
                      requires_note: Boolean(reason.requires_note),
                  }))
                  .filter((reason) => reason.id && reason.name)
            : [];
        this.state.board = board;
        const openCount = Object.values(board).reduce((total, orders) => total + orders.length, 0);
        if (this.state.boardProvider) {
            this.state.boardProvider = {
                ...this.state.boardProvider,
                open_count: openCount,
                received_count: board.received.length,
            };
        }
        this.state.boardError = false;
        this.state.updatedAt = new Date();
        this._processReceivedSnapshot(providerId, board.received);
    }

    async transition(order, targetState, reason = "", rejectReasonId = false) {
        if (!order?.id || this.isTransitioning(order) || this._isOffline()) {
            if (this._isOffline()) {
                this.notification.add(_t("Reconnect before updating a delivery order."), {
                    type: "warning",
                });
            }
            return;
        }

        let invoicePartnerId = false;
        if (targetState === "accepted" && order.needs_invoice_partner) {
            // Use the partner picker directly: pos.selectPartner() also changes
            // the cashier's unrelated current POS order.
            const partner = await makeAwaitable(this.dialog, PartnerList, { partner: false });
            if (!partner) {
                return;
            }
            invoicePartnerId = partner.id;
        }

        this.state.transitioning = {
            ...this.state.transitioning,
            [order.id]: true,
        };
        try {
            const response = await this.orm.call(
                "bs.delivery.order",
                "action_pos_transition",
                [order.id, targetState],
                {
                    reason: String(reason || "").trim(),
                    reject_reason_id: rejectReasonId || false,
                    provider_id: this.state.selectedProviderId,
                    pos_config_id: this.posConfigId,
                    invoice_partner_id: invoicePartnerId,
                }
            );
            if (response && (Array.isArray(response) || response.orders)) {
                this._applyBoardResponse(response, this.state.selectedProviderId);
            } else {
                await this.refreshBoard({ silent: true });
            }
            void this.refreshProviders({ silent: true });
        } catch (error) {
            this.notification.add(errorMessage(error), {
                title: _t("Order update failed"),
                type: "danger",
            });
        } finally {
            const transitioning = { ...this.state.transitioning };
            delete transitioning[order.id];
            this.state.transitioning = transitioning;
        }
    }

    async reject(order) {
        if (this.isTransitioning(order)) {
            return;
        }
        if (!this.state.rejectionReasons.length) {
            this.notification.add(
                _t("Configure at least one rejection reason before rejecting orders."),
                { title: _t("Rejection reasons"), type: "warning" }
            );
            return;
        }
        const selectedReason = await makeAwaitable(this.dialog, SelectionPopup, {
            title: _t("Select rejection reason"),
            list: this.state.rejectionReasons.map((reason) => ({
                id: reason.id,
                label: reason.name,
                item: reason,
            })),
        });
        if (!selectedReason) {
            return;
        }
        let note = "";
        if (selectedReason.requires_note) {
            note = await makeAwaitable(this.dialog, TextInputPopup, {
                title: _t("Rejection note"),
                placeholder: _t("Add the details required for this rejection reason..."),
                rows: 3,
            });
            if (!String(note || "").trim()) {
                return;
            }
        }
        await this.transition(order, "rejected", note, selectedReason.id);
    }

    isTransitioning(order) {
        return Boolean(this.state.transitioning[order?.id]);
    }

    _normalizeProvider(provider = {}, index = 0) {
        const id = provider.id || provider.provider_id || false;
        const name = String(provider.display_name || provider.name || provider.code || "").trim();
        const counts = provider.counts || {};
        const color = safeProviderColor(provider.color, index);
        let logoUrl = provider.logo_url || provider.image_url || false;
        if (!logoUrl && provider.image_128) {
            logoUrl = String(provider.image_128).startsWith("data:")
                ? provider.image_128
                : `data:image/png;base64,${provider.image_128}`;
        }
        if (!logoUrl && id) {
            logoUrl = `/web/image?model=bs.delivery.provider&id=${encodeURIComponent(
                id
            )}&field=image_128`;
        }
        return {
            ...provider,
            id,
            code: String(provider.code || `provider_${id || index}`),
            name: name || _t("Delivery"),
            initials: providerInitials(name || provider.code),
            logo_url: logoUrl,
            color,
            active: provider.active !== false,
            connected: provider.connected !== false,
            title: provider.connected === false
                ? sprintf(_t("%s is not connected to this POS"), name)
                : name,
            paused: Boolean(provider.paused),
            partially_paused: Boolean(provider.partially_paused),
            sound_alert: provider.sound_alert !== false,
            polling_interval_minutes: Number(
                provider.polling_interval_minutes || DEFAULT_POLLING_INTERVAL_MINUTES
            ),
            open_count: normalizeNonNegativeInteger(
                provider.open_count ?? provider.order_count ?? counts.open,
                0
            ),
            received_count: normalizeNonNegativeInteger(
                provider.received_count ?? provider.new_count ?? counts.received,
                0
            ),
        };
    }

    _normalizeOrder(order = {}) {
        const id = order.id || order.order_id || false;
        if (!id) {
            return false;
        }
        const state = normalizeBoardState(order.state || order.status);
        return {
            ...order,
            id,
            state,
            name: String(order.name || order.order_name || order.reference || id),
            provider_reference: String(
                order.provider_reference || order.platform_reference || order.external_reference || ""
            ),
            customer_name: String(order.customer_name || order.customer?.name || _t("Guest")),
            driver_name: String(order.driver_name || order.driver?.name || ""),
            driver_phone: String(order.driver_phone || order.driver?.phone || ""),
            driver_eta_minutes:
                order.driver_eta_minutes ?? order.driver_eta ?? order.driver?.eta_minutes ?? false,
            customer_phone_masked: String(order.customer_phone_masked || ""),
            delivery_address: String(order.delivery_address || order.address || ""),
            fulfillment_type: String(order.fulfillment_type || "delivery"),
            scheduled_at: String(order.scheduled_at || ""),
            scheduled_at_display: this._dateTimeDisplay(order.scheduled_at),
            fulfillment_label: this._fulfillmentLabel(order.fulfillment_type),
            customer_note: String(order.customer_note || order.note || ""),
            is_prepaid: Boolean(order.is_prepaid ?? order.prepaid ?? order.is_paid),
            amount_display: this._amountDisplay(order),
            age_display: this._ageDisplay(order),
        };
    }

    _amountDisplay(order) {
        if (order.amount_display || order.total_display) {
            return String(order.amount_display || order.total_display);
        }
        const amount = Number(order.total_amount ?? order.amount_total ?? 0);
        const currencyCode = String(order.currency_code || "").toUpperCase();
        if (Number.isFinite(amount) && /^[A-Z]{3}$/.test(currencyCode)) {
            try {
                return new Intl.NumberFormat(undefined, {
                    style: "currency",
                    currency: currencyCode,
                }).format(amount);
            } catch {
                // Fall through to the provider-supplied symbol/plain number.
            }
        }
        const symbol = String(order.currency_symbol || "").trim();
        return `${symbol ? `${symbol} ` : ""}${Number.isFinite(amount) ? amount.toFixed(2) : "0.00"}`;
    }

    _ageDisplay(order) {
        if (order.age_display) {
            return String(order.age_display);
        }
        const seconds = orderAgeSeconds(order);
        if (seconds === false) {
            return "";
        }
        if (seconds < 60) {
            return _t("Just now");
        }
        const minutes = Math.floor(seconds / 60);
        if (minutes < 60) {
            return sprintf(_t("%s min ago"), String(minutes));
        }
        return sprintf(_t("%s hr ago"), String(Math.floor(minutes / 60)));
    }

    handleLogoError(provider) {
        provider.logo_url = false;
    }

    _processReceivedSnapshot(providerId, orders) {
        const providerKey = String(providerId);
        const isInitialSnapshot = !this._snapshotInitialized.has(providerKey);
        let initialAlerts = 0;
        for (const order of orders) {
            const key = this._orderNotificationKey(order);
            if (!key || this._notifiedOrderKeys.has(key)) {
                continue;
            }
            if (
                isInitialSnapshot &&
                initialAlerts < INITIAL_ALERT_LIMIT &&
                this._shouldAlertInitialOrder(order)
            ) {
                initialAlerts += 1;
                this._notifyIncomingOrder(order, providerId);
            } else if (isInitialSnapshot) {
                this._rememberNotificationKey(key);
            } else {
                this._notifyIncomingOrder(order, providerId);
            }
        }
        this._snapshotInitialized.add(providerKey);
    }

    _processProviderPollSnapshot(orders) {
        const isInitialSnapshot = !this._providerSnapshotInitialized;
        let initialAlerts = 0;
        for (const order of orders) {
            const key = this._orderNotificationKey(order);
            if (!key || this._notifiedOrderKeys.has(key)) {
                continue;
            }
            if (
                isInitialSnapshot &&
                initialAlerts < INITIAL_ALERT_LIMIT &&
                this._shouldAlertInitialOrder(order)
            ) {
                initialAlerts += 1;
                this._notifyIncomingOrder(order, order.provider_id);
            } else if (isInitialSnapshot) {
                this._rememberNotificationKey(key);
            } else {
                this._notifyIncomingOrder(order, order.provider_id);
            }
        }
        this._providerSnapshotInitialized = true;
    }

    _dateTimeDisplay(value) {
        if (!value) {
            return "";
        }
        const date = parseDeliveryDate(value);
        if (!date) {
            return String(value);
        }
        return new Intl.DateTimeFormat(undefined, {
            dateStyle: "short",
            timeStyle: "short",
        }).format(date);
    }

    _fulfillmentLabel(value) {
        return {
            delivery: _t("Delivery"),
            pickup: _t("Pickup"),
            dine_in: _t("Dine In"),
        }[String(value || "delivery")] || _t("Delivery");
    }

    _shouldAlertInitialOrder(order = {}) {
        let ageSeconds = Number(order.age_seconds);
        if (!Number.isFinite(ageSeconds) && order.received_at) {
            const receivedAt = Date.parse(order.received_at);
            if (Number.isFinite(receivedAt)) {
                ageSeconds = Math.max(0, (Date.now() - receivedAt) / 1000);
            }
        }
        // When an older backend does not return timing metadata, alerting is
        // safer than silently losing a live kitchen order.  The initial limit
        // and sessionStorage dedupe prevent a notification storm.
        return !Number.isFinite(ageSeconds) || ageSeconds <= INITIAL_ALERT_MAX_AGE_SECONDS;
    }

    _notifyIncomingOrder(order = {}, fallbackProviderId = false) {
        const providerId = order.provider_id || order.provider?.id || fallbackProviderId;
        const stableKey = this._orderNotificationKey(order);
        if (stableKey) {
            if (this._notifiedOrderKeys.has(stableKey)) {
                return;
            }
            this._rememberNotificationKey(stableKey);
        } else {
            const genericKey = String(providerId || "all");
            const lastNotification = this._genericNotificationAt.get(genericKey) || 0;
            if (Date.now() - lastNotification < 10_000) {
                return;
            }
            this._genericNotificationAt.set(genericKey, Date.now());
        }

        const provider = this.state.providers.find(
            (candidate) => String(candidate.id) === String(providerId || "")
        );
        const providerName = String(
            order.provider_name || order.provider?.name || provider?.name || _t("Delivery")
        );
        const orderLabel = String(
            order.name || order.order_name || order.reference || order.provider_reference || ""
        );
        const message = orderLabel
            ? sprintf(_t("New %s order: %s"), providerName, orderLabel)
            : sprintf(_t("New %s order"), providerName);

        // Honour the per-store/per-connection sound policy returned by the
        // backend.  A missing flag keeps the backwards-compatible default.
        const soundAlert = order.sound_alert ?? provider?.sound_alert;
        if (soundAlert !== false) {
            try {
                Promise.resolve(this.pos.sound?.play("notification")).catch(() => {});
            } catch {
                // Browsers can reject audio before the cashier's first interaction.
            }
        }
        const options = {
            title: _t("Delivery order"),
            type: "warning",
            sticky: true,
        };
        if (provider) {
            options.buttons = [
                {
                    name: _t("Open board"),
                    onClick: () => void this.openProvider(provider),
                },
            ];
        }
        this.notification.add(message, options);
    }

    _orderNotificationKey(order = {}) {
        return orderNotificationKey(order);
    }

    _storageKey() {
        return `bs_delivery_notified_orders:${String(this.posConfigId || "unknown")}`;
    }

    _readNotifiedOrderKeys() {
        try {
            const values = JSON.parse(window.sessionStorage.getItem(this._storageKey()) || "[]");
            return new Set(Array.isArray(values) ? values.slice(-MAX_NOTIFIED_ORDERS) : []);
        } catch {
            return new Set();
        }
    }

    _rememberNotificationKey(key) {
        this._notifiedOrderKeys.add(key);
        while (this._notifiedOrderKeys.size > MAX_NOTIFIED_ORDERS) {
            this._notifiedOrderKeys.delete(this._notifiedOrderKeys.values().next().value);
        }
        try {
            window.sessionStorage.setItem(
                this._storageKey(),
                JSON.stringify([...this._notifiedOrderKeys])
            );
        } catch {
            // Notification dedupe remains in memory if storage is unavailable.
        }
    }

    _createKitchenPrintClaimKey() {
        try {
            if (typeof window.crypto?.randomUUID === "function") {
                return window.crypto.randomUUID();
            }
        } catch {
            // Fall through to a stable-per-tab entropy source.
        }
        return [
            "bs-delivery-pos",
            String(this.posConfigId || "unknown"),
            String(Date.now()),
            Math.random().toString(36).slice(2),
        ].join(":");
    }

    async _processPendingCashierReceipts() {
        if (
            !this._mounted ||
            !this.posConfigId ||
            this._isOffline() ||
            document.hidden ||
            this._cashierReceiptRefreshPromise
        ) {
            return this._cashierReceiptRefreshPromise;
        }
        const task = (async () => {
            let jobs;
            try {
                jobs = await this.orm.call(
                    "bs.delivery.order", "get_pending_cashier_receipts", [this.posConfigId]
                );
            } catch {
                // The next poll can recover a job that has not been claimed.
                return;
            }
            for (const job of Array.isArray(jobs) ? jobs : []) {
                await this._processCashierReceiptJob(job);
            }
        })();
        this._cashierReceiptRefreshPromise = task;
        try {
            return await task;
        } finally {
            if (this._cashierReceiptRefreshPromise === task) {
                this._cashierReceiptRefreshPromise = null;
            }
        }
    }

    async _processCashierReceiptJob(job = {}) {
        const deliveryOrderId = normalizeNonNegativeInteger(job.order_id, 0);
        const posOrderId = normalizeNonNegativeInteger(job.pos_order_id, 0);
        if (
            !deliveryOrderId || !posOrderId ||
            this._cashierReceiptInFlight.has(deliveryOrderId)
        ) {
            return;
        }
        this._cashierReceiptInFlight.add(deliveryOrderId);
        try {
            await this._enqueueAutoPrint(() => this._runCashierReceiptJob(deliveryOrderId, posOrderId));
        } finally {
            this._cashierReceiptInFlight.delete(deliveryOrderId);
        }
    }

    async _runCashierReceiptJob(deliveryOrderId, posOrderId) {
        let claimed = false;
        let success = false;
        let failure = "";
        try {
            const order = await this._loadPosOrderForPrint(posOrderId);
            if (!order) {
                return; // Keep the job unclaimed for a later synchronization.
            }
            claimed = Boolean(await this.orm.call(
                "bs.delivery.order", "claim_cashier_receipt",
                [deliveryOrderId, this.posConfigId, this._cashierReceiptClaimKey]
            ));
            if (!claimed) {
                return;
            }
            if (order.nb_print > 0) {
                success = true; // A cashier already printed this receipt in this POS tab.
            } else {
                const result = await this.pos.printReceipt({ order });
                success = result === true || result?.successful === true;
                if (!success) {
                    failure = _t("The POS receipt printer did not confirm printing.");
                }
            }
        } catch (error) {
            failure = errorMessage(error).slice(0, 2000);
        } finally {
            if (claimed) {
                try {
                    await this.orm.call(
                        "bs.delivery.order", "acknowledge_cashier_receipt",
                        [
                            deliveryOrderId, this.posConfigId, this._cashierReceiptClaimKey,
                            success, failure,
                        ]
                    );
                } catch {
                    // Do not reprint an unacknowledged attempt automatically.
                }
                if (!success) {
                    this.notification.add(`#${deliveryOrderId}: ${failure}`, {
                        title: _t("Cashier receipt printing failed"), type: "danger",
                    });
                }
            }
        }
    }

    async _processPendingKitchenPrints() {
        if (!this._mounted || !this.posConfigId || this._isOffline()) {
            return this._kitchenPrintRefreshPromise;
        }
        if (this._kitchenPrintRefreshPromise) {
            // A sweep is already running and may have queried the server
            // before the new job existed.  Run once more right after it,
            // instead of silently dropping this request until the next poll.
            this._kitchenPrintSweepQueued = true;
            return this._kitchenPrintRefreshPromise;
        }
        const task = (async () => {
            let response;
            try {
                response = await this.orm.call(
                    "bs.delivery.order",
                    "get_pending_kitchen_prints",
                    [this.posConfigId]
                );
            } catch {
                // A later poll can deliver jobs not yet claimed by a POS.
                return;
            }
            const jobs = Array.isArray(response) ? response : response?.jobs || [];
            for (const job of jobs) {
                await this._processKitchenPrintJob(job);
            }
        })();
        this._kitchenPrintRefreshPromise = task;
        try {
            return await task;
        } finally {
            if (this._kitchenPrintRefreshPromise === task) {
                this._kitchenPrintRefreshPromise = null;
            }
            if (this._kitchenPrintSweepQueued && this._mounted) {
                this._kitchenPrintSweepQueued = false;
                void this._processPendingKitchenPrints();
            }
        }
    }

    async _processKitchenPrintJob(job = {}) {
        const deliveryOrderId = normalizeNonNegativeInteger(
            job.order_id || job.delivery_order_id || job.id,
            0
        );
        const rawPosOrderId = Array.isArray(job.pos_order_id)
            ? job.pos_order_id[0]
            : job.pos_order_id || job.pos_order?.id;
        const posOrderId = normalizeNonNegativeInteger(rawPosOrderId, 0);
        const dedupeKey = String(deliveryOrderId || "");
        if (
            !deliveryOrderId ||
            !posOrderId ||
            job.auto_print === false ||
            this._kitchenPrintInFlight.has(dedupeKey) ||
            this._kitchenPrintCompleted.has(dedupeKey)
        ) {
            return;
        }

        this._kitchenPrintInFlight.add(dedupeKey);
        try {
            await this._enqueueAutoPrint(() => this._runKitchenPrintJob(deliveryOrderId, posOrderId, dedupeKey));
        } finally {
            this._kitchenPrintInFlight.delete(dedupeKey);
        }
    }

    /** Queue an automatic print behind the previous one (never rejects). */
    _enqueueAutoPrint(task) {
        const run = this._autoPrintChain.then(task, task);
        this._autoPrintChain = run.catch(() => {});
        return run;
    }

    async _runKitchenPrintJob(deliveryOrderId, posOrderId, dedupeKey) {
        if (this._kitchenPrintCompleted.has(dedupeKey)) {
            return;
        }
        let claimed = false;
        let success = false;
        let failure = "";
        try {
            // Claim only when this device can really print the ticket. A
            // device without a kitchen printer, or one that has not loaded
            // the order yet, must leave the single attempt to another device
            // or to the next poll instead of turning the job into an error.
            if (this._kitchenPrintUnavailableReason()) {
                return;
            }
            const order = await this._loadPosOrderForPrint(posOrderId);
            if (!order) {
                return;
            }
            claimed = Boolean(
                await this.orm.call(
                    "bs.delivery.order",
                    "claim_kitchen_print",
                    [deliveryOrderId, this.posConfigId, this._kitchenPrintClaimKey]
                )
            );
            if (!claimed) {
                return;
            }
            success = await this._printKitchenOrderWithEvidence(order);
        } catch (error) {
            failure = errorMessage(error).slice(0, 2000);
            if (claimed) {
                this.notification.add(`#${deliveryOrderId}: ${failure}`, {
                    title: _t("Kitchen printing failed"),
                    type: "danger",
                    sticky: true,
                });
            }
        } finally {
            if (claimed) {
                try {
                    await this.orm.call(
                        "bs.delivery.order",
                        "acknowledge_kitchen_print",
                        [
                            deliveryOrderId,
                            this.posConfigId,
                            this._kitchenPrintClaimKey,
                            success,
                            failure,
                        ]
                    );
                    if (success) {
                        this._rememberCompletedKitchenPrint(dedupeKey);
                    }
                } catch {
                    // An unacknowledged print needs a manager's manual retry.
                }
            }
        }
    }

    /**
     * Return the delivery POS order, loading it from the server if needed.
     *
     * Delivery orders are paid and invoiced on the server before printing, so
     * they are never part of the POS startup load nor of
     * deviceSync.readDataFromServer(), which both fetch draft orders only.
     * Without the realtime SYNCHRONISATION message (POS opened or reloaded
     * after acceptance, websocket reconnecting, sleeping tablet) the order
     * would stay missing on this device.
     */
    async _loadPosOrderForPrint(posOrderId) {
        const orderModel = this.pos.models?.["pos.order"];
        let order = orderModel?.get(posOrderId);
        if (order) {
            return order;
        }
        if (typeof this.pos.data?.loadServerOrders === "function") {
            try {
                await this.pos.data.loadServerOrders([["id", "=", posOrderId]]);
            } catch {
                // The next poll can load the order again.
            }
            order = orderModel?.get(posOrderId);
        }
        return order || false;
    }

    _kitchenPrintUnavailableReason() {
        if (!this.pos.config?.printerCategories?.size) {
            return _t("No kitchen printer categories are configured for this POS.");
        }
        if (
            !Array.from(this.pos.unwatched?.printers || []).length ||
            typeof this.pos.generateOrderChange !== "function" ||
            typeof this.pos.generateReceiptsDataToPrint !== "function" ||
            typeof this.pos.printOrderChanges !== "function"
        ) {
            return _t("No kitchen printer is configured or connected for this POS.");
        }
        return false;
    }

    async _printKitchenOrderWithEvidence(order) {
        const unavailable = this._kitchenPrintUnavailableReason();
        if (unavailable) {
            throw new Error(unavailable);
        }
        const categories = this.pos.config.printerCategories;
        const printers = Array.from(this.pos.unwatched.printers);

        // The whole order, whatever this device believes was already sent:
        // the claimed server job is what guarantees a single ticket.
        const orderChange = fullOrderKitchenChanges(order, categories, changesToOrder);
        const hasPrintableChanges = Boolean(
            orderChange.new.length ||
                orderChange.cancelled.length ||
                orderChange.noteUpdate.length ||
                orderChange.internal_note ||
                orderChange.general_customer_note
        );
        if (!hasPrintableChanges) {
            throw new Error(_t("The POS order has no printable kitchen changes."));
        }

        // printChanges() opens Odoo's RetryPrintPopup on failure. Automatic
        // delivery jobs must report their result once through the durable job,
        // leaving another physical attempt to the manager's manual retry.
        const result = await printKitchenReceipts(this.pos, order, orderChange, printers);
        if (!result.successful) {
            throw new Error(result.errors.join("\n") || _t("The kitchen printer did not confirm the ticket."));
        }

        order.uiState?.lastPrints?.push(orderChange);
        order.updateLastOrderChange();
        if (!this.pos.models?.["pos.prep.display"]?.length) {
            try {
                await this.pos.syncAllOrders({ orders: [order] });
            } catch {
                // The physical print is already positively confirmed. Device
                // synchronization can retry independently and must not cause a
                // duplicate kitchen ticket through the durable print queue.
            }
        }
        return true;
    }

    _rememberCompletedKitchenPrint(key) {
        this._kitchenPrintCompleted.add(key);
        while (this._kitchenPrintCompleted.size > MAX_NOTIFIED_ORDERS) {
            this._kitchenPrintCompleted.delete(
                this._kitchenPrintCompleted.values().next().value
            );
        }
    }

    _networkIsOffline() {
        return Boolean(this.pos.data?.network?.offline || navigator.onLine === false);
    }

    _isOffline() {
        return Boolean(this.state?.offline || this._networkIsOffline());
    }
}

patch(Navbar, {
    components: {
        ...Navbar.components,
        AggregatorPosHub,
    },
});
