import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('DB_PATH', os.path.join(tempfile.gettempdir(), 'price-viewer-tests.sqlite'))
os.environ['START_CRAWLER'] = '0'
from price_viewer.database import db, init
from price_viewer.sources import plati


class PlatiTests(unittest.TestCase):
    def test_feedback_counts_are_product_reviews(self):
        html = '''<div id="reviews-tab-content"><span><svg><use href="#thumb-up"></use></svg>56 806</span><span><svg><use href="#thumb-down"></use></svg>2</span></div>'''
        self.assertEqual(plati.feedback(html), (56806, 2))
        self.assertEqual(plati.feedback('<html></html>'), (None, None))

    @patch('price_viewer.sources.plati.requests.Session')
    def test_catalogue_saves_canonical_link_and_feedback(self, session_class):
        init()
        session = session_class.return_value
        from unittest.mock import Mock
        api = Mock()
        api.json.return_value = {'retval': 0, 'content': {'items': [{'product_id': 7654321, 'name_url': 'test-item', 'name': [{'locale':'ru-RU', 'value':'Тестовый товар'}], 'currency':'RUB', 'price': 123.5, 'seller_name':'Тест'}], 'has_next_page': False}}
        detail = Mock()
        detail.text = '<div id="reviews-tab-content"><span><svg><use href="#thumb-up"></use></svg>17</span><span><svg><use href="#thumb-down"></use></svg>2</span></div>'
        session.get.side_effect = [api, detail]
        plati._fetch('test', 'test')
        with db() as c:
            row = c.execute('SELECT * FROM listings WHERE url=?', ('https://plati.market/itm/test-item/7654321',)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual((row['price_num'], row['seller_positive'], row['seller_negative']), (124, 17, 2))
        self.assertEqual(plati._jobs['test']['state'], 'ready')
