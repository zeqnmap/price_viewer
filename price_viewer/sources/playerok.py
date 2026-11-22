"""Search the same public catalogue endpoint used by Playerok's site."""
import json
import threading
import time
from urllib.parse import quote
import requests
from ..database import db, now

API = 'https://api.playerok.com/v1/catalog/items'
_lock = threading.Lock()
_jobs = {}


def status():
    with _lock:
        latest = max(_jobs.values(), key=lambda job: job['time'], default=None)
        result = dict(latest) if latest else None
    if not result:
        return {'id': 'playerok', 'name': 'Playerok', 'status': 'active', 'message': 'Поиск по каталогу включён.'}
    if result.get('state') == 'error':
        return {'id': 'playerok', 'name': 'Playerok', 'status': 'unavailable', 'message': result['error']}
    return {'id': 'playerok', 'name': 'Playerok', 'status': 'active', 'message': 'Поиск по каталогу включён.'}


def search(query, category=None):
    query = query.strip()[:200]
    if len(query) < 2 and not category:
        return {'state': 'idle'}
    key = (category or '') + ':' + query.casefold()
    with _lock:
        existing = _jobs.get(key)
        if existing and (existing['state'] == 'loading' or time.monotonic() - existing['time'] < 120):
            return dict(existing)
        if sum(job['state'] == 'loading' for job in _jobs.values()) >= 2:
            return {'state': 'busy'}
        _jobs[key] = {'state': 'loading', 'time': time.monotonic()}
        threading.Thread(target=_fetch, args=(key, query, category), daemon=True).start()
        return {'state': 'loading'}


def _fetch(key, query, category):
    result = {'state': 'ready', 'time': time.monotonic(), 'pages': 0, 'more': False}
    try:
        filters = {}
        if query:
            filters['searchQuery'] = query
        if category:
            ids = json.loads(category)
            filters.update(gameIds=[ids['game']], gameCategoryIds=[ids['category']])
        cursor = None
        session = requests.Session()
        for page in range(5):
            page_args = {'size': 40}
            if cursor:
                page_args['cursor'] = cursor
            response = session.post(API, json={'filter': filters, 'page': page_args}, timeout=20)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get('items'), list):
                raise ValueError('Invalid catalogue response')
            records, categories = [], {}
            for position, item in enumerate(data['items']):
                game, cat = item.get('game') or {}, item.get('category') or {}
                slug, game_slug, cat_slug = item.get('slug'), game.get('slug'), cat.get('slug')
                price, title = item.get('price'), item.get('name')
                if not all([slug, game_slug, cat_slug, game.get('id'), cat.get('id'), title]) or isinstance(price, bool) or not isinstance(price, (int, float)) or price < 0:
                    continue
                category_url = 'https://playerok.com/' + quote(game_slug, safe='') + '/' + quote(cat_slug, safe='')
                categories[category_url] = (game.get('name') or game_slug, cat.get('name') or cat_slug, json.dumps({'game': game['id'], 'category': cat['id']}))
                seller = item.get('seller') or {}
                rating = seller.get('rating')
                count = seller.get('testimonialCounter')
                rating = rating if isinstance(rating, (int, float)) and not isinstance(rating, bool) and 0 < rating <= 5 and isinstance(count, int) and count > 0 else None
                count = count if isinstance(count, int) and not isinstance(count, bool) and count >= 0 else None
                records.append(('https://playerok.com/products/' + quote(slug, safe=''), category_url, str(title), f'{price:g} ₽', str(seller.get('username') or ''), 0, now(), '{}', page * 40 + position, price, 'playerok', rating, count))
            with db() as c:
                c.executemany('INSERT INTO categories(url,game,name,slug) VALUES(?,?,?,?) ON CONFLICT(url) DO UPDATE SET game=excluded.game,name=excluded.name,slug=excluded.slug', [(url,*values) for url,values in categories.items()])
                c.executemany('INSERT INTO listings (url,category_url,title,price,seller,online,updated,attributes,position,price_num,source,seller_rating,seller_review_count) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET category_url=excluded.category_url,title=excluded.title,price=excluded.price,seller=excluded.seller,updated=excluded.updated,position=excluded.position,price_num=excluded.price_num,seller_rating=excluded.seller_rating,seller_review_count=excluded.seller_review_count', records)
            result.update(pages=page + 1, more=bool(data.get('hasNextPage')))
            cursor = data.get('endCursor')
            if not result['more'] or not cursor or not data['items']:
                break
            time.sleep(0.3)
    except requests.HTTPError as exc:
        result.update(state='error', error=f'Playerok: HTTP {exc.response.status_code}')
    except (requests.RequestException, ValueError, KeyError, TypeError):
        result.update(state='error', error='Не удалось получить данные Playerok')
    finally:
        result['time'] = time.monotonic()
        with _lock:
            _jobs[key] = result
