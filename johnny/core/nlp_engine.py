import hashlib
import json
import os
import re
import warnings
from pathlib import Path

import numpy as np

try:
    import PyPDF2
except ImportError:
    PyPDF2 = None
try:
    import docx
except ImportError:
    docx = None

from johnny.core.embedding_manager import (
    DEFAULT_MODEL_NAME,
    EmbeddingManager,
    load_routing_calibration,
    missing_profiles,
)
from johnny.core.index_manager import DEFAULT_PROFILES


PROJECT_DIR = Path(__file__).resolve().parents[2]  # repo root


class NLPEngine:
    def __init__(self, categories=None, category_profiles=None, model_name=None):
        self.categories = categories or ["Unsorted_Miscellaneous"]
        self.model_name = model_name or DEFAULT_MODEL_NAME
        self.embedding_manager = EmbeddingManager.get()
        self.routing_calibration = load_routing_calibration()
        self.category_profiles = DEFAULT_PROFILES if category_profiles is None else category_profiles
        self.prototype_texts = {}
        self.prototype_index = []
        self.prototype_lookup = []
        self.category_embeddings = None
        self.category_token_sets = []
        self.model = None

        if self.embedding_manager.is_available():
            self.model = self.embedding_manager.get_model(self.model_name)
            self._build_category_prototypes()

    def _normalize(self, text):
        text = (text or "").replace("_", " ").replace("-", " ")
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _build_category_prototypes(self):
        self.prototype_texts = {}
        self.categories_without_profile = missing_profiles(self.categories, self.category_profiles)
        if self.categories_without_profile:
            warnings.warn(
                f"{len(self.categories_without_profile)} of {len(self.categories)} categories have no entry in "
                "the index's \"profiles\"; routing uses their names only, so expect many files in Unsorted_Miscellaneous.",
                stacklevel=2,
            )
        category_profiles_hash_payload = {}

        for category in self.categories:
            profile = self.category_profiles.get(category, {})
            if isinstance(profile, str):
                profile = {"description": profile}
            prototypes = [self._normalize(category)]

            description = self._normalize(profile.get("description", ""))
            if description:
                prototypes.append(description)

            for alias in profile.get("aliases", []):
                alias = self._normalize(alias)
                if alias:
                    prototypes.append(alias)

            for example in profile.get("positive_examples", []):
                example = self._normalize(example)
                if example:
                    prototypes.append(example)

            deduped = []
            seen = set()
            for item in prototypes:
                lowered = item.lower()
                if lowered not in seen:
                    deduped.append(item)
                    seen.add(lowered)

            self.prototype_texts[category] = deduped
            category_profiles_hash_payload[category] = deduped

        self.prototype_index = []
        self.prototype_lookup = []
        self.category_token_sets = []
        for category in self.categories:
            texts = self.prototype_texts.get(category) or [self._normalize(category)]
            self.category_token_sets.append(set(re.findall(r"\w+", " ".join(texts).lower())))
            for text in texts:
                self.prototype_index.append(text)
                self.prototype_lookup.append(category)

        cache_key = hashlib.sha1(
            json.dumps(category_profiles_hash_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        self.category_embeddings = self.embedding_manager.get_category_embeddings(
            self.prototype_index,
            cache_key=cache_key,
            model_name=self.model_name,
        )

    def extract_text(self, file_path):
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".txt":
            with open(file_path, "r", encoding="utf-8", errors="ignore") as handle:
                return handle.read()
        if ext == ".pdf" and PyPDF2 is not None:
            text = ""
            try:
                with open(file_path, "rb") as handle:
                    reader = PyPDF2.PdfReader(handle)
                    for page in reader.pages:
                        extracted = page.extract_text()
                        if extracted:
                            text += extracted + " "
            except Exception as exc:
                print(f"Error reading PDF {file_path}: {exc}")
            return text
        if ext == ".docx" and docx is not None:
            text = ""
            try:
                document = docx.Document(file_path)
                for para in document.paragraphs:
                    text += para.text + " "
            except Exception as exc:
                print(f"Error reading DOCX {file_path}: {exc}")
            return text
        return ""

    def _rank(self, similarities, top_k):
        """[{category, score}] best first; a category scores as its best prototype."""
        per_category_scores = {}
        for idx, category in enumerate(self.prototype_lookup):
            score = float(similarities[idx])
            if category not in per_category_scores or score > per_category_scores[category]:
                per_category_scores[category] = score
        ranked = sorted(per_category_scores.items(), key=lambda item: item[1], reverse=True)
        return [{"category": category, "score": score} for category, score in ranked[: max(1, top_k)]]

    def _decide(self, candidates):
        """The abstention rule: confidence and top1-top2 margin, else Unsorted -> (category, best_score)."""
        if not candidates:
            return "Unsorted_Miscellaneous", 0.0
        top_1 = candidates[0]
        top_2 = candidates[1] if len(candidates) > 1 else {"category": "Unsorted_Miscellaneous", "score": 0.0}
        best_score = float(top_1["score"])
        margin = best_score - float(top_2["score"])

        confidence_threshold = self.routing_calibration["dense_confidence_threshold"]
        margin_threshold = self.routing_calibration["top1_top2_margin_threshold"]
        unsorted_override = self.routing_calibration["unsorted_override_threshold"]

        category = top_1["category"]
        if best_score < confidence_threshold or margin < margin_threshold:
            if best_score < unsorted_override or category != "Unsorted_Miscellaneous":
                category = "Unsorted_Miscellaneous"
        return category, best_score

    def get_category_candidates(self, text, top_k=3):
        clean_text = self._normalize(text)[:1000]
        if not clean_text or self.model is None or self.category_embeddings is None:
            return []
        query_embedding = self.embedding_manager.encode_queries([clean_text], model_name=self.model_name)[0]
        return self._rank(np.dot(self.category_embeddings, query_embedding), top_k)

    def get_topic_details(self, text):
        clean_text = self._normalize(text)[:1000]
        if not clean_text or not self.categories:
            return "Unsorted_Miscellaneous", 0.0, ""
        candidates = self.get_category_candidates(clean_text, top_k=3)
        if not candidates:
            return "Unsorted_Miscellaneous", 0.0, clean_text
        category, best_score = self._decide(candidates)
        return category, best_score, clean_text

    def _query_embeddings(self, clean_texts, cache_path=None, chunk=2000):
        """Embeddings of the texts. File texts never change between rounds (only the buckets do), so with
        cache_path they are encoded once, saved after every chunk (an interrupted run resumes) and reused."""
        keys = [hashlib.sha1(t.encode("utf-8")).hexdigest() for t in clean_texts]
        known = {}
        if cache_path and os.path.exists(cache_path):
            with np.load(cache_path, allow_pickle=False) as data:
                if str(data["model"]) == self.model_name:
                    known = dict(zip(data["keys"].tolist(), data["emb"]))
        todo = {k: t for k, t in zip(keys, clean_texts) if k not in known}
        items = list(todo.items())
        for start in range(0, len(items), chunk):
            part = items[start:start + chunk]
            known.update(zip((k for k, _ in part), self.embedding_manager.encode_queries([t for _, t in part], model_name=self.model_name)))
            if cache_path:
                tmp = cache_path + ".tmp.npz"
                np.savez(tmp, model=np.array(self.model_name), keys=np.array(list(known)), emb=np.stack(list(known.values())))
                os.replace(tmp, cache_path)
            print(f"  encoded {min(start + chunk, len(items))}/{len(items)} new texts", flush=True)
        return np.stack([known[k] for k in keys]) if keys else np.zeros((0, 1))

    # Must keep deciding exactly like get_topic_details (tests/test_core.py BatchSorting): both share _rank and _decide on purpose.
    def get_topic_details_many(self, texts, cache_path=None, top_k=3):
        """Batch form of get_topic_details -> [(category, best_score, candidates)], same decisions as one call at a time."""
        clean = [self._normalize(t)[:1000] for t in texts]
        usable = [i for i, t in enumerate(clean) if t and self.categories and self.model is not None and self.category_embeddings is not None]
        results = [("Unsorted_Miscellaneous", 0.0, [])] * len(texts)
        if usable:
            similarities = np.dot(self._query_embeddings([clean[i] for i in usable], cache_path), self.category_embeddings.T)
            for row, i in zip(similarities, usable):
                candidates = self._rank(row, top_k)
                results[i] = (*self._decide(candidates), candidates)
        return results

    def get_topic(self, text):
        category, _, _ = self.get_topic_details(text)
        return category

    def generate_filename(self, text):
        words = [word for word in re.split(r"\W+", text) if word and not word.isdigit()]
        if not words:
            return "Unsorted_Document"
        name = "_".join(words[:5]).title()
        if len(name) > 70:
            name = name[:70]
        return name.rstrip("_")
