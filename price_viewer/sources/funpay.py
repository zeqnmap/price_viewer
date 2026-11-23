import json
import re
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from ..database import db, now, state

BASE = "https://funpay.com/"

def text(el): return el.get_text(' ', strip=True) if el else ''

def discover(session):
    response = session.get(BASE, timeout=30); response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    found = {}
    for group in soup.select('.promo-game-item'):
        game = text(group.select_one('.game-title'))
        for a in group.select('a[href]'):
            url = urljoin(BASE, a['href']).split('?')[0]
            if re.fullmatch(r'https://funpay\.com/(?:lots|chips)/\d+/', url):
                name = text(a)
                if name and name != game: found[url] = (game, name)
    if not found: raise ValueError('Категории не найдены: структура FunPay могла измениться')
    with db() as c:
        c.executemany('INSERT INTO categories(url,game,name) VALUES(?,?,?) ON CONFLICT(url) DO UPDATE SET game=excluded.game,name=excluded.name', [(u,*v) for u,v in found.items()])
        state(c,'discovered', now())
    return len(found)

def crawl(session, cat):
    response = session.get(cat['url'], timeout=30); response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    filters = []
    for field in soup.select('.lot-field[data-id]'):
        fid = field.get('data-id')
        select = field.select_one('select')
        if select:
            options = [{'value': o.get('value', ''), 'label': text(o)} for o in select.select('option') if o.get('value')]
            label = text(select.select_one('option')) or fid
        else:
            options = [{'value': b.get('value', '').lower(), 'label': text(b)} for b in field.select('button[value]') if b.get('value')]
            label = {'type':'Тип', 'server':'Сервер', 'platform':'Платформа', 'region':'Регион', 'side':'Сторона', 'time':'Срок'}.get(fid, fid.replace('_',' ').replace('-',' ').capitalize())
        if options: filters.append({'id': fid, 'label': label, 'options': options})
    counter = text(soup.select_one('.counter-item.active .counter-value'))
    digits = re.sub(r'[^0-9]', '', counter)
    source_count = int(digits) if digits else None
    items = []
    for position, a in enumerate(soup.select('a.tc-item[href]')):
        url = urljoin(BASE, a['href'])
        if urlparse(url).hostname != 'funpay.com': continue
        title = text(a.select_one('.tc-desc-text'))
        if title:
            attrs = {k[7:]: str(v).casefold() for k,v in a.attrs.items() if k.startswith('data-f-')}
            price_el = a.select_one('.tc-price')
            try: price_num = float(price_el.get('data-s')) if price_el else None
            except (TypeError, ValueError): price_num = None
            reviews = a.select_one('.media-user-reviews')
            stars = reviews.select_one('.rating-stars') if reviews else None
            rating = next((int(cls[7:]) for cls in (stars.get('class', []) if stars else []) if re.fullmatch(r'rating-[1-5]', cls)), None)
            count_element = reviews.select_one('.rating-mini-count') if reviews else None
            count_text = text(count_element) if count_element else text(reviews)
            match = re.search(r'\d[\d\s\u00a0\u202f]*', count_text)
            count = int(re.sub(r'\D', '', match.group())) if match else None
            items.append((url, cat['url'], title, text(price_el), text(a.select_one('.media-user-name')), int(a.has_attr('data-online')), now(), json.dumps(attrs, ensure_ascii=False), position, price_num, rating, count))
    # A recognizable listing table is required before treating an empty response as authoritative.
    if not soup.select_one('.tc, .trade-container, .tc-item, .tc-desc'):
        raise ValueError('Страница объявлений не распознана')
    with db() as c:
        c.execute('DELETE FROM listings WHERE category_url=?', (cat['url'],))
        c.executemany('INSERT OR REPLACE INTO listings (url,category_url,title,price,seller,online,updated,attributes,position,price_num,seller_rating,seller_review_count) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', items)
        c.execute('UPDATE categories SET updated=?, rating_checked=?, error=NULL, filters=?, source_count=? WHERE url=?', (now(),now(),json.dumps(filters, ensure_ascii=False),source_count,cat['url']))
        state(c,'last_update',now())

