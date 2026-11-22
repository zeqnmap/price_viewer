import os
import threading
from pathlib import Path
from flask import Flask
from .database import init
from .routes import bp
from .worker import worker

def create_app(start_crawler=None):
    root = Path(__file__).resolve().parent.parent
    app = Flask(__name__, template_folder=str(root / "templates"), static_folder=str(root / "static"))
    app.register_blueprint(bp)
    init()
    if start_crawler is None:
        start_crawler = os.environ.get("START_CRAWLER", "1") == "1"
    if start_crawler:
        threading.Thread(target=worker, daemon=True, name="funpay-crawler").start()
    return app
