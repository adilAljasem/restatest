import io
import json
from odoo import fields, models, _
from odoo.exceptions import UserError
try:
    from odoo.tools.misc import xlsxwriter
except ImportError:
    import xlsxwriter


class InventoryStockExport(models.TransientModel):
    _name="stock.adjustment.report.export"
    _description = "Stock Report Export"

    start_date = fields.Date('Start Date', default=lambda self: fields.Date.today())
    end_date = fields.Date('End Date', default=lambda self: fields.Date.today())

    def action_report_excel(self):
        record = []
        if self.start_date and self.end_date:
            datas = self.env['stock.inventory'].sudo().search([('date','>=',self.start_date),('date','<=',self.end_date)])
            for i in datas:
                for line in i.line_ids:
                    vals = {
                        'location_id':i.location_id.display_name,
                        'product':line.product_id.display_name,
                        'product_uom':line.product_uom_id.name,
                        'lot_number':line.prod_lot_id.name,
                        'price_value':line.price_value,
                        'available_quantity':line.available_quantity,
                        'reserved_quantity':line.reserved_quantity,
                        'quantity':line.quantity,
                        'inventory_quantity':line.inventory_quantity
                    }
                    record.append(vals)
            if record:
                data = {
                    'data': record,
                    'start_date': str(self.start_date),
                    'end_date': str(self.end_date),
                }
                return {
                    'type': 'ir.actions.report',
                    'data': {
                        'model': 'stock.adjustment.report.export',
                        'options': json.dumps(data),
                        'output_format': 'xlsx',
                        'report_name': 'Excel Reports',
                    },
                    'report_type': 'stock_adjustment',
                }
        else:
            raise UserError(_("you have not selected start and end date."))

    def get_xlsx_report(self, data, response):
        datas = data['data']
        start_date = data['start_date']
        end_date = data['end_date']
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        sheet = workbook.add_worksheet()
        sheet.set_margins(0.5, 0.5, 0.5, 0.5)
        cell_format = workbook.add_format(
            {'font_size': '12px', 'align': 'left'})
        header_style = workbook.add_format(
            {'font_name': 'Times', 'bold': True, 'left': 1, 'bottom': 1,
             'right': 1, 'top': 1, 'align': 'center'})
        text_style = workbook.add_format(
            {'font_name': 'Times', 'left': 1, 'bottom': 1, 'right': 1, 'top': 1,
             'align': 'left'})
        head = workbook.add_format(
            {'align': 'center', 'bold': True, 'font_size': '20px'})
        bold_format = workbook.add_format(
            {'bold': True, 'font_size': '10px', 'align': 'left'})
        txt = workbook.add_format({'font_size': '10px', 'align': 'left'})

        sheet.write('A4', 'From Date: ', bold_format)
        sheet.write('B4', start_date, txt)
        sheet.write('A5', 'To Date: ', bold_format)
        sheet.write('B5', end_date, txt)
        sheet.merge_range('C1:G2', 'Inventory Stock Movement Report', head)
        headers = ['Location', 'Product', 'UOM', 'Lot Number','Diff. Valuation', 'Available Quantity','Reserved Quantity','Quantity On Hand','Counted Quantity']

        for col, header in enumerate(headers):
            sheet.write(6, col, header, header_style)

        sheet.set_column('A:A', 23, cell_format)
        sheet.set_column('B:B', 43, cell_format)
        sheet.set_column('C:C', 13, cell_format)
        sheet.set_column('D:D', 25, cell_format)
        sheet.set_column('E:E', 23, cell_format)
        sheet.set_column('F:G', 25, cell_format)
        sheet.set_column('H:I', 25, cell_format)
        sheet.set_column('J:K', 15, cell_format)
        sheet.set_column('L:M', 15, cell_format)
        row = 7
        number = 1
        for val in datas:
            sheet.write(row, 0, val['location_id'], text_style)
            sheet.write(row, 1, val['product'], text_style)
            sheet.write(row, 2, val['product_uom'], text_style)
            sheet.write(row, 3, val['lot_number'], text_style)
            sheet.write(row, 4, val['price_value'], text_style)
            sheet.write(row, 5, val['available_quantity'], text_style)
            sheet.write(row, 6, val['reserved_quantity'], text_style)
            sheet.write(row, 7, val['quantity'], text_style)
            sheet.write(row, 8, val['inventory_quantity'], text_style)
            row += 1
            number += 1
        workbook.close()
        output.seek(0)
        response.stream.write(output.read())
        output.close()
