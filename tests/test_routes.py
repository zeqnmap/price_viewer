import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('DB_PATH', os.path.join(tempfile.gettempdir(), 'price-viewer-tests.sqlite'))
os.environ['START_CRAWLER'] = '0'
from price_viewer import create_app
from price_viewer import database


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path_patch = patch.object(database, 'DB_PATH', os.path.join(self.tmp.name, 'db.sqlite'))
        self.path_patch.start()
        self.client = create_app(start_crawler=False).test_client()
        self.patches = [patch('price_viewer.routes.' + name + '.search', return_value={'state':'idle'}) for name in ('lolz','playerok','plati')]
        for p in self.patches: p.start()
        with database.db() as c:
            c.execute('INSERT INTO categories(url,game,name,rating_checked) VALUES(?,?,?,?)', ('https://funpay.com/lots/1/', 'Test Game', 'Аккаунты', 'done'))
            c.execute('INSERT INTO categories(url,game,name,slug) VALUES(?,?,?,?)', ('https://playerok.com/test/accounts','Test Game','Accounts','{}'))
            c.execute('INSERT INTO categories(url,game,name) VALUES(?,?,?)', ('https://plati.market/catalog/','Plati.Market','Каталог'))
            for url, cat, source, price in [('https://funpay.com/offer/1','https://funpay.com/lots/1/','funpay',100), ('https://playerok.com/products/1','https://playerok.com/test/accounts','playerok',50), ('https://plati.market/itm/test/1','https://plati.market/catalog/','plati',75)]:
                c.execute('INSERT INTO listings(url,category_url,title,price,price_num,source,attributes) VALUES(?,?,?,?,?,?,?)',(url,cat,'Test Game account',str(price),price,source,'{}'))

    def tearDown(self):
        for p in self.patches: p.stop()
        self.path_patch.stop()
        self.tmp.cleanup()

    def test_source_filter_and_price_sort(self):
        data = self.client.get('/api/listings?q=Test&sort=price_asc').get_json()
        self.assertEqual([x['price_num'] for x in data['items']], [50,75,100])
        data = self.client.get('/api/listings?q=Test&source=plati').get_json()
        self.assertEqual([x['source'] for x in data['items']], ['plati'])

    def test_invalid_price_values_do_not_break_results(self):
        for value in ('NaN','inf','-1','text'):
            result = self.client.get('/api/listings', query_string={'q':'Test', 'min_price':value})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.get_json()['total'], 3)

    def test_missing_and_plati_refresh_are_rejected(self):
        self.assertEqual(self.client.post('/api/refresh', json={'category':'missing'}).status_code, 404)
        self.assertEqual(self.client.post('/api/refresh', json={'category':'https://plati.market/catalog/'}).status_code, 400)

    def test_pagination_and_nonexistent_category(self):
        self.assertEqual(self.client.get('/api/listings?page=-1').get_json()['page'], 1)
        self.assertEqual(self.client.get('/api/listings?category=unknown').get_json()['total'], 0)
