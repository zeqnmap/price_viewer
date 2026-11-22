import logging
import os
import queue
import threading
import time
from datetime import datetime, timezone, timedelta
import requests
from .config import CRAWL_DELAY, CRAWL_INTERVAL
from .database import db, now, state
from .sources.funpay import discover, crawl

logger = logging.getLogger(__name__)
priority = queue.LifoQueue()
queued = set()
queue_lock = threading.Lock()
wake = threading.Event()
last_queued = {}
last_activity = 0

def enqueue(cat, force=False):
    global last_activity
    last_activity = time.monotonic()
    with queue_lock:
        url = cat['url']
        if url in queued or (not force and time.monotonic() - last_queued.get(url, -1000) < 120):
            return False
        queued.add(url)
        last_queued[url] = time.monotonic()
        priority.put(dict(cat))
        wake.set()
        return True

def worker():
    session = requests.Session()
    session.headers.update({'User-Agent':'PriceViewer/1.0 (+local personal catalog)'})
    last_discovery = 0
    while True:
        try:
            if not priority.empty() and last_discovery:
                pass
            elif time.monotonic() - last_discovery >= CRAWL_INTERVAL:
                count = discover(session)
                last_discovery = time.monotonic()
                logger.info('Discovered %s categories',count)
            with db() as c: cats = [dict(x) for x in c.execute('SELECT * FROM categories WHERE url LIKE "https://funpay.com/%" ORDER BY COALESCE(updated, ""), url')] if os.environ.get('CRAWL_BACKGROUND', '0') == '1' else []
            while cats or not priority.empty():
                try:
                    cat = priority.get_nowait()
                    cats = [x for x in cats if x['url'] != cat['url']]
                except queue.Empty:
                    if not cats: break
                    if time.monotonic() - last_activity < 30:
                        wake.wait(2)
                        wake.clear()
                        continue
                    cat = cats.pop(0)
                retry = False
                try: crawl(session,cat)
                except Exception as e:
                    logger.warning('Failed %s: %s',cat['url'],e)
                    with db() as c:
                        c.execute('UPDATE categories SET error=? WHERE url=?',(str(e)[:300],cat['url']))
                        state(c,'last_error',str(e)[:300])
                    if isinstance(e, requests.HTTPError) and e.response is not None and e.response.status_code == 429:
                        logger.warning('FunPay rate limit; waiting 60 seconds')
                        with db() as c: state(c, 'cooldown_until', (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat())
                        retry = True
                        time.sleep(60)
                finally:
                    with queue_lock:
                        queued.discard(cat['url'])
                if retry:
                    enqueue(cat, force=True)
                time.sleep(CRAWL_DELAY)
            with db() as c: state(c,'last_cycle',now())
        except Exception as e:
            logger.exception('Discovery failed')
            with db() as c: state(c,'last_error',str(e)[:300])
        wake.wait(CRAWL_INTERVAL)
        wake.clear()

