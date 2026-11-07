import logging

from odoo import _
from odoo.exceptions import UserError

LEGACY_MODULE_NAME = "odoo-pos-custom-branding"
MODULE_NAME = "odoo_pos_custom_branding"

_logger = logging.getLogger(__name__)


def _merge_dependencies(env, legacy_module_id, module_id):
    dependency_model = env["ir.module.module.dependency"].sudo()

    legacy_name_dependencies = dependency_model.search(
        [("name", "=", LEGACY_MODULE_NAME)],
    )
    for dependency in legacy_name_dependencies:
        duplicate = dependency_model.search(
            [
                ("id", "!=", dependency.id),
                ("module_id", "=", dependency.module_id.id),
                ("name", "=", MODULE_NAME),
            ],
            limit=1,
        )
        if duplicate:
            dependency.unlink()
        else:
            dependency.name = MODULE_NAME

    legacy_owned_dependencies = dependency_model.search(
        [("module_id", "=", legacy_module_id)],
    )
    for dependency in legacy_owned_dependencies:
        duplicate = dependency_model.search(
            [
                ("module_id", "=", module_id),
                ("name", "=", dependency.name),
            ],
            limit=1,
        )
        if duplicate:
            dependency.unlink()
        else:
            dependency.module_id = module_id


def _merge_xmlids(env):
    xmlid_model = env["ir.model.data"].sudo()
    legacy_xmlids = xmlid_model.search([("module", "=", LEGACY_MODULE_NAME)])
    target_by_name = {
        xmlid.name: xmlid
        for xmlid in xmlid_model.search(
            [
                ("module", "=", MODULE_NAME),
                ("name", "in", legacy_xmlids.mapped("name")),
            ],
        )
    }

    for legacy_xmlid in legacy_xmlids:
        target_xmlid = target_by_name.get(legacy_xmlid.name)
        if not target_xmlid:
            legacy_xmlid.module = MODULE_NAME
            continue
        if (
            target_xmlid.model != legacy_xmlid.model
            or target_xmlid.res_id != legacy_xmlid.res_id
        ):
            raise UserError(
                _(
                    "Cannot migrate branding XML ID %(xmlid)s because the old and new "
                    "module names point to different records.",
                    xmlid=legacy_xmlid.name,
                ),
            )
        legacy_xmlid.unlink()


def _repoint_module_xmlids(env, legacy_module_id, module_id):
    env["ir.model.data"].sudo().search(
        [
            ("model", "=", "ir.module.module"),
            ("res_id", "=", legacy_module_id),
        ],
    ).write({"res_id": module_id})


def _move_model_metadata(env, legacy_module_id, module_id):
    for model_name in ("ir.model.constraint", "ir.model.relation"):
        records = env[model_name].sudo().search([("module", "=", legacy_module_id)])
        records.write({"module": module_id})


def pre_init_hook(env):
    """Merge the invalid Odoo 18 technical name into its Odoo 19 replacement."""
    module_model = env["ir.module.module"].sudo()
    legacy_module = module_model.search([("name", "=", LEGACY_MODULE_NAME)], limit=1)
    if not legacy_module:
        return

    module = module_model.search([("name", "=", MODULE_NAME)], limit=1)
    if not module:
        raise UserError(
            _(
                "Cannot migrate module %(old)s because its replacement %(new)s is missing.",
                old=LEGACY_MODULE_NAME,
                new=MODULE_NAME,
            ),
        )

    _merge_dependencies(env, legacy_module.id, module.id)
    _merge_xmlids(env)
    _repoint_module_xmlids(env, legacy_module.id, module.id)
    _move_model_metadata(env, legacy_module.id, module.id)
    env.flush_all()

    # Bypass the installed-module unlink guard. All owned metadata that must be
    # preserved has been reassigned above; deleting the obsolete row also lets
    # Odoo's final module-state consistency check succeed.
    env.cr.execute("DELETE FROM ir_module_module WHERE id = %s", [legacy_module.id])
    module_model.invalidate_model()
    _logger.info(
        "Merged legacy module %s into %s before installation",
        LEGACY_MODULE_NAME,
        MODULE_NAME,
    )
