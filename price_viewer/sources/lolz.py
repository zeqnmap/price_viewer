"""Read public listing fields from the authenticated Lolz Market API."""
import os
import json
import re
import threading
import time
from urllib.parse import urljoin
import requests
from ..database import db, now

API = 'https://api.lzt.market/'
_lock = threading.Lock()
_jobs = {}


def status():
    if not os.environ.get('LOLZ_TOKEN'):
        return {'id': 'lolz', 'name': 'Lolzteam', 'status': 'unavailable', 'message': 'Укажите LOLZ_TOKEN в .env и перезапустите приложение.'}
    with _lock:
        jobs = list(_jobs.values())
    error = next((x.get('error') for x in reversed(jobs) if x.get('error')), None)
    return {'id': 'lolz', 'name': 'Lolzteam', 'status': 'unavailable' if error else 'active', 'message': error or 'Поиск по каталогу Lolzteam включён.'}


def search(query, slug=None):
    """Schedule a bounded refresh; repeated polls reuse the cached search."""
    query = query.strip()[:200]
    if (len(query) < 2 and not slug) or not os.environ.get('LOLZ_TOKEN'):
        return {'state': 'idle'}
    if slug and not re.fullmatch(r'[a-zA-Z0-9_-]+', slug):
        return {'state': 'error', 'error': 'Неизвестная категория'}
    key = (slug or '') + ':' + query.casefold()
    with _lock:
        job = _jobs.get(key)
        if job and (job['state'] == 'loading' or time.monotonic() - job['time'] < 120):
            return dict(job)
        if sum(x['state'] == 'loading' for x in _jobs.values()) >= 2:
            return {'state': 'busy'}
        _jobs[key] = {'state': 'loading', 'time': time.monotonic()}
        threading.Thread(target=_fetch, args=(key, query, slug), daemon=True).start()
        return {'state': 'loading'}


def _fetch(key, query, slug):
    result = {'state': 'ready', 'time': time.monotonic(), 'pages': 0, 'more': False}
    try:
        session = requests.Session()
        session.headers.update({'Authorization': 'Bearer ' + os.environ['LOLZ_TOKEN'], 'User-Agent': 'PriceViewer/1.0'})
        for page in range(1, 6):
            response = session.get(urljoin(API, (slug + '/') if slug else ''), params={'title': query, 'page': page} if query else {'page': page}, timeout=25)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get('items'), list):
                raise ValueError('Некорректный ответ API')
            records = []
            categories = {}
            for position, item in enumerate(data['items']):
                item_id = item.get('item_id')
                title = item.get('title')
                price = item.get('rub_price')
                if not item_id or not title or not isinstance(price, (int, float)) or price < 0:
                    continue
                cat = item.get('category') if isinstance(item.get('category'), dict) else {}
                cat_id = item.get('category_id')
                cat_url = urljoin('https://lzt.market/', f'category/{cat_id}/')
                categories[cat_url] = ('Lolzteam', str(cat.get('title') or cat.get('category_title') or 'Объявления'), str(cat.get('category_url') or ''))
                user = item.get('seller') if isinstance(item.get('seller'), dict) else {}
                feedback = item.get('feedback_data')
                try:
                    feedback = json.loads(feedback) if isinstance(feedback, str) else feedback
                    category_feedback = feedback.get(str(cat_id), {}) if isinstance(feedback, dict) else {}
                    positive, negative = category_feedback.get('positive'), category_feedback.get('negative')
                    positive = positive if isinstance(positive, int) and not isinstance(positive, bool) and positive >= 0 else None
                    negative = negative if isinstance(negative, int) and not isinstance(negative, bool) and negative >= 0 else None
                except (ValueError, TypeError, AttributeError):
                    positive = negative = None
                records.append((urljoin('https://lzt.market/', f'{item_id}/'), cat_url, str(title), f'{price:g} ₽', str(user.get('username') or ''), 0, now(), '{}', (page-1)*40+position, price, 'lolz', positive, negative))
            with db() as c:
                c.executemany('INSERT INTO categories(url,game,name,slug) VALUES(?,?,?,?) ON CONFLICT(url) DO UPDATE SET game=excluded.game,name=excluded.name,slug=excluded.slug', [(url, *v) for url,v in categories.items()])
                c.executemany('INSERT INTO listings (url,category_url,title,price,seller,online,updated,attributes,position,price_num,source,seller_positive,seller_negative) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET category_url=excluded.category_url,title=excluded.title,price=excluded.price,seller=excluded.seller,updated=excluded.updated,position=excluded.position,price_num=excluded.price_num,seller_positive=excluded.seller_positive,seller_negative=excluded.seller_negative', records)
            result.update(pages=page, more=bool(data.get('hasNextPage')))
            if not data.get('hasNextPage') or not data['items']:
                break
            time.sleep(1)
    except requests.HTTPError as exc:
        result.update(state='error', error=f'API Lolzteam: HTTP {exc.response.status_code}')
    except (requests.RequestException, ValueError, KeyError):
        result.update(state='error', error='Не удалось получить данные Lolzteam')
    finally:
        result['time'] = time.monotonic()
        with _lock:
            _jobs[key] = result
