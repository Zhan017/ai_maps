import os

from dotenv import load_dotenv

load_dotenv()

DSN = os.getenv("DATABASE_DSN", "host=localhost port=5434 dbname=gis user=zhan password=zhan")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_DIM = 1536
CHAT_MODEL = os.getenv("CHAT_MODEL", "gpt-4o-mini")

MATCH_HIGH_THRESHOLD = float(os.getenv("MATCH_HIGH_THRESHOLD", "0.85"))
MATCH_LOW_THRESHOLD = float(os.getenv("MATCH_LOW_THRESHOLD", "0.6"))

DEFAULT_TZ = "Asia/Almaty"

POOL_MIN_SIZE = 2
POOL_MAX_SIZE = 10
