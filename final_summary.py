import sys; sys.path.insert(0, 'c:/Users/陈晨/Desktop/工单相关/newgongdan/cloudcost')
from sqlalchemy import create_engine, text; from app.config import settings
e = create_engine(settings.SYNC_DATABASE_URL)
names = {1:'AWS-main',2:'Azure-45d7a360',3:'GCP-xmind',4:'GCP-testmanger',5:'GCP-cb_export',6:'GCP-px_billing',7:'GCP-us_native'}
with e.connect() as c:
    print('ds_id | name             | rows   | total_cost')
    print('-'*55)
    for row in c.execute(text('SELECT data_source_id, count(*), SUM(cost) FROM billing_data GROUP BY data_source_id ORDER BY data_source_id')):
        nm = names.get(row[0], '?')
        print('%5s | %-16s | %6s | %.2f' % (row[0], nm, row[1], float(row[2])))
    tot = list(c.execute(text('SELECT count(*), SUM(cost) FROM billing_data')))[0]
    print('  ALL | %-16s | %6s | %.2f' % ('', tot[0], float(tot[1])))
