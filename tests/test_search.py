import unittest
from price_viewer.search import normalize


class SearchNormalizationTests(unittest.TestCase):
    def test_same_rules_for_different_scripts_and_unicode_forms(self):
        self.assertEqual(normalize('ГТА 6'), normalize('GTA 6'))
        self.assertEqual(normalize('ДИАБЛО 4'), normalize('Diablo 4'))
        self.assertEqual(normalize('ＡＢＣ'), normalize('abc'))

    def test_token_boundaries(self):
        self.assertEqual(normalize('Game: 2026 / PC'), 'game 2026 pc')

if __name__ == '__main__':
    unittest.main()
