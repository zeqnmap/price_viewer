import unittest
from price_viewer.matching import matches


def category(url, game, name):
    return {'url': url, 'game': game, 'name': name}


class MatchingTests(unittest.TestCase):
    def test_same_game_and_kind_across_sources(self):
        a = category('https://funpay.com/lots/1/', 'Diablo 4', 'Аккаунты')
        b = category('https://playerok.com/diablo/accounts', 'Diablo 4', 'Accounts')
        self.assertTrue(matches(a, b))

    def test_different_game_or_kind_do_not_match(self):
        a = category('https://funpay.com/lots/1/', 'Diablo 4', 'Аккаунты')
        self.assertFalse(matches(a, category('https://playerok.com/x/y', 'Diablo 4', 'Currency')))
        self.assertFalse(matches(a, category('https://playerok.com/z/y', 'Diablo 3', 'Accounts')))
        self.assertFalse(matches(a, category('https://lzt.market/category/1/', 'Lolzteam', 'Diablo 4')))

    def test_unknown_category_only_matches_itself(self):
        a = category('https://funpay.com/lots/1/', 'Game', 'Разное')
        self.assertTrue(matches(a, a))
        self.assertFalse(matches(a, category('https://playerok.com/x/y', 'Game', 'Разное')))
