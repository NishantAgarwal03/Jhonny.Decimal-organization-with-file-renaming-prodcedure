import json
from pathlib import Path
from threading import Lock


try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None


PROJECT_DIR = Path(__file__).resolve().parents[2]  # repo root
DEFAULT_MODEL_NAME = "intfloat/multilingual-e5-base"
FAST_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


class EmbeddingManager:
    _instance = None
    _instance_lock = Lock()

    def __init__(self):
        self._models = {}
        self._model_locks = {}
        self._category_cache = {}
        self._metrics = {
            "model_load_count": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "default_model": DEFAULT_MODEL_NAME,
        }

    @classmethod
    def get(cls):
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
        return cls._instance

    def is_available(self):
        return SentenceTransformer is not None

    def _get_model_lock(self, model_name):
        if model_name not in self._model_locks:
            self._model_locks[model_name] = Lock()
        return self._model_locks[model_name]

    def get_model(self, model_name=None):
        model_name = model_name or DEFAULT_MODEL_NAME
        if SentenceTransformer is None:
            raise RuntimeError("sentence-transformers is not installed")
        if model_name in self._models:
            return self._models[model_name]
        lock = self._get_model_lock(model_name)
        with lock:
            if model_name not in self._models:
                self._models[model_name] = SentenceTransformer(model_name)
                self._metrics["model_load_count"] += 1
        return self._models[model_name]

    def _prefix_texts(self, texts, prefix):
        return [f"{prefix}: {text}".strip() for text in texts]

    def encode_queries(self, texts, model_name=None):
        model = self.get_model(model_name)
        return model.encode(self._prefix_texts(texts, "query"), normalize_embeddings=True)

    def encode_passages(self, texts, model_name=None):
        model = self.get_model(model_name)
        return model.encode(self._prefix_texts(texts, "passage"), normalize_embeddings=True)

    def get_category_embeddings(self, texts, cache_key, model_name=None):
        model_name = model_name or DEFAULT_MODEL_NAME
        full_key = f"{model_name}|{cache_key}"
        if full_key in self._category_cache:
            self._metrics["cache_hits"] += 1
            return self._category_cache[full_key]
        self._metrics["cache_misses"] += 1
        embeddings = self.encode_passages(texts, model_name=model_name)
        self._category_cache[full_key] = embeddings
        return embeddings

    def metrics(self):
        return dict(self._metrics)


def load_category_profiles(path=None):
    path = Path(path) if path else PROJECT_DIR / "category_profiles.json"
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def missing_profiles(categories, profiles):
    """Categories with no entry in the profiles; routing falls back to the bare category name for these."""
    return [category for category in categories if category not in profiles]


def load_routing_calibration(path=None):
    path = Path(path) if path else PROJECT_DIR / "routing_calibration.json"
    if not path.exists():
        return {
            "dense_confidence_threshold": 0.28,
            "top1_top2_margin_threshold": 0.04,
            "unsorted_override_threshold": 0.22,
        }
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    return {
        "dense_confidence_threshold": float(data.get("dense_confidence_threshold", 0.28)),
        "top1_top2_margin_threshold": float(data.get("top1_top2_margin_threshold", 0.04)),
        "unsorted_override_threshold": float(data.get("unsorted_override_threshold", 0.22)),
    }
