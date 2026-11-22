import os

DB_PATH = os.environ.get("DB_PATH", "/data/listings.db")
CRAWL_DELAY = float(os.environ.get("CRAWL_DELAY", "1"))
CRAWL_INTERVAL = int(os.environ.get("CRAWL_INTERVAL", "300"))
