"""Deterministic stand-in for NLPEngine so end-to-end tests need no embedding model."""
import os
import re
import sys
import tempfile

# Tests must never write to the real undo log; every test module imports this file before utils.
os.environ.setdefault("JOHNNY_UNDO_LOG", os.path.join(tempfile.gettempdir(), "johnny_tests_undo.jsonl"))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from johnny.core.nlp_engine import NLPEngine as RealNLPEngine

class FixedClock:
    """Stand-in for utils.datetime: collision suffixes use the current minute, so freeze it."""

    class datetime:
        @staticmethod
        def now():
            import datetime as real
            return real.datetime(2026, 1, 1, 12, 0)


KEYWORDS = {"tax": "Tax", "study": "Study"}


class FakeNLP:
    instances = []  # lets tests see which profiles each engine was built with

    def __init__(self, categories=None, *args, **kwargs):
        self.categories = categories or []
        self.profiles = kwargs.get("category_profiles")
        FakeNLP.instances.append(self)

    def get_topic_details(self, text):
        lowered = re.sub(r"johnny_test_\w+", " ", (text or "").lower())  # ignore the random temp-dir name
        for keyword, topic in KEYWORDS.items():
            if keyword in lowered and topic in self.categories:
                return topic, 0.9, text
        return "Unsorted_Miscellaneous", 0.0, text

    def get_topic(self, text):
        return self.get_topic_details(text)[0]

    def extract_text(self, file_path):
        return RealNLPEngine.extract_text(self, file_path)


def make_index(path, categories=None):
    import json
    categories = categories or {"Tax": "11.01", "Study": "12.01", "Unsorted_Miscellaneous": "80.01"}
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"categories": categories, "incubation": {}}, f)
    return path
