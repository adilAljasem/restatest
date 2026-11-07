# -*- coding: utf-8 -*-

from odoo import fields, models, _
from odoo.exceptions import UserError


class PettyCashImportWizard(models.TransientModel):
    _name = 'petty.cash.import.wizard'
    _description = 'Wizard for importing settlements from Excel'

    excel_file = fields.Binary('Excel File', required=True, attachment=True)
    excel_filename = fields.Char('Filename', required=True)
    petty_request_id = fields.Many2one('petty.cash.request', string='Petty Cash Request', required=True)

    def action_import(self):
        """Import the Excel file"""
        if not self.excel_file:
            raise UserError(_('Please select an Excel file to import.'))

        return self.petty_request_id.import_settlements_from_excel(
            self.excel_file,
            self.excel_filename
        )
