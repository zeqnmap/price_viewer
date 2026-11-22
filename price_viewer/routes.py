import re
from datetime import datetime, timezone
from flask import Blueprint, jsonify, render_template, request
from .database import db
from .search import normalize
from .worker import enqueue, queue_lock, queued
from .sources import lolz, playerok

bp = Blueprint("site", __name__)

@bp.get('/')
def index(): return render_template('index.html')

@bp.get('/api/sources')
def sources():
    with db() as c:
        stats = c.execute('SELECT COUNT(*) AS n, MAX(updated) AS updated FROM listings WHERE source=?', ('funpay',)).fetchone()
    return jsonify([
        {'id':'funpay','name':'FunPay','status':'active','count':stats['n'],'updated':stats['updated']},
        lolz.status(),
        playerok.status()
    ])

@bp.get('/api/categories')
def categories():
    q = normalize(request.args.get('q','').strip()[:200])
    if not q: return jsonify([])
    source = request.args.get('source', '')
    with db() as c:
        rows = c.execute('SELECT url,game,name,updated,filters,slug,search_game,search_name FROM categories').fetchall()
    def rank(row):
        name = row['search_name'] or ''
        game = row['search_game'] or ''
        target = name if row['url'].startswith('https://lzt.market/') else game
        if target == q: return 0
        if any(word.startswith(q) for word in target.split()): return 1
        if len(q) <= 3 and len(target.split()) == 1 and target.startswith(q[0]):
            letters = iter(target[1:])
            if all(any(c == letter for c in letters) for letter in q[1:]): return 2
        if len(q) >= 3 and q in target: return 3
        return 99
    matches = [dict(row) for row in rows if (not source or source not in ('funpay','lolz','playerok') or row['url'].startswith({'funpay':'https://funpay.com/','lolz':'https://lzt.market/','playerok':'https://playerok.com/'}[source])) and rank(row) < 99]
    matches.sort(key=lambda row: (rank(row), row['game'], row['name']))
    return jsonify(matches[:100])

@bp.post('/api/refresh')
def refresh_category():
    category = (request.get_json(silent=True) or {}).get('category', '')
    with db() as c:
        row = c.execute('SELECT * FROM categories WHERE url=?', (category,)).fetchone()
    if not row: return jsonify(error='Категория не найдена'), 404
    if row['url'].startswith('https://lzt.market/'):
        lolz.search('', row['slug'])
    elif row['url'].startswith('https://playerok.com/'):
        playerok.search('', row['slug'])
    else:
        enqueue(row, force=True)
    return jsonify(queued=True)

@bp.get('/api/listings')
def listings():
    q = request.args.get('q','').strip()[:200]
    page = max(1, min(request.args.get('page',1,type=int) or 1,100000))
    game = request.args.get('game','').strip()[:200]
    category = request.args.get('category','').strip()[:200]
    sort = request.args.get('sort','source')
    source = request.args.get('source','')
    with db() as c:
        category_row = c.execute('SELECT * FROM categories WHERE url=?', (category,)).fetchone() if category else None
    is_lolz_category = bool(category_row and category_row['url'].startswith('https://lzt.market/'))
    is_playerok_category = bool(category_row and category_row['url'].startswith('https://playerok.com/'))
    playerok_search = playerok.search(q, category_row['slug']) if is_playerok_category else playerok.search(q) if not category and source in ('', 'playerok') else {'state': 'idle'}
    lolz_search = lolz.search(q, category_row['slug']) if is_lolz_category else lolz.search(q) if not category and source in ('', 'lolz') else {'state': 'idle'}
    where, args = [], []
    for word in normalize(q).split():
        if category:
            where.append('l.search_title LIKE ?')
            args.append('%'+word+'%')
        else:
            if len(word) <= 2:
                where.append("(instr(' ' || l.search_title || ' ', ?) > 0 OR instr(' ' || c.search_game || ' ', ?) > 0 OR instr(' ' || c.search_name || ' ', ?) > 0)")
                args += [' '+word+' ']*3
            else:
                where.append('(l.search_title LIKE ? OR c.search_game LIKE ? OR c.search_name LIKE ?)')
                args += ['%'+word+'%']*3
    if game: where.append('c.game = ?'); args.append(game)
    if category: where.append('c.url = ?'); args.append(category)
    if source in ('funpay','lolz','playerok'): where.append('l.source = ?'); args.append(source)
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
        if category and not is_lolz_category and not is_playerok_category:
            row = c.execute('SELECT * FROM categories WHERE url=?', (category,)).fetchone()
            if row:
                stale = not row['updated'] or not row['filters'] or (datetime.now(timezone.utc) - datetime.fromisoformat(row['updated'])).total_seconds() > 120
                if stale: enqueue(row)
                with queue_lock:
                    if category in queued: pending = [dict(row)]
        elif not category and len(q) >= 2 and source in ('', 'funpay'):
            pending = [dict(x) for x in c.execute("SELECT * FROM categories WHERE url LIKE 'https://funpay.com/%' AND updated IS NULL AND error IS NULL AND (search_game LIKE ? OR search_name LIKE ?) LIMIT 3", ('%'+normalize(q)+'%', '%'+normalize(q)+'%'))]
        if not category:
            for cat in pending: enqueue(cat)
        total = c.execute('SELECT COUNT(*) FROM listings l JOIN categories c ON c.url=l.category_url'+clause,args).fetchone()[0]
        order = {'source': 'COALESCE(l.position, 999999), l.url', 'price_asc': 'l.price_num IS NULL, l.price_num ASC, l.url', 'price_desc': 'l.price_num IS NULL, l.price_num DESC, l.url'}.get(sort, 'COALESCE(l.position, 999999), l.url')
        rows = c.execute('SELECT l.*, c.game, c.name AS category FROM listings l JOIN categories c ON c.url=l.category_url'+clause+' ORDER BY '+order+' LIMIT 40 OFFSET ?',args+[(page-1)*40]).fetchall()
        selected = c.execute('SELECT filters,updated,error,source_count,slug FROM categories WHERE url=?',(category,)).fetchone() if category else None
        status = dict(c.execute('SELECT key,value FROM state').fetchall())
        stats = dict(c.execute('SELECT COUNT(*) AS categories, COUNT(updated) AS updated, SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) AS errors FROM categories WHERE url LIKE \"https://funpay.com/%\"').fetchone())
    return jsonify(items=[dict(x) for x in rows],total=total,page=page,status=status,stats=stats,pending=len(pending),selected=dict(selected) if selected else None, lolz_search=lolz_search,playerok_search=playerok_search)
