"""The restaurant system follows the Resta platform.

  - Apps are never installed, upgraded or removed from inside the restaurant: only the
    Resta support account (technical administrator) or an order from the platform can.
    The apps catalogue cannot be opened by the restaurant's users, even by link.
    The owner's settings page shows no «install» switches.
  - Nobody but the support account can give the technical administrator role.
  - Plan limits (users, points of sale) come from the platform.
  - Orders from the platform (unlock a feature, reach a level, install an app, journey plan)
    are stored here and run by a scheduled job, then reported back at the next sync.
The rules apply only to databases linked to the platform (account number set).
"""
import json
import logging

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.modules import module as odoo_module
from odoo.modules.registry import Registry
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)

PROTECTED_GROUPS = ["base.group_system", "resta_growth.group_resta_support"]


def _linked(env):
    return bool(env["ir.config_parameter"].sudo().get_param("resta.account_number"))


def _platform_user(env):
    """The superuser, the Resta support account (technical administrator), or a platform order."""
    return env.su or env.context.get("resta_platform_command") or env.user.has_group("base.group_system")


def _entitlements(env):
    try:
        return json.loads(env["ir.config_parameter"].sudo().get_param("resta.entitlements") or "{}")
    except ValueError:
        return {}


def _contact_line(env):
    try:
        manager = json.loads(env["ir.config_parameter"].sudo().get_param("resta.account_manager") or "{}")
    except ValueError:
        manager = {}
    name = manager.get("name")
    phone = manager.get("phone") or manager.get("whatsapp")
    return _("تواصل مع مدير حسابك %(name)s %(phone)s", name=name or "", phone=phone or "").strip()


# ---------------------------------------------------------------------- apps
# Screens of the apps catalogue (list, update, upgrade, import). Opening them is refused to the
# restaurant's users, even by link, so the catalogue never shows inside the restaurant system.
APP_SCREEN_MODELS = {"ir.module.module", "ir.module.category", "base.module.update", "base.module.upgrade",
                     "base.module.uninstall", "base.import.module"}


class IrActionsActWindow(models.Model):
    _inherit = "ir.actions.act_window"

    def _get_action_dict(self):
        # the web client loads actions as superuser: check the real user
        if self.res_model in APP_SCREEN_MODELS and _linked(self.env) and not (
                self.env.context.get("resta_platform_command")
                or self.env.uid == SUPERUSER_ID
                or self.env.user.has_group("base.group_system")):
            raise AccessError(_("قائمة التطبيقات تديرها ريستا. الميزات الجديدة تُفتح مع مستويات رحلة النمو "
                                "أو بطلب من مدير حسابك.") + "\n" + _contact_line(self.env))
        return super()._get_action_dict()


class IrModuleModule(models.Model):
    _inherit = "ir.module.module"

    def _resta_check_platform(self):
        if _linked(self.env) and not _platform_user(self.env):
            raise AccessError(_("إضافة التطبيقات وتحديثها تتم من إدارة ريستا. الميزات الجديدة تُفتح مع مستويات "
                                "رحلة النمو أو بطلب من مدير حسابك.") + "\n" + _contact_line(self.env))

    def button_install(self):
        self._resta_check_platform()
        return super().button_install()

    def button_immediate_install(self):
        self._resta_check_platform()
        return super().button_immediate_install()

    def button_uninstall(self):
        self._resta_check_platform()
        return super().button_uninstall()

    def button_immediate_uninstall(self):
        self._resta_check_platform()
        return super().button_immediate_uninstall()

    def button_upgrade(self):
        self._resta_check_platform()
        return super().button_upgrade()

    def button_immediate_upgrade(self):
        self._resta_check_platform()
        return super().button_immediate_upgrade()


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    def _resta_keep_modules_as_installed(self):
        """Ignore any «install / remove app» switch: the platform decides."""
        for module in self._get_classified_fields()["module"]:
            self[f"module_{module.name}"] = module.state in ("installed", "to upgrade")

    def execute(self):
        if _linked(self.env) and not _platform_user(self.env):
            self._resta_keep_modules_as_installed()
        return super().execute()

    @api.model
    def _resta_simplify_arch(self, arch):
        arch = super()._resta_simplify_arch(arch)
        # «install app» switches: for the support account only (the owner is not a technical admin)
        for setting in arch.iter("setting"):
            if any((f.get("name") or "").startswith("module_") for f in setting.iter("field")) \
                    and setting.get("invisible") != "1":
                setting.set("groups", "base.group_system")
        return arch


