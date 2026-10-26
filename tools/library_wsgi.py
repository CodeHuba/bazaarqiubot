"""Dedicated production WSGI entry point; no QQ bot or main-site imports."""
from pathlib import Path
import os

from tools.preview_library import create_app

ROOT = Path(__file__).resolve().parents[1]
app = create_app(ROOT/'input/GameData.db', ROOT/'input/zh-CN.bytes', ROOT/'input/card_images.json',
                 Path(os.environ.get('LIBRARY_STATE', '/var/lib/qiubot-library')),
                 prefix='/library', data_label=os.environ.get('LIBRARY_DATA_LABEL', '19.3'), production=True)
