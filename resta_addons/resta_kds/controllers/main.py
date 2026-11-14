"""Public customer screen «طلبك جاهز» (TV at the counter), opened by the screen's secret link."""
from datetime import timedelta

from werkzeug.exceptions import NotFound

from odoo import fields, http
from odoo.http import request


class RestaKdsTracking(http.Controller):

    def _display(self, token):
        display = request.env["resta.kds.display"].sudo().search([("access_token", "=", token)], limit=1) \
            if token else None
        if not display:
            raise NotFound()
        return display

    @http.route("/resta/order-status/<string:token>", type="http", auth="public", website=False, sitemap=False)
    def tracking_page(self, token, **kw):
        display = self._display(token)
        html = request.env["ir.qweb"]._render("resta_kds.tracking_page", {
            "display": display, "token": token, "title": display.tracking_title or "طلبك",
            "company": display.company_id,
        })
        return request.make_response("<!DOCTYPE html>\n" + str(html), headers=[
            ("Content-Type", "text/html; charset=utf-8"), ("Cache-Control", "no-store"),
            ("X-Robots-Tag", "noindex")])

    @http.route("/resta/order-status/<string:token>/data", type="http", auth="public", methods=["GET"],
                website=False, sitemap=False)
    def tracking_data(self, token, **kw):
        display = self._display(token)
        return request.make_json_response(self._tracking_payload(display),
                                          headers={"Cache-Control": "no-store"})

    def _tracking_payload(self, display):
        Ticket = request.env["resta.kds.ticket"].sudo()
        since = fields.Datetime.now() - timedelta(hours=12)
        tickets = Ticket.search([("display_id", "=", display.id), ("create_date", ">=", since),
                                 ("cancelled", "=", False)])
        by_order = {}
        for t in tickets:
            by_order.setdefault(t.pos_order_id, request.env["resta.kds.ticket"].sudo())
            by_order[t.pos_order_id] |= t
        keep = fields.Datetime.now() - timedelta(minutes=display.tracking_ready_minutes or 10)
        preparing, ready = [], []
        for order, ts in by_order.items():
            number = order.tracking_number or (ts[:1].tracking_number or "")
            if not number:
                continue
            if all(t.done or t.stage_id.is_ready for t in ts):
                ready_at = max((t.ready_date or t.done_date or t.write_date) for t in ts)
                if ready_at >= keep:
                    ready.append((ready_at, number))
            elif not all(t.done for t in ts):
                preparing.append((min(t.create_date for t in ts), number))
        return {
            "title": display.tracking_title or "",
            "preparing": [n for _d, n in sorted(preparing)],
            "ready": [n for _d, n in sorted(ready, reverse=True)],
            "server_time": fields.Datetime.to_string(fields.Datetime.now()),
        }
