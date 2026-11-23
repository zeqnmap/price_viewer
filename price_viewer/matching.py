"""Conservative matching of categories from different marketplaces."""
from .search import normalize

KINDS = {
    'accounts': {'account', 'accounts', 'akkaunt', 'akkaunty', 'akkauntov', 'uchetnye zapisi'},
    'currency': {'currency', 'coins', 'coin', 'valyuta', 'igrovaya valyuta', 'zoloto', 'gold', 'monety'},
    'items': {'items', 'item', 'predmety', 'veshchi'},
    'boost': {'boost', 'boosting', 'prokachka', 'bust'},
    'keys': {'keys', 'key', 'klyuchi', 'klyuch'},
}


def kind(name):
    value = normalize(name)
    return next((key for key, aliases in KINDS.items() if value in aliases), None)


def identity(row):
    url = row['url']
    if url.startswith('https://lzt.market/'):
        # A game-only category does not establish the kind of offer.
        return normalize(row['name']), None
    return normalize(row['game']), kind(row['name'])


def matches(selected, candidate):
    if selected['url'] == candidate['url']:
        return True
    game, category_kind = identity(selected)
    other_game, other_kind = identity(candidate)
    return bool(game and category_kind and game == other_game and category_kind == other_kind)
