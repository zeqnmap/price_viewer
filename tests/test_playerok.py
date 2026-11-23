import os
import atexit
import tempfile
import unittest
from unittest.mock import patch

# Configure an isolated database before importing the application modules.
_temp = tempfile.TemporaryDirectory()
atexit.register(_temp.cleanup)
os.environ['DB_PATH'] = os.path.join(_temp.name, 'test.sqlite')
os.environ['START_CRAWLER'] = '0'
from price_viewer.database import db, init
from price_viewer.sources import playerok


class PlayerokCatalogueTests(unittest.TestCase):
    def setUp(self):
        init()

    @patch('price_viewer.sources.playerok.requests.Session')
    def test_saves_public_listing_fields(self, session_class):
        response = session_class.return_value.post.return_value
        response.json.return_value = {
            'items': [{'slug': 'test-offer', 'name': 'Test offer', 'price': 123,
                       'game': {'id': 'game-id', 'name': 'Game', 'slug': 'game'},
                       'category': {'id': 'category-id', 'name': 'Accounts', 'slug': 'accounts'},
                       'seller': {'username': 'seller', 'rating': 4.97, 'testimonialCounter': 58}}],
            'hasNextPage': False,
        }
        playerok._fetch('test-key', 'test', None)
        with db() as connection:
            item = connection.execute('SELECT * FROM listings WHERE source=?', ('playerok',)).fetchone()
            category = connection.execute('SELECT * FROM categories WHERE url=?', (item['category_url'],)).fetchone()
        self.assertEqual(item['url'], 'https://playerok.com/products/test-offer')
        self.assertEqual(item['price_num'], 123)
        self.assertEqual(item['seller'], 'seller')
        self.assertEqual(item['seller_rating'], 4.97)
        self.assertEqual(item['seller_review_count'], 58)
        self.assertEqual(category['name'], 'Accounts')
        self.assertEqual(playerok._jobs['test-key']['state'], 'ready')

if __name__ == '__main__':
    unittest.main()
