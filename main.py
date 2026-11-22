import os, re, sqlite3, threading, time, logging, queue, json
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)
DB = os.environ.get('DB_PATH', '/data/listings.db')
BASE = 'https://funpay.com/'
DELAY = float(os.environ.get('CRAWL_DELAY', '1'))
INTERVAL = int(os.environ.get('CRAWL_INTERVAL', '300'))
os.makedirs(os.path.dirname(os.path.abspath(DB)), exist_ok=True)
logging.basicConfig(level=logging.INFO)
priority = queue.Queue()
queued = set()
queue_lock = threading.Lock()

def db():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.create_function("casefold", 1, lambda x: (x or "").casefold(), deterministic=True)
    return c

def init():
    with db() as c:
        c.executescript('''PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS categories (url TEXT PRIMARY KEY, game TEXT, name TEXT, updated TEXT, error TEXT);
        CREATE TABLE IF NOT EXISTS listings (url TEXT PRIMARY KEY, category_url TEXT, title TEXT, price TEXT, seller TEXT, online INTEGER, updated TEXT);
        CREATE INDEX IF NOT EXISTS idx_listing_category ON listings(category_url);
        CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);''')
        for table, col, typ in [('categories','filters','TEXT'), ('listings','attributes','TEXT'), ('listings','position','INTEGER'), ('listings','price_num','REAL')]:
            if col not in [x['name'] for x in c.execute(f'PRAGMA table_info({table})')]:
                c.execute(f'ALTER TABLE {table} ADD COLUMN {col} {typ}')

def now(): return datetime.now(timezone.utc).isoformat()
def state(c, key, value): c.execute('INSERT OR REPLACE INTO state VALUES (?,?)', (key,str(value)))
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
            label = fid
        if options: filters.append({'id': fid, 'label': label, 'options': options})
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
            items.append((url, cat['url'], title, text(price_el), text(a.select_one('.media-user-name')), int(a.has_attr('data-online')), now(), json.dumps(attrs, ensure_ascii=False), position, price_num))
    # A recognizable listing table is required before treating an empty response as authoritative.
    if not soup.select_one('.tc, .trade-container, .tc-item, .tc-desc'):
        raise ValueError('Страница объявлений не распознана')
    with db() as c:
        c.execute('DELETE FROM listings WHERE category_url=?', (cat['url'],))
        c.executemany('INSERT OR REPLACE INTO listings (url,category_url,title,price,seller,online,updated,attributes,position,price_num) VALUES (?,?,?,?,?,?,?,?,?,?)', items)
        c.execute('UPDATE categories SET updated=?, error=NULL, filters=? WHERE url=?', (now(),json.dumps(filters, ensure_ascii=False),cat['url']))
        state(c,'last_update',now())

def worker():
    session = requests.Session()
    session.headers.update({'User-Agent':'PriceViewer/1.0 (+local personal catalog)'})
    while True:
        try:
            count = discover(session)
            app.logger.info('Discovered %s categories',count)
            with db() as c: cats = [dict(x) for x in c.execute('SELECT * FROM categories ORDER BY COALESCE(updated, ""), url')]
            while cats or not priority.empty():
                try:
                    cat = priority.get_nowait()
                    with queue_lock: queued.discard(cat['url'])
                    cats = [x for x in cats if x['url'] != cat['url']]
                except queue.Empty:
                    if not cats: break
                    cat = cats.pop(0)
                try: crawl(session,cat)
                except Exception as e:
                    app.logger.warning('Failed %s: %s',cat['url'],e)
                    with db() as c:
                        c.execute('UPDATE categories SET error=? WHERE url=?',(str(e)[:300],cat['url']))
                        state(c,'last_error',str(e)[:300])
                    if isinstance(e, requests.HTTPError) and e.response is not None and e.response.status_code == 429:
                        app.logger.warning('FunPay rate limit; waiting 60 seconds')
                        time.sleep(60)
                time.sleep(DELAY)
            with db() as c: state(c,'last_cycle',now())
        except Exception as e:
            app.logger.exception('Discovery failed')
            with db() as c: state(c,'last_error',str(e)[:300])
        time.sleep(INTERVAL)

