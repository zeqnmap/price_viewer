"""Search the public Plati catalogue and load product feedback counts."""
import re
import math
import threading
import time
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup
from ..database import db, now

API = 'https://api.digiseller.com/api/cataloguer/front/products'
_lock = threading.Lock()
_jobs = {}


def status():
    with _lock:
        job = max(_jobs.values(), key=lambda item: item['time'], default=None)
        job = dict(job) if job else None
    return {'id': 'plati', 'name': 'Plati.Market', 'status': 'unavailable' if job and job['state'] == 'error' else 'active', 'message': job.get('error', '') if job else 'Поиск по каталогу включён.'}


def search(query):
    query = query.strip()[:100]
    if len(query) < 2:
        return {'state': 'idle'}
    key = query.casefold()
    with _lock:
        existing = _jobs.get(key)
        if existing and (existing['state'] == 'loading' or time.monotonic() - existing['time'] < 120):
            return dict(existing)
        if sum(job['state'] == 'loading' for job in _jobs.values()) >= 2:
            return {'state': 'busy'}
        _jobs[key] = {'state': 'loading', 'time': time.monotonic()}
        threading.Thread(target=_fetch, args=(key, query), daemon=True).start()
        return {'state': 'loading'}


def feedback(html):
    soup = BeautifulSoup(html, 'html.parser')
    panel = soup.select_one('#reviews-tab-content')
    if panel is None:
        return None, None
    counts = []
    for icon in ('thumb-up', 'thumb-down'):
        use = panel.select_one('use[href="#' + icon + '"]')
        span = use.find_parent('span') if use else None
        number = re.sub(r'[^0-9]', '', span.get_text(' ', strip=True)) if span else ''
        counts.append(int(number) if number else None)
    return tuple(counts)


def _fetch(key, query):
    result = {'state': 'ready', 'time': time.monotonic(), 'pages': 0, 'more': False, 'feedback_incomplete': False}
    try:
        session = requests.Session()
        session.headers.update({'User-Agent': 'PriceViewer/1.0 (+local personal catalog)'})
        for page in range(1, 3):
            response = session.get(API, params={'productName': query, 'ownerId': 'plati', 'getProductsRecursive': 'true', 'currency': 'RUB', 'page': page, 'count': 30, 'sortBy': 'popular', 'lang': 'ru-RU', 'fuzzy': 'false'}, timeout=20)
            response.raise_for_status()
            data = response.json()
            content = data.get('content') if isinstance(data, dict) and data.get('retval') == 0 else None
            if not isinstance(content, dict) or not isinstance(content.get('items'), list):
                raise ValueError('Неверный ответ каталога')
            for position, item in enumerate(content['items']):
                pid, price = item.get('product_id'), item.get('price')
                names = item.get('name') or []
                title = next((n.get('value') for n in names if n.get('locale') == 'ru-RU' and n.get('value')), None)
                if not isinstance(pid, int) or pid <= 0 or not title or isinstance(price, bool) or not isinstance(price, (int, float)) or price < 0 or item.get('currency') != 'RUB':
                    continue
                # Plati rounds RUB catalogue prices up before displaying them.
                price = math.ceil(price)
                alias = item.get('name_url') or 'product'
                url = 'https://plati.market/itm/' + quote(str(alias), safe='') + '/' + str(pid)
                positive = negative = None
                try:
                    detail = session.get(url, timeout=15)
                    detail.raise_for_status()
                    positive, negative = feedback(detail.text)
                    if positive is None or negative is None:
                        result['feedback_incomplete'] = True
                except requests.RequestException:
                    result['feedback_incomplete'] = True
                with db() as c:
                    category = 'https://plati.market/catalog/'
                    c.execute('INSERT OR IGNORE INTO categories(url,game,name) VALUES(?,?,?)', (category, 'Plati.Market', 'Каталог'))
                    c.execute('''INSERT INTO listings (url,category_url,title,price,seller,online,updated,attributes,position,price_num,source,seller_positive,seller_negative)
                        VALUES (?,?,?,?,?,0,?,'{}',?,?, 'plati',?,?)
                        ON CONFLICT(url) DO UPDATE SET title=excluded.title,price=excluded.price,seller=excluded.seller,updated=excluded.updated,position=excluded.position,price_num=excluded.price_num,seller_positive=COALESCE(excluded.seller_positive,listings.seller_positive),seller_negative=COALESCE(excluded.seller_negative,listings.seller_negative)''',
                        (url, category, str(title), f'{price:g} ₽', str(item.get('seller_name') or ''), now(), (page-1)*30+position, price, positive, negative))
            result.update(pages=page, more=bool(content.get('has_next_page')))
            if not result['more'] or not content['items']:
                break
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        result.update(state='error', error='Не удалось получить данные Plati.Market')
    finally:
        result['time'] = time.monotonic()
        with _lock:
            _jobs[key] = result
