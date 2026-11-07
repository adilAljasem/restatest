# -*- coding: utf-8 -*-
"""Link The Dash into every installed app.

The Dash only depends on point_of_sale, so menus under other apps cannot live
in its XML. This model adds a "لوحة البيانات" entry to the reporting menu of
each installed app (opening the matching Operations tab) and files Odoo's
spreadsheet dashboards under The Dash, so the system has one dashboard entry.
It runs on every registry load: apps installed later get their entry too.
Records use the "__the_dash__" xmlid namespace so module updates never
garbage-collect them; uninstall_hook removes them.
"""
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)

NS = '__the_dash__'
# key -> (parent reporting menu xmlid, Operations tab to open, sequence)
APP_MENUS = {
    'inventory': ('stock.menu_warehouse_report', 'inventory', 1),
    'purchase': ('purchase.purchase_report_main', 'purchase', 1),
    'accounting': ('account.menu_finance_reports', 'accounting', 1),
    'manufacturing': ('mrp.menu_mrp_reporting', 'manufacturing', 1),
    'people': ('hr.hr_menu_hr_reports', 'people', 1),
    'attendance': ('hr_attendance.menu_hr_attendance_reporting', 'people', 1),
    'sales': ('sale.menu_sale_report', 'pos', 1),
}
SPREADSHEET_ROOT = 'spreadsheet_dashboard.spreadsheet_dashboard_menu_root'


class TheDashAppMenus(models.AbstractModel):
    _name = 'the.dash.app.menus'
    _description = 'The Dash - entries in every app'

    def _register_hook(self):
        super()._register_hook()
        try:
            with self.env.cr.savepoint():
                self.sudo()._sync()
        except Exception:  # noqa: BLE001 - never block the registry on menus
            _logger.exception('The Dash: could not sync app menus')

    def _xmlid_record(self, name):
        return self.env['ir.model.data']._xmlid_to_res_model_res_id('%s.%s' % (NS, name), raise_if_not_found=False)

    def _store(self, name, record):
        self.env['ir.model.data'].create({
            'module': NS, 'name': name, 'model': record._name, 'res_id': record.id, 'noupdate': True})

    @api.model
    def _sync(self):
        root = self.env.ref('the_dash.menu_the_dash_root', raise_if_not_found=False)
        if not root:
            return
        groups = [g for g in (self.env.ref('the_dash.group_the_dash_viewer', raise_if_not_found=False),)
                  if g]
        for key, (parent_xmlid, focus, seq) in APP_MENUS.items():
            parent = self.env.ref(parent_xmlid, raise_if_not_found=False)
            if not parent:
                continue
            _model, menu_id = self._xmlid_record('menu_%s' % key)
            if menu_id and self.env['ir.ui.menu'].browse(menu_id).exists():
                continue
            action = self.env['ir.actions.client'].create({
                'name': 'لوحة البيانات', 'tag': 'the_dash.embedded', 'params': {'focus': focus}})
            self._store('action_%s' % key, action)
            menu = self.env['ir.ui.menu'].create({
                'name': 'لوحة البيانات', 'parent_id': parent.id, 'sequence': seq,
                'action': 'ir.actions.client,%d' % action.id,
                'group_ids': [(6, 0, [g.id for g in groups] + parent.group_ids.ids)],
            })
            self._store('menu_%s' % key, menu)
            _logger.info('The Dash: linked into %s', parent_xmlid)

        # Odoo spreadsheet dashboards -> under The Dash, so there is one dashboard app
        sheet = self.env.ref(SPREADSHEET_ROOT, raise_if_not_found=False)
        if sheet and sheet.parent_id != root:
            ICP = self.env['ir.config_parameter']
            ICP.set_param('the_dash.spreadsheet_root_backup', '%s|%s' % (sheet.parent_id.id or '', sheet.sequence))
            sheet.write({'parent_id': root.id, 'sequence': 50, 'name': 'لوحات الجداول'})

    @api.model
    def _unlink_all(self):
        data = self.env['ir.model.data'].sudo().search([('module', '=', NS)])
        for d in data.filtered(lambda d: d.model == 'ir.ui.menu'):
            self.env['ir.ui.menu'].browse(d.res_id).exists().unlink()
        for d in data.filtered(lambda d: d.model == 'ir.actions.client'):
            self.env['ir.actions.client'].browse(d.res_id).exists().unlink()
        data.unlink()
        sheet = self.env.ref(SPREADSHEET_ROOT, raise_if_not_found=False)
        backup = self.env['ir.config_parameter'].sudo().get_param('the_dash.spreadsheet_root_backup')
        if sheet:
            parent, _sep, seq = (backup or '|').partition('|')
            sheet.write({'parent_id': int(parent) if parent else False,
                         'sequence': int(seq) if seq else 10, 'name': 'Dashboards'})
