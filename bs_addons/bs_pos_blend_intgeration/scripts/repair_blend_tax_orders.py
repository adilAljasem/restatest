"""Run in a fresh Odoo 19 shell after installing the non-blocking tax fix.

Preview first, then set BLEND_REPAIR_IDS to reviewed pos.order IDs and
BLEND_REPAIR_APPLY = True. BLEND_REPAIR_DB must equal the target database.
This is historical accounting repair: no kitchen resend or Blend status update.
The shell wrapper commits the whole batch only if every attempted repair passes.
"""
import copy
import json
from unittest.mock import patch

from markupsafe import Markup


def _skip_reason(order):
    if not order.blend_is_inbound or not order.blend_financial_required:
        return "Not an enrolled incoming Blend order"
    if (order.state != "draft" or order.payment_ids or order.account_move
            or order.amount_paid or order.blend_financial_state == "done"):
        return "Already processed, cancelled, or has payment/invoice: manual review only"
    if order.blend_refund_required or any(line.qty <= 0 for line in order.lines):
        return "Refund/cancellation or non-positive quantity: manual review only"
    error = order.blend_financial_error or ""
    if (order.blend_financial_state != "failed"
            or not error.startswith("Blend tax (") or "differs from Odoo" not in error):
        return "Not the diagnosed Blend tax mismatch failure"
    if not order.blend_can_finalize:
        return "Order has not been accepted, or its current status/settings do not allow accounting"
    if not order.config_id.blend_finalize_on_accept:
        return "Blend payment and invoicing are disabled on this POS"
    if order.session_id.state != "opened":
        return "Original POS session is not open: manual review; session will not be changed"
    return None


def _preparation_snapshot(order):
    tickets = order.env["pos.prep.order"].search([("pos_order_id", "=", order.id)], order="id")
    lines = tickets.prep_line_ids.sorted("id")
    states = order.env["pos.prep.state"].search([("prep_line_id", "in", lines.ids)], order="id")
    return (
        tickets.read(["prep_line_ids", "completion_time", "write_date"]),
        lines.read(["quantity", "cancelled", "product_id", "write_date"]),
        states.read(["todo", "stage_id", "last_stage_change", "write_date"]),
    )


def _outbound_snapshot(order):
    return order.env["blend.integration.log"].search([
        ("order_id", "=", order.id), ("direction", "=", "outbound"),
    ], order="id").read(["state", "event", "request_payload", "write_date"])