@app.get('/')
def index(): return render_template('index.html')

@app.get('/api/categories')
def categories():
    q = request.args.get('q','').strip().casefold()[:200]
    with db() as c:
        rows = c.execute('SELECT url,game,name,updated,filters FROM categories WHERE casefold(game) LIKE ? OR casefold(name) LIKE ? ORDER BY game,name LIMIT 100', ('%'+q+'%', '%'+q+'%')).fetchall() if q else []
    return jsonify([dict(x) for x in rows])

@app.get('/api/listings')
def listings():
    q = request.args.get('q','').strip()[:200]
    page = max(1, min(request.args.get('page',1,type=int) or 1,100000))
    game = request.args.get('game','').strip()[:200]
    category = request.args.get('category','').strip()[:200]
    sort = request.args.get('sort','source')
    where, args = [], []
    if q:
        where.append('(casefold(l.title) LIKE ? OR casefold(c.game) LIKE ? OR casefold(c.name) LIKE ?)')
        args += ['%'+q.casefold()+'%']*3
    if game: where.append('c.game = ?'); args.append(game)
    if category: where.append('c.url = ?'); args.append(category)
    if request.args.get('online') == '1': where.append('l.online = 1')
    for key, value in request.args.items():
        if key.startswith('f-') and re.fullmatch(r'f-[a-zA-Z0-9_-]+',key) and value:
            where.append('json_extract(l.attributes, ?) = ?')
            args.extend(['$."'+key[2:]+'"', value.casefold()])
    for key, op in [('min_price','>='),('max_price','<=')]:
        try:
            val = float(request.args.get(key,''))
            where.append('l.price_num '+op+' ?'); args.append(val)
        except ValueError: pass
    clause = ' WHERE '+' AND '.join(where) if where else ''
    with db() as c:
        pending = []
        if category:
            pending = [dict(x) for x in c.execute('SELECT * FROM categories WHERE url=? AND (updated IS NULL OR filters IS NULL)', (category,))]
        elif len(q) >= 2:
            pending = [dict(x) for x in c.execute('SELECT * FROM categories WHERE updated IS NULL AND error IS NULL AND (casefold(game) LIKE ? OR casefold(name) LIKE ?) LIMIT 50', ('%'+q.casefold()+'%', '%'+q.casefold()+'%'))]
        with queue_lock:
            for cat in pending:
                if cat['url'] not in queued:
                    queued.add(cat['url'])
                    priority.put(cat)
        total = c.execute('SELECT COUNT(*) FROM listings l JOIN categories c ON c.url=l.category_url'+clause,args).fetchone()[0]
        order = {'source': 'COALESCE(l.position, 999999), l.url', 'price_asc': 'l.price_num IS NULL, l.price_num ASC, l.url', 'price_desc': 'l.price_num IS NULL, l.price_num DESC, l.url'}.get(sort, 'COALESCE(l.position, 999999), l.url')
        rows = c.execute('SELECT l.*, c.game, c.name AS category FROM listings l JOIN categories c ON c.url=l.category_url'+clause+' ORDER BY '+order+' LIMIT 40 OFFSET ?',args+[(page-1)*40]).fetchall()
        selected = c.execute('SELECT filters,updated,error FROM categories WHERE url=?',(category,)).fetchone() if category else None
        status = dict(c.execute('SELECT key,value FROM state').fetchall())
        stats = dict(c.execute('SELECT COUNT(*) AS categories, COUNT(updated) AS updated, SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) AS errors FROM categories').fetchone())
    return jsonify(items=[dict(x) for x in rows],total=total,page=page,status=status,stats=stats,pending=len(pending),selected=dict(selected) if selected else None)

init()
if os.environ.get('START_CRAWLER','1') == '1':
    threading.Thread(target=worker, daemon=True).start()
if __name__ == '__main__': app.run(host='0.0.0.0',port=5000,debug=False)
