# -*- coding: utf-8 -*-
"""Attach the sound script to the kitchen display asset bundles.

Odoo Enterprise's kitchen display ships its own asset bundle, and its name
has changed between versions. Instead of hard-coding one name, we read the
manifests of installed modules, find bundles that look like a kitchen /
preparation display bundle, and append our script to each of them with
``ir.asset`` records.
"""
import logging
import re

_logger = logging.getLogger(__name__)

JS_PATH = "pos_kds_sound/static/src/kds_sound.js"
ASSET_PREFIX = "pos_kds_sound: "
BUNDLE_PATTERN = re.compile(r"(prep|preparation|kitchen|kds)", re.IGNORECASE)
EXCLUDE_PATTERN = re.compile(r"(test|tour|qunit|unit)", re.IGNORECASE)
# Already declared in __manifest__.py
STATIC_BUNDLES = {"web.assets_backend", "pos_preparation_display.assets"}


def _get_manifest(module_name):
    """Return a module manifest as a dict, across Odoo versions."""
    try:
        from odoo.modules.module import get_manifest
        manifest = get_manifest(module_name)
        if manifest:
            return dict(manifest)
    except Exception:  # noqa: BLE001
        pass
    try:  # Odoo 19+ Manifest API
        from odoo.modules.module import Manifest
        manifest = Manifest.for_addon(module_name)
        if manifest:
            return dict(manifest)
    except Exception:  # noqa: BLE001
        pass
    return {}


def discover_bundles(env):
    modules = env["ir.module.module"].sudo().search(
        [("state", "in", ("installed", "to install", "to upgrade"))]
    )
    bundles = set()
    for name in modules.mapped("name"):
        if name == "pos_kds_sound":
            continue
        assets = _get_manifest(name).get("assets") or {}
        for bundle in assets:
            if BUNDLE_PATTERN.search(bundle) and not EXCLUDE_PATTERN.search(bundle):
                bundles.add(bundle)
    return bundles - STATIC_BUNDLES


def sync_assets(env, extra_bundles=None):
    """Create/remove ir.asset records so our script is in every KDS bundle."""
    wanted = discover_bundles(env)
    for bundle in extra_bundles or []:
        bundle = bundle.strip()
        if bundle and bundle not in STATIC_BUNDLES:
            wanted.add(bundle)

    Asset = env["ir.asset"].sudo()
    existing = Asset.search([("name", "=like", ASSET_PREFIX + "%")])
    existing_by_bundle = {a.bundle: a for a in existing}

    for bundle, asset in existing_by_bundle.items():
        if bundle not in wanted:
            asset.unlink()
    for bundle in wanted:
        if bundle not in existing_by_bundle:
            Asset.create({
                "name": ASSET_PREFIX + bundle,
                "bundle": bundle,
                "directive": "append",
                "path": JS_PATH,
                "sequence": 999,
            })
    _logger.info("pos_kds_sound attached to bundles: %s", sorted(wanted | STATIC_BUNDLES))
    return sorted(wanted | STATIC_BUNDLES)


def post_init_hook(env):
    settings = env["pos.kds.sound.settings"].sudo().get_settings()
    bundles = sync_assets(env)
    settings.bundles_info = "\n".join(bundles)


def uninstall_hook(env):
    env["ir.asset"].sudo().search([("name", "=like", ASSET_PREFIX + "%")]).unlink()