def repair_blend_tax_orders(env, order_ids=None, apply=False):
    """No commit here: the caller owns the transaction; failures roll back this batch."""
    if not (env.su or env.user.has_group("base.group_system")):
        raise RuntimeError("Run this maintenance script as an Odoo administrator.")
    Order = env["pos.order"].sudo()
    if "blend_tax_warning" not in Order._fields:
        raise RuntimeError("Update bs_pos_blend_intgeration and restart Odoo before running this script.")
    if apply and not order_ids:
        raise RuntimeError("Preview first, then supply explicit BLEND_REPAIR_IDS.")
    if order_ids is None:
        orders = Order.search([
            ("blend_partner_reference", "!=", False),
            ("blend_financial_state", "=", "failed"),
            ("blend_financial_error", "ilike", "Blend tax ("),
        ], order="id")
    else:
        if any(type(record_id) is not int or record_id <= 0 for record_id in order_ids):
            raise RuntimeError("BLEND_REPAIR_IDS must contain positive integer pos.order IDs.")
        orders = Order.browse(sorted(set(order_ids))).exists()
        if set(orders.ids) != set(order_ids):
            raise RuntimeError("One or more requested pos.order IDs do not exist in this database.")
    result = []
    with env.cr.savepoint():
        for order in orders:
            order = order.with_company(order.company_id).with_context(
                blend_financial_processing=True, blend_skip_status_sync=True,
                generate_pdf=False, mail_notify_force_send=False,
            )
            if apply:
                order.lock_for_update()
                order.invalidate_recordset()
            reason = _skip_reason(order)
            row = {
                "id": order.id, "reference": order.blend_partner_reference,
                "company": order.company_id.id, "session": order.session_id.id,
                "total": order.amount_total, "blend_tax": (order.blend_payload or {}).get("tax"),
                "result": "skipped" if reason else "eligible",
            }
            if reason:
                row["reason"] = reason
            if reason or not apply:
                result.append(row)
                continue
            payload = copy.deepcopy(order.blend_payload)
            old_error = order.blend_financial_error
            old_status = order.blend_status
            old_lines = [(line.id, line.product_id.id, line.qty, line.blend_gross_total) for line in order.lines]
            preparation = _preparation_snapshot(order)
            outbound = _outbound_snapshot(order)

            def no_preparation(model, order_id, *args, **kwargs):
                # The native payment wizard can call this through _send_order().
                # Only this standalone shell process is patched, never Odoo workers.
                if order_id != order.id:
                    raise RuntimeError("Unexpected preparation request for another order")
                return None

            with patch.object(type(env["pos.prep.order"]), "process_order", no_preparation), patch(
                "requests.sessions.Session.request",
                side_effect=RuntimeError("External HTTP is disabled during historical Blend repair"),
            ):
                order.blend_financial_attempts += 1
                order._blend_finalize_accounting()
                invoice = order.account_move
                if (order.state != "done" or order.blend_financial_state != "done"
                        or not invoice or invoice.state != "posted"
                        or not order.currency_id.is_zero(invoice.amount_residual)
                        or not order.currency_id.is_zero(order.amount_paid - order.amount_total)
                        or not order.currency_id.is_zero(invoice.amount_total - order.amount_total)
                        or not order.currency_id.is_zero(invoice.amount_tax - order.amount_tax)
                        or not order.currency_id.is_zero(order.amount_total - float(payload["total"]))):
                    raise RuntimeError("Payment/invoice verification failed for order %s" % order.id)
                if (order.blend_payload != payload or order.blend_status != old_status
                        or old_lines != [(line.id, line.product_id.id, line.qty, line.blend_gross_total) for line in order.lines]
                        or _preparation_snapshot(order) != preparation
                        or _outbound_snapshot(order) != outbound):
                    raise RuntimeError("Historical order/preparation/outbound data changed unexpectedly for order %s" % order.id)
                order.message_post(body=Markup("<p>%s</p><pre>%s</pre><pre>%s</pre>") % (
                    "Historical Blend tax repair completed: paid and invoiced using Odoo taxes. "
                    "No kitchen resend or Blend status notification. Original failure retained below.",
                    old_error, order.blend_tax_warning or "Current taxes match the original payload.",
                ))
            row.update(result="repaired", total=order.amount_total, odoo_tax=order.amount_tax,
                       invoice_id=invoice.id, invoice=invoice.name, payment_ids=order.payment_ids.ids)
            result.append(row)
        env.flush_all()
    return result


if globals().get("BLEND_REPAIR_RUN", True):
    commit_started = False
    try:
        if globals().get("BLEND_REPAIR_DB") != env.cr.dbname:
            raise RuntimeError("Set BLEND_REPAIR_DB to the exact target database name. Connected: %s" % env.cr.dbname)
        apply_repair = globals().get("BLEND_REPAIR_APPLY", False)
        if type(apply_repair) is not bool:
            raise RuntimeError("BLEND_REPAIR_APPLY must be True or False.")
        report = repair_blend_tax_orders(env, globals().get("BLEND_REPAIR_IDS"), apply=apply_repair)
        if apply_repair:
            commit_started = True
            env.cr.commit()
        else:
            env.cr.rollback()
        print(json.dumps({"database": env.cr.dbname, "mode": "APPLIED" if apply_repair else "PREVIEW",
                          "counts": {status: sum(row["result"] == status for row in report)
                                     for status in ("eligible", "repaired", "skipped")},
                          "orders": report}, ensure_ascii=False, indent=2))
    except Exception:
        env.cr.rollback()
        print("COMMIT INTERRUPTED: verify database state before retrying." if commit_started else
              "REPAIR ABORTED: this batch was rolled back; nothing was committed.")
        raise
