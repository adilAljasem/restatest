import json
import logging
from datetime import timedelta

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import html_sanitize

_logger = logging.getLogger(__name__)

STALE_MINUTES = 10
STATE_LABELS = {
    "trial": "تجربة", "active": "مشترك", "expired": "منتهي", "suspended": "موقوف",
    "cancelled": "ملغى", "provisioning": "قيد التجهيز", "failed": "—",
}


class RestaUpdate(models.Model):
    """Local copy of an announcement published on the Resta platform."""
    _name = "resta.update"
    _description = "Resta update / message"
    _order = "date desc, id desc"

    remote_id = fields.Integer(required=True, index=True)
    title = fields.Char(required=True)
    body = fields.Html(sanitize=True)
    kind = fields.Char()
    tag = fields.Char()
    important = fields.Boolean()
    date = fields.Datetime()
    read_user_ids = fields.Many2many("res.users", string="Read by")
    rated = fields.Boolean()

    _remote_uniq = models.Constraint("UNIQUE(remote_id)", "Duplicate update.")


class RestaSaasClient(models.AbstractModel):
    _name = "resta.saas.client"
    _description = "Resta platform client"

    # ------------------------------------------------------------- params
    def _icp(self):
        return self.env["ir.config_parameter"].sudo()

    def _conf(self):
        icp = self._icp()
        return {
            "master": (icp.get_param("resta.master_url") or "").rstrip("/"),
            "account": icp.get_param("resta.account_number") or "",
            "token": icp.get_param("resta.tenant_token") or "",
        }

    def _post(self, path, payload, timeout=8):
        conf = self._conf()
        if not (conf["master"] and conf["account"] and conf["token"]):
            raise UserError(_("هذا النظام غير مربوط بمنصة ريستا."))
        payload = dict(payload, account=conf["account"], token=conf["token"])
        resp = requests.post(conf["master"] + path, data=json.dumps(payload),
                             headers={"Content-Type": "application/json"}, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    # ----------------------------------------------------------------- usage
    def _usage(self):
        env = self.env
        since = fields.Datetime.now() - timedelta(days=30)
        usage = {
            "users": env["res.users"].sudo().search_count([("share", "=", False)]),
            "modules": env["ir.module.module"].sudo().search_count([("state", "=", "installed")]),
        }
        if "pos.order" in env:
            Order = env["pos.order"].sudo()
            dom = [("date_order", ">=", since), ("state", "in", ("paid", "done", "invoiced"))]
            usage["pos"] = env["pos.config"].sudo().search_count([])
            usage["orders_30d"] = Order.search_count(dom)
            usage["sales_30d"] = sum(a for (a,) in Order._read_group(dom, [], ["amount_total:sum"]))
            last = Order.search([], order="date_order desc", limit=1)
            usage["last_order"] = fields.Datetime.to_string(last.date_order) if last else None
        logins = [d for d in env["res.users"].sudo().search([("share", "=", False)]).mapped("login_date") if d]
        usage["last_login"] = fields.Datetime.to_string(max(logins)) if logins else None
        return usage

    # ------------------------------------------------------------------ sync
    @api.model
    def sync(self):
        icp = self._icp()
        Command = self.env["resta.platform.command"].sudo()
        acked, acks = Command._acks()
        try:
            last_id = int(icp.get_param("resta.last_update_id", "0") or 0)
            data = self._post("/resta/api/v1/sync", {"last_id": last_id, "usage": self._usage(), "acks": acks})
        except Exception as exc:  # noqa: BLE001 - never break the UI on a sync failure
            _logger.warning("Resta: sync with platform failed: %s", exc)
            icp.set_param("resta.last_sync_error", str(exc)[:500])
            return False
        icp.set_param("resta.subscription_state", data.get("state") or "")
        icp.set_param("resta.trial_end", data.get("trial_end") or "")
        icp.set_param("resta.subscription_end", data.get("subscription_end") or "")
        icp.set_param("resta.days_left", str(data.get("days_left") or 0))
        icp.set_param("resta.plan", data.get("plan") or "")
        icp.set_param("resta.account_manager", json.dumps(data.get("account_manager") or {}, ensure_ascii=False))
        icp.set_param("resta.support", json.dumps(data.get("support") or {}, ensure_ascii=False))
        icp.set_param("resta.last_sync", fields.Datetime.to_string(fields.Datetime.now()))
        icp.set_param("resta.last_sync_error", "")
        Update = self.env["resta.update"].sudo()
        for a in data.get("announcements") or []:
            if Update.search_count([("remote_id", "=", a["id"])]):
                continue
            Update.create({"remote_id": a["id"], "title": a.get("title") or "", "body": html_sanitize(a.get("body") or ""),
                           "kind": a.get("kind"), "tag": a.get("tag"), "important": a.get("important"),
                           "date": a.get("date")})
            last_id = max(last_id, int(a["id"]))
        icp.set_param("resta.last_update_id", str(last_id))
        acked.write({"acked": True})
        if "entitlements" in data:
            Command._apply_entitlements(data.get("entitlements"))
        Command._receive(data.get("commands"))
        return True

    def _sync_if_stale(self):
        last = self._icp().get_param("resta.last_sync")
        if not last or fields.Datetime.from_string(last) < fields.Datetime.now() - timedelta(minutes=STALE_MINUTES):
            self.sync()

    # ----------------------------------------------------------- UI (systray)
    @api.model
    def get_panel_data(self):
        conf = self._conf()
        if not conf["account"]:
            return {"linked": False}
        self.sudo()._sync_if_stale()
        icp = self._icp()
        uid = self.env.uid
        updates = self.env["resta.update"].sudo().search([], limit=30)
        state = icp.get_param("resta.subscription_state") or "trial"
        try:
            manager = json.loads(icp.get_param("resta.account_manager") or "{}")
            support = json.loads(icp.get_param("resta.support") or "{}")
        except ValueError:
            manager, support = {}, {}
        return {
            "linked": True,
            "account": conf["account"],
            "state": state,
            "state_label": STATE_LABELS.get(state, state),
            "days_left": int(icp.get_param("resta.days_left") or 0),
            "trial_end": icp.get_param("resta.trial_end") or "",
            "subscription_end": icp.get_param("resta.subscription_end") or "",
            "plan": icp.get_param("resta.plan") or "",
            "manager": manager,
            "support": support,
            "is_admin": self.env.user.has_group("base.group_system"),
            "updates": [{
                "id": u.id, "title": u.title, "body": u.body or "", "kind": u.kind, "tag": u.tag,
                "important": u.important, "date": fields.Datetime.to_string(u.date) if u.date else "",
                "unread": uid not in u.read_user_ids.ids, "rated": u.rated,
            } for u in updates],
        }

    @api.model
    def mark_read(self, update_ids=None):
        domain = [("id", "in", update_ids)] if update_ids else []
        for u in self.env["resta.update"].sudo().search(domain):
            u.read_user_ids = [(4, self.env.uid)]
        return True

    @api.model
    def send_feedback(self, kind, message="", rating=None, update_id=None):
        update = self.env["resta.update"].sudo().browse(update_id).exists() if update_id else None
        user = self.env.user
        self._post("/resta/api/v1/feedback", {
            "kind": kind, "message": message or "", "rating": rating,
            "announcement_id": update.remote_id if update else None,
            "user_name": user.name, "user_email": user.email or user.login,
        })
        if update and rating:
            update.rated = True
        return True

    @api.model
    def blocked_state(self):
        return self._icp().get_param("resta.subscription_state") in ("expired", "suspended")

    @api.model
    def get_lock_state(self):
        """Full-screen notice in the back office when the subscription has ended or the account is suspended.
        The Resta support account keeps working (to help the restaurant)."""
        conf = self._conf()
        icp = self._icp()
        state = icp.get_param("resta.subscription_state") or ""
        if not conf["account"] or state not in ("expired", "suspended"):
            return {"blocked": False}
        try:
            manager = json.loads(icp.get_param("resta.account_manager") or "{}")
            support = json.loads(icp.get_param("resta.support") or "{}")
        except ValueError:
            manager, support = {}, {}
        is_support = self.env.user.has_group("base.group_system")
        expired = state == "expired"
        return {
            "blocked": not is_support,
            "bypass": is_support,
            "state": state,
            "title": _("انتهى اشتراك ريستا") if expired else _("حساب ريستا موقوف"),
            "message": _("لتجديد الاشتراك ومواصلة استخدام النظام تواصل مع مدير حسابك. بياناتك محفوظة كاملة.")
            if expired else _("تم إيقاف الحساب مؤقتاً. تواصل مع مدير حسابك لإعادة التفعيل. بياناتك محفوظة كاملة."),
            "manager": manager,
            "support": support,
            "account": conf["account"],
            "renew_url": "%s/resta/login?account=%s" % (conf["master"], conf["account"]) if conf["master"] else "",
        }
