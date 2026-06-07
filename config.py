import os
import ssl
import sys
import logging

# --- ПОДКЛЮЧЕНИЕ НАСТРОЕК ---
sys.path.append("d:/ML/CONFIG/")
try:
    import settings  # noqa: F401  (импортируется здесь, используется в других модулях)
except ImportError:
    print("CRITICAL: settings.py not found")
    sys.exit(1)

# --- ОТКЛЮЧЕНИЕ ПРОВЕРКИ SSL ---
os.environ['CURL_CA_BUNDLE'] = ''
ssl._create_default_https_context = ssl._create_unverified_context

# --- ПУТИ ---
BASE_PATH = r"d:\ML\discrib"
TMP_DIR = os.path.join(BASE_PATH, "tmp")
os.makedirs(TMP_DIR, exist_ok=True)

# --- ЛОГИРОВАНИЕ ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(BASE_PATH, "service.log"), encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("Transcrib_Service")