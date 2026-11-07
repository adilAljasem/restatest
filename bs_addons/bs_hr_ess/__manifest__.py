{
    'name': 'Employee Self-Service Suite (Jisr-like HR portal)',
    'summary': 'Installs the complete ESS suite: portal, attendance (geo / rotating QR / face), requests, '
               'documents & contracts, announcements, grievances, appraisals, HR dashboard, POS bridge',
    'description': 'Meta module. bs_hr_ess_payroll installs automatically when Odoo Payroll (Enterprise) is installed.',
    'version': '19.0.1.0.0',
    'category': 'Human Resources',
    'author': 'Basic Solution',
    'website': 'https://resta.sa',
    'license': 'LGPL-3',
    'depends': [
        'bs_hr_ess_base', 'bs_hr_ess_attendance', 'bs_hr_ess_requests', 'bs_hr_ess_documents',
        'bs_hr_ess_comms', 'bs_hr_ess_grievance', 'bs_hr_ess_appraisal', 'bs_hr_ess_reports', 'bs_hr_ess_pos',
    ],
    'data': [],
    'installable': True,
    'application': True,
}
