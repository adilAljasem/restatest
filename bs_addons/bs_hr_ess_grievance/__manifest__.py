{
    'name': 'ESS Grievances & Disciplinary',
    'summary': 'Confidential complaints, whistleblowing and appeals handled by a grievance committee, '
               'disciplinary actions with a penalty ladder and portal appeal',
    'version': '19.0.1.0.0',
    'category': 'Human Resources/Employees',
    'author': 'Basic Solution',
    'website': 'https://resta.sa',
    'license': 'LGPL-3',
    'depends': ['bs_hr_ess_base', 'hr', 'mail'],
    'data': [
        'security/ir.model.access.csv',
        'security/grievance_rules.xml',
        'data/ir_sequence_data.xml',
        'data/violation_type_data.xml',
        'views/grievance_views.xml',
        'views/disciplinary_views.xml',
        'views/res_config_settings_views.xml',
        'views/menus.xml',
        'views/portal_templates.xml',
    ],
    'installable': True,
    'application': False,
}
