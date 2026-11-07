"""One-order accounting workaround; no invented combo components or kitchen resend.

Paste into a fresh administrative Odoo shell. The wrapper targets only the
confirmed database and commits after verification. Tests load with RUN=False.
"""
import copy
import json
from unittest.mock import patch

from odoo import Command


def repair_blend_combo(env, order_id=2309, line_id=9596, template_id=1240):
    def require(condition, message):
        if not condition:
            raise RuntimeError(message)

    require(env.su or env.user.has_group("base.group_system"), "Administrator shell required")
    with env.cr.savepoint():
        order = env["pos.order"].sudo().browse(order_id).exists()
        require(order, "Order not found")
        order.lock_for_update()
        order.invalidate_recordset()
        order = order.with_company(order.company_id).with_context(
            blend_financial_processing=True, blend_skip_status_sync=True,
            blend_inbound_sync=True, generate_pdf=False, mail_notify_force_send=False,
        )
        reference = "hunger-station: 1876793267"
        require(order.blend_partner_reference == reference, "Wrong order reference")
        zero = order.currency_id.is_zero
        marker = "BLEND-REPAIR-%s-%s" % (order_id, template_id)

        def verified():
            invoice = order.account_move
            return bool(
                order.state == "done" and order.blend_financial_state == "done"
                and invoice and invoice.state == "posted" and len(order.payment_ids) == 1
                and zero(order.amount_total - 269) and zero(order.amount_paid - 269)
                and zero(invoice.amount_total - 269) and zero(invoice.amount_residual)
                and zero(invoice.amount_tax - order.amount_tax)
            )

        if verified():
            return {"result": "already_done", "order": order.id, "invoice": order.account_move.name}
        require(order.state == "draft" and not order.payment_ids and not order.account_move
                and zero(order.amount_paid) and not order.picking_ids,
                "Order already has payment, invoice or stock operations; no changes made")
        require(order.blend_financial_required and order.blend_can_finalize
                and not order.blend_refund_required, "Order is not eligible for financial completion")
        require(order.session_id.state == "opened", "Original POS session is not open")
        require("blend_tax_warning" in order._fields, "Non-blocking tax fix must be installed")
        line = order.lines.filtered(lambda record: record.id == line_id)
        require(len(line) == 1, "Expected order line not found")
        original = line.product_id.with_company(order.company_id)
        require(original.product_tmpl_id.id == template_id and original.type == "combo",
                "Expected original Combo product not found")
        require(not line.combo_line_ids and not line.combo_parent_id
                and line.qty == 1 and zero(line.blend_gross_total - 79),
                "Combo quantity, amount or components have changed")
        require(order.lines.filtered(lambda record: record.product_id.type == "combo") == line,
                "Unexpected additional Combo lines")
        payload = copy.deepcopy(order.blend_payload)
        require(payload and zero(float(payload["total"]) - 269)
                and zero(order.amount_total - 269), "Unexpected original order total")
        other_lines = order.lines - line
        taxes = other_lines.tax_ids
        require(len(taxes) == 1 and taxes.active and taxes.company_id == order.company_id
                and taxes.amount_type == "percent" and taxes.amount == 15 and taxes.price_include,
                "Expected one existing company tax: 15 percent included")
        others = [(r.id, r.product_id.id, r.qty, r.blend_gross_total) for r in other_lines]
        old_status, old_error = order.blend_status, order.blend_financial_error
        old_product = original.id

        def prep_snapshot():
            tickets = order.env["pos.prep.order"].search([("pos_order_id", "=", order.id)], order="id")
            prep_lines = tickets.prep_line_ids.sorted("id")
            states = order.env["pos.prep.state"].search([("prep_line_id", "in", prep_lines.ids)], order="id")
            return (tickets.read(["write_date"]), prep_lines.read(["product_id", "quantity", "cancelled", "write_date"]),
                    states.read(["todo", "stage_id", "write_date"]))

        before_prep = prep_snapshot()
        outbound = order.env["blend.integration.log"].search([
            ("order_id", "=", order.id), ("direction", "=", "outbound"),
        ], order="id")
        before_outbound = outbound.read(["state", "request_payload", "write_date"])

        def no_preparation(model, target_id, *args, **kwargs):
            require(target_id == order.id, "Unexpected preparation request")
            return None

        with patch.object(type(order.env["pos.prep.order"]), "process_order", no_preparation), patch(
            "requests.sessions.Session.request", side_effect=RuntimeError("External HTTP disabled during repair"),
        ):
            Product = order.env["product.product"]
            require(not Product.with_context(active_test=False).search_count([("default_code", "=", marker)]),
                    "Repair product already exists but order is incomplete; review before retrying")
            replacement = Product.create({
                "name": original.name + " [Blend accounting repair %s]" % order.id,
                "default_code": marker, "type": "service", "company_id": order.company_id.id,
                "available_in_pos": False, "sale_ok": False, "purchase_ok": False,
                "categ_id": original.categ_id.id, "uom_id": original.uom_id.id,
                "list_price": 79, "taxes_id": [Command.set(taxes.ids)],
                "supplier_taxes_id": [Command.clear()],
                "property_account_income_id": original.property_account_income_id.id,
            })
            line.write({"product_id": replacement.id})
            order.write({"blend_taxes_prepared": False,
                         "blend_financial_attempts": order.blend_financial_attempts + 1})
            order._blend_finalize_accounting()
            require(verified(), "Invoice/payment verification failed")
            require(order.blend_payload == payload and order.blend_status == old_status,
                    "Original payload/status changed unexpectedly")
            require(others == [(r.id, r.product_id.id, r.qty, r.blend_gross_total) for r in other_lines],
                    "Other order lines changed unexpectedly")
            require(prep_snapshot() == before_prep, "Kitchen data changed unexpectedly")
            logs = order.env["blend.integration.log"].search([
                ("order_id", "=", order.id), ("direction", "=", "outbound"),
            ], order="id")
            require(logs.read(["state", "request_payload", "write_date"]) == before_outbound,
                    "Outbound Blend data changed unexpectedly")
            order.message_post(body=(
                "One-order Blend accounting repair. Original Combo product %s replaced on line %s "
                "by accounting-only service product %s. Gross 79 retained; total 269 paid and invoiced. "
                "Combo selections were absent: NO component stock deduction and NO kitchen resend. "
                "Original payload and remote status preserved. Prior error: %s. Tax warning: %s"
            ) % (old_product, line.id, replacement.id, old_error, order.blend_tax_warning or "None"))
        env.flush_all()
        return {"result": "repaired", "order": order.id, "state": order.state,
                "financial": order.blend_financial_state, "total": order.amount_total,
                "tax": order.amount_tax, "paid": order.amount_paid,
                "invoice": order.account_move.name, "residual": order.account_move.amount_residual,
                "payment_ids": order.payment_ids.ids}


if globals().get("BLEND_COMBO_REPAIR_RUN", True):
    commit_started = False
    try:
        if env.cr.dbname != "walsoodi-pino-main-34539673":
            raise RuntimeError("Wrong database: " + env.cr.dbname)
        result = repair_blend_combo(env)
        commit_started = True
        env.cr.commit()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception:
        env.cr.rollback()
        print("Commit interrupted: inspect order before retrying." if commit_started else
              "Repair failed: all changes rolled back.")
        raise
