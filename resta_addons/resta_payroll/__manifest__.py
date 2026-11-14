{
    "name": "Resta - الرواتب السعودية",
    "summary": "Saudi payroll on Odoo Community: allowances, GOSI, overtime, absence, monthly batches with "
               "journal entry and bank sheet, end-of-service award",
    "version": "19.0.1.0.0",
    "category": "Human Resources/Payroll",
    "author": "Resta",
    "website": "https://resta.sa",
    "license": "OPL-1",
    "depends": ["hr", "hr_holidays", "account"],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/data.xml",
        "views/views.xml",
        "report/payslip_report.xml",
    ],
    "installable": True,
    "application": True,
}
