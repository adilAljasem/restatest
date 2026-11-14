{
    'name': 'BOM Consumption Adjustments',
    'version': '19.0.1.2.2',
    'category': 'Manufacturing/Manufacturing',
    'summary': 'Review historical recipe consumption and post valued stock corrections',
    'license': 'LGPL-3',
    'author': "Resta",
    "website": "https://resta.sa",
    'depends': ['mrp_account', 'analytic'],
    'data': ['security/security.xml', 'security/ir.model.access.csv',
             'data/sequence.xml', 'views/adjustment_views.xml'],
    'installable': True,
}
