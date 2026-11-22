import os
import sqlite3
from datetime import datetime, timezone
from .config import DB_PATH
from .search import normalize

os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)

def db():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.create_function("casefold", 1, normalize, deterministic=True)
    return c

def init():
    with db() as c:
        c.executescript('''PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS categories (url TEXT PRIMARY KEY, game TEXT, name TEXT, updated TEXT, error TEXT);
        CREATE TABLE IF NOT EXISTS listings (url TEXT PRIMARY KEY, category_url TEXT, title TEXT, price TEXT, seller TEXT, online INTEGER, updated TEXT);
        CREATE INDEX IF NOT EXISTS idx_listing_category ON listings(category_url);
        CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);''')
        for table, col, typ in [('categories','slug','TEXT'), ('categories','search_game','TEXT'), ('categories','search_name','TEXT'), ('listings','search_title','TEXT'), ('listings','seller_rating','REAL'), ('listings','seller_review_count','INTEGER'), ('listings','seller_positive','INTEGER'), ('listings','seller_negative','INTEGER'), ('categories','filters','TEXT'), ('categories','source_count','INTEGER'), ('listings','attributes','TEXT'), ('listings','position','INTEGER'), ('listings','price_num','REAL'), ('listings','source','TEXT NOT NULL DEFAULT \"funpay\"')]:
            if col not in [x['name'] for x in c.execute(f'PRAGMA table_info({table})')]:
                c.execute(f'ALTER TABLE {table} ADD COLUMN {col} {typ}')
        c.execute('UPDATE categories SET search_game=casefold(game), search_name=casefold(name) WHERE search_game IS NULL OR search_name IS NULL')
        c.execute('UPDATE listings SET search_title=casefold(title) WHERE search_title IS NULL')
        c.executescript('''
        CREATE TRIGGER IF NOT EXISTS categories_search_insert AFTER INSERT ON categories BEGIN
            UPDATE categories SET search_game=casefold(NEW.game), search_name=casefold(NEW.name) WHERE url=NEW.url;
        END;
        CREATE TRIGGER IF NOT EXISTS categories_search_update AFTER UPDATE OF game,name ON categories BEGIN
            UPDATE categories SET search_game=casefold(NEW.game), search_name=casefold(NEW.name) WHERE url=NEW.url;
        END;
        CREATE TRIGGER IF NOT EXISTS listings_search_insert AFTER INSERT ON listings BEGIN
            UPDATE listings SET search_title=casefold(NEW.title) WHERE url=NEW.url;
        END;
        CREATE TRIGGER IF NOT EXISTS listings_search_update AFTER UPDATE OF title ON listings BEGIN
            UPDATE listings SET search_title=casefold(NEW.title) WHERE url=NEW.url;
        END;''')

def now(): return datetime.now(timezone.utc).isoformat()
def state(c, key, value): c.execute('INSERT OR REPLACE INTO state VALUES (?,?)', (key,str(value)))