# ---------------------------------------------------------------------- rights and limits
class ResUsers(models.Model):
    _inherit = "res.users"

    def _resta_protected_group_ids(self):
        return {g.id for g in (self.env.ref(x, raise_if_not_found=False) for x in PROTECTED_GROUPS) if g}

    def _resta_check_protected(self, before):
        if not _linked(self.env) or _platform_user(self.env):
            return
        protected = self._resta_protected_group_ids()
        for user in self.sudo():
            gained = (set(user.all_group_ids.ids) & protected) - before.get(user.id, set())
            if gained:
                raise AccessError(_("صلاحية «مدير النظام التقني» خاصة بفريق ريستا ولا تُمنح من داخل المطعم."))

    def _resta_check_user_limit(self):
        if not _linked(self.env) or _platform_user(self.env):
            return
        limit = int(_entitlements(self.env).get("max_users") or 0)
        if not limit:
            return
        count = self.sudo().search_count([("share", "=", False), ("active", "=", True),
                                          ("id", "not in", self._resta_support_user_ids())])
        if count > limit:
            raise UserError(_("باقتك تسمح بـ %(n)s مستخدمين. لإضافة مستخدمين ارفع الباقة.", n=limit)
                            + "\n" + _contact_line(self.env))

    def _resta_support_user_ids(self):
        ids = [SUPERUSER_ID]
        for xmlid in ("base.user_admin", "base.public_user"):
            user = self.env.ref(xmlid, raise_if_not_found=False)
            if user:
                ids.append(user.id)
        return ids

    @api.model_create_multi
    def create(self, vals_list):
        users = super().create(vals_list)
        users._resta_check_protected({})
        if any(not u.share for u in users):
            users._resta_check_user_limit()
        return users

    def write(self, vals):
        before = {}
        if _linked(self.env) and not _platform_user(self.env) and ("group_ids" in vals or any(
                k.startswith(("in_group_", "sel_groups_")) for k in vals)):
            before = {u.id: set(u.all_group_ids.ids) for u in self.sudo()}
        res = super().write(vals)
        if before:
            self._resta_check_protected(before)
        if vals.get("active") or "share" in vals or "group_ids" in vals:
            self._resta_check_user_limit()
        return res


class ResGroups(models.Model):
    _inherit = "res.groups"

    def write(self, vals):
        if "user_ids" in vals and _linked(self.env) and not _platform_user(self.env):
            protected = self.env["res.users"]._resta_protected_group_ids()
            if protected & set(self.ids):
                raise AccessError(_("صلاحية «مدير النظام التقني» خاصة بفريق ريستا ولا تُمنح من داخل المطعم."))
        return super().write(vals)


class PosConfigLimit(models.Model):
    _inherit = "pos.config"

    @api.model_create_multi
    def create(self, vals_list):
        configs = super().create(vals_list)
        if _linked(self.env) and not _platform_user(self.env):
            limit = int(_entitlements(self.env).get("max_pos") or 0)
            if limit and self.sudo().search_count([]) > limit:
                raise UserError(_("باقتك تسمح بـ %(n)s نقاط بيع. لإضافة فرع أو نقطة بيع ارفع الباقة.", n=limit)
                                + "\n" + _contact_line(self.env))
        return configs


