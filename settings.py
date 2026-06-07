APPLICATION_DIRECTORY = "d:/ML/"
DATA_DIRECTORY = APPLICATION_DIRECTORY + "DATA/"
LOG_DIRECTORY = APPLICATION_DIRECTORY+"LOGS/"

CONNECTION_STRING = "DSN=ML_PROD_DB;"
OT_WS_URL = "http://localhost/otws/v1.asmx"
OT_CONNECTOR_LOGIN = ""
OT_CONNECTOR_PWD = ""

THIRD_PARTY_API_KEY = "1"
THIRD_PARTY_URL = "http://127.0.0.1:5000"

TELEGRAM_BOT_TOKEN = ""
TELEGRAM_LOCAL_SERVER = "http://localhost:8081"

# Выбор бэкенда перевода: "nllb_600m" | "nllb_1300m" | "deepl"
TRANSLATION_BACKEND = "nllb_1300m"
DEEPL_API_KEY       = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx:fx"  # только для deepl

TRANSLATION_BACKEND = "gemini"
GEMINI_API_KEY      = ""                    # ключ с aistudio.google.com/apikey
GEMINI_MODEL        = "gemini-3.1-flash-lite"             # опционально, это дефолт
# Модель для анализа изображений (если не задана — берётся GEMINI_MODEL)
GEMINI_VISION_MODEL = "gemini-2.5-flash"

QWEN_MODEL_PATH   = r"d:\ml\ocr\qwen2.5-1.5b-instruct-q4_k_m.gguf"  # другая модель
QWEN_N_CTX        = 8192          # больше контекст для длинных документов
QWEN_SYSTEM_PROMPT = (
    "Ты — аналитик технических скриншотов. "
    "Тебе передаётся сырой OCR-текст, снятый с изображения экрана. "
    "Проанализируй текст и верни ТОЛЬКО валидный JSON без пояснений, без markdown, без пробелов вокруг скобок.\n\n"
    "Структура ответа (все поля обязательны, если данных нет — null):\n"
    "{\n"
    '  "error": "текст ошибки или исключения, если есть — максимально точно, null если нет",\n'
    '  "error_code": "код ошибки или HTTP-статус, например 404 / 0x800... / NullPointerException, null если нет",\n'
    '  "site": "домен или полный URL если виден в адресной строке или тексте, null если нет",\n'
    '  "app": "название программы, браузера, IDE, системы из которой сделан скрин, null если нет",\n'
    '  "tech_details": ["список других технических деталей: версии, стектрейсы, имена файлов, БД, запросы, IP-адреса и т.п."],\n'
    '  "raw_summary": "одно-три предложение — что в целом происходит на скриншотах"\n'
    "}\n\n"
    "Правила:\n"
    "- Не придумывай данные, которых нет в тексте.\n"
    "- Если одно поле содержит несколько значений — бери наиболее информативное.\n"
    "- tech_details — массив строк, может быть пустым [].\n"
)

DIC_SIZE=10000
MODEL_FIT_BATCH_SIZE=32
