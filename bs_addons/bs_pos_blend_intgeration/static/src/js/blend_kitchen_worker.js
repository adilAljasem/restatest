import { ConnectionAbortedError, ConnectionLostError, RPCError } from "@web/core/network/rpc";

/** Process one printer at a time; never repeat physical output to retry an RPC. */
async function confirmPrint(store) {
    try {
        await store.data.call("blend.kitchen.print", "finish", store._blendKitchenAck);
        store._blendKitchenAck = null;
        store._blendKitchenConnectionRestored();
    } catch (error) {
        // A manager may have explicitly reset an unconfirmed job while this
        // browser was offline. Do not let its revoked token block other jobs.
        if (error.data?.name === "odoo.exceptions.AccessError") {
            store._blendKitchenAck = null;
        }
        throw error;
    }
}

export async function drainBlendKitchenPrint(store) {
    const printers = store.unwatched.printers || [];
    if (store._blendKitchenBusy || !printers.length || store.data.network.offline) {
        return;
    }
    store._blendKitchenBusy = true;
    try {
        // A lost acknowledgement is retried separately from physical printing.
        if (store._blendKitchenAck) {
            await confirmPrint(store);
        }
        for (let index = 0; index < 10; index++) {
            const job = await store.data.call("blend.kitchen.print", "claim_next", [
                store.config.id, store.session.id, printers.map((printer) => printer.config.id),
            ]);
            store._blendKitchenConnectionRestored();
            if (!job) {
                break;
            }
            const printer = printers.find((candidate) => candidate.config.id === job.printer_id);
            let successful = false;
            let message = "";
            try {
                const result = await store.printOrderChanges(job.receipt, printer);
                successful = result?.successful === true;
                message = String(result?.message?.body || "Printer did not confirm success");
                if (successful && result.warningCode) {
                    store.displayPrinterWarning(result, printer.config.name);
                }
            } catch (error) {
                message = String(error.message || error);
            }
            store._blendKitchenAck = [[job.id], job.token, successful, successful ? "" : message];
            if (!successful) {
                store._blendKitchenNotify(job.reference, message);
            }
            await confirmPrint(store);
        }
        store._blendKitchenRpcWarning = false;
    } catch (error) {
        if (error instanceof ConnectionLostError || error instanceof ConnectionAbortedError) {
            // A failed claim is not proof of a printer failure (or even a job).
            // Retrying finish must keep the ACK and never repeat physical output.
            store._blendKitchenConnectionLost();
        } else {
            if (error instanceof RPCError) {
                // A server error response also proves that connectivity returned.
                store._blendKitchenConnectionRestored();
            }
            if (!store._blendKitchenRpcWarning) {
                store._blendKitchenNotify("", String(error.message || error));
                store._blendKitchenRpcWarning = true;
            }
        }
    } finally {
        store._blendKitchenBusy = false;
    }
}