# ---------------------------------------------------------------------- platform orders
class RestaPlatformCommand(models.Model):
    _name = "resta.platform.command"
    _description = "أمر من منصة ريستا"
    _order = "remote_id"

    remote_id = fields.Integer(required=True, index=True)
    kind = fields.Char(required=True)
    payload = fields.Text(default="{}")
    state = fields.Selection([("pending", "بانتظار التنفيذ"), ("done", "نُفّذ"), ("failed", "فشل")],
                             default="pending", required=True, index=True)
    result = fields.Text()
    acked = fields.Boolean(help="أُبلغت المنصة بالنتيجة")
    done_date = fields.Datetime()

    _remote_uniq = models.Constraint("UNIQUE(remote_id)", "Duplicate platform order.")

    @api.model
    def _receive(self, commands):
        new = self.browse()
        for cmd in commands or []:
            if not cmd.get("id") or self.search_count([("remote_id", "=", int(cmd["id"]))]):
                continue
            new |= self.create({"remote_id": int(cmd["id"]), "kind": cmd.get("kind") or "",
                                "payload": json.dumps(cmd.get("payload") or {})})
        if new:
            cron = self.env.ref("resta_saas_tenant.ir_cron_resta_commands", raise_if_not_found=False)
            if cron:
                cron._trigger()
        return new

    @api.model
    def _acks(self):
        done = self.search([("state", "in", ("done", "failed")), ("acked", "=", False)])
        return done, [{"id": c.remote_id, "state": c.state, "result": c.result or ""} for c in done]

    @api.model
    def _cron_run(self):
        """Run the platform orders one by one (each in its own transaction)."""
        test_mode = bool(odoo_module.current_test)
        command = self.search([("state", "=", "pending")], limit=1)
        while command:
            command_id, kind = command.id, command.kind
            try:
                result, state = command.with_context(resta_platform_command=True).sudo()._run(), "done"
            except Exception as exc:  # noqa: BLE001 - reported to the platform
                _logger.exception("Resta: platform order %s failed", command.remote_id)
                if not test_mode:
                    self.env.cr.rollback()
                result, state = str(exc)[:1000], "failed"
            # installing an app reloads the registry: continue with a fresh environment
            env = api.Environment(self.env.cr, SUPERUSER_ID, {}) if kind == "install_modules" else self.env
            env["resta.platform.command"].browse(command_id).write(
                {"state": state, "result": result or "", "done_date": fields.Datetime.now()})
            if not test_mode:
                self.env.cr.commit()
            if Registry(self.env.cr.dbname) is not self.env.registry:
                # an app was installed: continue in a new job, on the new registry
                env.ref("resta_saas_tenant.ir_cron_resta_commands")._trigger()
                env["resta.saas.client"].sync()
                return
            command = self.search([("state", "=", "pending")], limit=1)
        self.env["resta.saas.client"].sync()

    def _run(self):
        self.ensure_one()
        payload = json.loads(self.payload or "{}")
        handler = getattr(self, "_run_%s" % self.kind, None)
        if not handler:
            raise UserError(_("أمر غير معروف: %s", self.kind))
        return handler(payload)

    def _run_install_modules(self, payload):
        Module = self.env["ir.module.module"]
        names = [n for n in payload.get("modules") or [] if isinstance(n, str)]
        Module.update_list()
        modules = Module.search([("name", "in", names)])
        missing = set(names) - set(modules.mapped("name"))
        todo = modules.filtered(lambda m: m.state not in ("installed", "to upgrade"))
        if todo:
            todo.button_immediate_install()
        notes = [_("ثُبّت: %s", ", ".join(todo.mapped("name")))] if todo else [_("مثبتة مسبقاً")]
        if missing:
            notes.append(_("غير موجود على السيرفر: %s", ", ".join(sorted(missing))))
        return " | ".join(notes)

    def _growth(self):
        if "resta.growth.feature" not in self.env:
            raise UserError(_("رحلة النمو غير مثبتة في هذا النظام."))
        return self.env["resta.growth.feature"], self.env["resta.growth.profile"]

    def _run_unlock_features(self, payload):
        Feature, _profile = self._growth()
        features = Feature.search([("key", "in", payload.get("keys") or [])])
        opened = features._unlock()
        return _("فُتح: %s", ", ".join(opened.mapped("name"))) if opened else _("مفتوحة مسبقاً")

    def _run_achieve_level(self, payload):
        _feature, Profile = self._growth()
        target = int(payload.get("level") or 0)
        reached = []
        for profile in Profile.search([]):
            for _i in range(10):
                current = profile.current_level_id
                if not current or current.sequence > target:
                    break
                state = profile._states_by_seq().get(current.sequence)
                if not state or state.state == "achieved":
                    break
                profile._achieve(state, skipped=True)
                reached.append(current.name)
        return _("تم: %s", "، ".join(reached)) if reached else _("لا تغيير")

    def _run_set_growth_plan(self, payload):
        _feature, Profile = self._growth()
        plan = payload.get("growth_plan")
        if plan not in ("trial", "standard", "paid_full"):
            raise UserError(_("خطة غير معروفة: %s", plan))
        Profile.search([]).write({"plan": plan})
        return plan

    @api.model
    def _apply_entitlements(self, entitlements):
        """Called at each sync: the journey plan follows the subscription."""
        icp = self.env["ir.config_parameter"].sudo()
        old = icp.get_param("resta.entitlements") or "{}"
        icp.set_param("resta.entitlements", json.dumps(entitlements or {}, ensure_ascii=False))
        try:
            old_plan = json.loads(old).get("growth_plan")
        except ValueError:
            old_plan = None
        plan = (entitlements or {}).get("growth_plan")
        if plan and plan != old_plan and "resta.growth.profile" in self.env:
            self.env["resta.growth.profile"].sudo().search([("plan", "!=", plan)]).write({"plan": plan})
