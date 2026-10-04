import os
import re
from collections import Counter

from johnny.core.index_manager import IndexManager
from johnny.core.nlp_engine import NLPEngine
from johnny.core.utils import generate_safe_filename, safe_rename


STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "copy", "doc", "docx", "file",
    "for", "from", "gif", "in", "image", "images", "jpeg", "jpg", "json", "new", "of",
    "on", "or", "pdf", "png", "scan", "temp", "text", "the", "this", "to", "txt",
    "untitled", "version", "webp", "with", "xml", "zip", "downloads"
}

VAGUE_PATTERNS = [
    r"^\d+$",
    r"^[a-f0-9-]{8,}$",
    r"^img[_-]?\d+$",
    r"^image[_-]?\d+$",
    r"^document[_ -]?\d*$",
    r"^new[_ -]?document.*$",
    r"^scan[_ -]?\d*$",
]

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
SERIAL_NAME_PATTERN = re.compile(r"^(?P<serial>\d{1,6}(?:_[a-z0-9]*)*)$", re.IGNORECASE)


class ContextualRenamer:
    def __init__(self, index_path=None):
        self.index_manager = None
        self.nlp = None
        if index_path and os.path.exists(index_path):
            self.index_manager = IndexManager(index_path)
            categories = list(self.index_manager.data.get("categories", {}).keys())
            self.nlp = NLPEngine(categories, category_profiles=self.index_manager.profiles)

    def is_vague_name(self, file_name):
        base = os.path.splitext(os.path.basename(file_name))[0].strip().lower()
        ext = os.path.splitext(os.path.basename(file_name))[1].lower()
        if not base:
            return True
        if ext in IMAGE_EXTENSIONS and SERIAL_NAME_PATTERN.match(base):
            return False
        for pattern in VAGUE_PATTERNS:
            if re.match(pattern, base):
                return True
        words = re.findall(r"[a-zA-Z0-9]+", base)
        vague_markers = {"copy", "document", "file", "image", "scan", "temp", "new", "untitled"}
        if len(words) <= 1:
            return base in vague_markers
        return sum(1 for w in words if w in vague_markers) >= max(1, len(words) // 2)

    def _extract_serial_prefix(self, file_path):
        base = os.path.splitext(os.path.basename(file_path))[0].strip()
        match = SERIAL_NAME_PATTERN.match(base)
        if not match:
            return None
        return match.group("serial")

    def _tokenize_label(self, text):
        return [part.capitalize() for part in re.findall(r"[A-Za-z0-9]+", text or "") if part.lower() not in STOPWORDS]

    def _meaningful_base_tokens(self, file_path):
        base = os.path.splitext(os.path.basename(file_path))[0].strip()
        ext = os.path.splitext(os.path.basename(file_path))[1].lower()
        if not base:
            return []
        if ext in IMAGE_EXTENSIONS and SERIAL_NAME_PATTERN.match(base.lower()):
            return []
        if self.is_vague_name(os.path.basename(file_path)):
            return []
        return [part.capitalize() for part in re.findall(r"[A-Za-z0-9]+", base) if part.lower() not in STOPWORDS][:3]

    def _topic_tokens(self, text):
        if not self.nlp:
            return []
        topic, score, _ = self.nlp.get_topic_details(text)
        if topic == "Unsorted_Miscellaneous" or score < 0.20:
            return []
        return [t for t in re.findall(r"[a-zA-Z0-9]+", topic.replace("_", " ").lower()) if t not in STOPWORDS]

    def _collect_context(self, file_path):
        base = os.path.splitext(os.path.basename(file_path))[0]
        parent = os.path.basename(os.path.dirname(file_path))
        higher = os.path.dirname(os.path.dirname(file_path))
        higher_name = os.path.basename(higher) if higher else ""
        return base, parent, higher_name

    def _summarize_terms(self, content_text, context_text, ext):
        weighted_tokens = []
        token_sources = []

        if content_text:
            content_tokens = re.findall(r"[a-zA-Z0-9]+", content_text.lower())
            token_sources.extend((tok, 3) for tok in content_tokens)
        context_tokens = re.findall(r"[a-zA-Z0-9]+", context_text.lower())
        token_sources.extend((tok, 2) for tok in context_tokens)
        token_sources.extend((tok, 1) for tok in re.findall(r"[a-zA-Z0-9]+", ext.lower()))

        counter = Counter()
        first_seen = {}
        for idx, (token, weight) in enumerate(token_sources):
            if len(token) < 3 or token in STOPWORDS:
                continue
            counter[token] += weight
            first_seen.setdefault(token, idx)

        ranked = sorted(counter.items(), key=lambda item: (-item[1], first_seen[item[0]], item[0]))
        return [token for token, _ in ranked]

    def build_name(self, file_path, context_label=None, serial_as_suffix=False):
        ext = os.path.splitext(file_path)[1]
        base, parent, higher_name = self._collect_context(file_path)
        serial_prefix = self._extract_serial_prefix(file_path)
        content_text = self.nlp.extract_text(file_path) if self.nlp else ""

        context_text = " ".join(part for part in [base, parent, higher_name] if part)
        topic_tokens = self._topic_tokens((content_text or "") + " " + context_text)
        ranked_terms = self._summarize_terms(content_text, context_text, ext)

        selected = []
        for token in topic_tokens + ranked_terms:
            clean = token.strip().lower()
            if not clean or clean in STOPWORDS or clean in selected:
                continue
            selected.append(clean)
            if len(selected) >= 5:
                break

        if not selected:
            fallback = [t for t in re.findall(r"[a-zA-Z0-9]+", base.lower()) if t not in STOPWORDS]
            selected = fallback[:5]
        if not selected:
            fallback = [t for t in re.findall(r"[a-zA-Z0-9]+", parent.lower()) if t not in STOPWORDS]
            selected = fallback[:5]
        if not selected:
            return None

        human_words = [word.upper() if word.isupper() else word.capitalize() for word in selected]
        if context_label:
            label_words = self._tokenize_label(context_label)
            base_words = self._meaningful_base_tokens(file_path)
            if serial_prefix and serial_as_suffix:
                candidate_parts = label_words[:4] + [serial_prefix]
            elif serial_prefix:
                candidate_parts = label_words[:2] + [serial_prefix] + base_words[:2]
            elif base_words:
                candidate_parts = (label_words + base_words)[:5]
            else:
                fallback_words = human_words[: max(0, 5 - len(label_words))]
                candidate_parts = (label_words + fallback_words)[:5]
        elif serial_prefix:
            candidate_parts = [serial_prefix] + human_words[:4]
        else:
            candidate_parts = human_words
        candidate = "_".join(candidate_parts[:5])
        return candidate[:170].strip("_")

    def rename_file(self, file_path, dry_run=False, force=False, context_label=None, serial_as_suffix=False, reserved=None):
        if not os.path.isfile(file_path):
            return {"path": file_path, "status": "skipped", "reason": "not_a_file"}

        original_name = os.path.basename(file_path)
        if not force and not self.is_vague_name(original_name):
            return {"path": file_path, "status": "skipped", "reason": "name_already_meaningful"}

        new_base = self.build_name(file_path, context_label=context_label, serial_as_suffix=serial_as_suffix)
        if not new_base:
            return {"path": file_path, "status": "skipped", "reason": "insufficient_context"}

        root = os.path.dirname(file_path)
        ext = os.path.splitext(file_path)[1]
        new_path = generate_safe_filename(root, new_base, ext, max_length=170, reserved=reserved)
        if os.path.normcase(new_path) == os.path.normcase(file_path):
            return {"path": file_path, "status": "skipped", "reason": "same_name"}

        result = {
            "path": file_path,
            "status": "renamed" if not dry_run else "would_rename",
            "old_name": original_name,
            "new_name": os.path.basename(new_path),
        }
        if not dry_run:
            safe_rename(file_path, new_path)
            result["new_path"] = new_path
        return result

    def rename_target(self, target_path, dry_run=False, force=False, context_label=None, serial_as_suffix=False):
        results = []
        if os.path.isfile(target_path):
            return [self.rename_file(target_path, dry_run=dry_run, force=force, context_label=context_label, serial_as_suffix=serial_as_suffix)]

        reserved = set()
        for root, dirs, files in os.walk(target_path):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for file_name in files:
                if file_name.startswith("."):
                    continue
                file_path = os.path.join(root, file_name)
                results.append(
                    self.rename_file(
                        file_path,
                        dry_run=dry_run,
                        force=force,
                        context_label=context_label,
                        serial_as_suffix=serial_as_suffix,
                        reserved=reserved,
                    )
                )
        return results
