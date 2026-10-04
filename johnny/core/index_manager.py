import json
import os

# Every index gets this fallback bucket profile; other categories need their own under "profiles".
DEFAULT_PROFILES = {
    "Unsorted_Miscellaneous": {
            "description": "Fallback bucket for files that do not confidently fit the active Johnny Decimal taxonomy.",
            "aliases": [
                    "unsorted",
                    "miscellaneous",
                    "triage",
                    "review needed"
            ],
            "positive_examples": [
                    "unknown file.tmp",
                    "misc document.pdf",
                    "review later.txt"
            ]
    },
}

class IndexManager:
    def __init__(self, index_path, create=False):
        self.index_path = index_path
        if create:
            self._ensure_index_exists()
        elif not os.path.isfile(index_path):
            raise FileNotFoundError(f"Index not found: {index_path}")
        self.data = self._load()

    def _ensure_index_exists(self):
        if not os.path.exists(self.index_path):
            with open(self.index_path, 'w') as f:
                json.dump({"categories": {}, "incubation": {}}, f, indent=4)

    def _load(self):
        with open(self.index_path, 'r') as f:
            return json.load(f)

    def _save(self):
        tmp_path = self.index_path + ".tmp"
        with open(tmp_path, 'w') as f:
            json.dump(self.data, f, indent=4)
        os.replace(tmp_path, self.index_path)

    @property
    def profiles(self):
        """Category profiles stored in the index itself (optional top-level "profiles"), over the default bucket profile."""
        stored = self.data.get("profiles") or {}
        custom = {name: profile for name, profile in stored.items() if name in self.data["categories"] and isinstance(profile, dict)}
        return {**DEFAULT_PROFILES, **custom}

    def get_category_prefix(self, topic):
        return self.data["categories"].get(topic)

    def add_to_incubation(self, topic, file_path):
        if topic not in self.data["incubation"]:
            self.data["incubation"][topic] = []
        if file_path not in self.data["incubation"][topic]:
            self.data["incubation"][topic].append(file_path)
            self._save()
        return self.data["incubation"][topic]

    def remove_from_incubation(self, topic):
        if topic in self.data["incubation"]:
            del self.data["incubation"][topic]
            self._save()

    def mint_category(self, topic):
        """
        Creates a new Johnny.Decimal ID for a topic.
        We'll use a simplified JD approach: 10.01, 10.02, etc. incrementing the minor part up to 99, 
        then jumping to 11.01.
        """
        if topic in self.data["categories"]:
            return self.data["categories"][topic]

        existing_codes = list(self.data["categories"].values())
        if not existing_codes:
            new_code = "10.01"
        else:
            # Parse highest existing code
            highest = existing_codes[-1] # assuming order, but let's parse safely
            max_area = 10
            max_id = 0
            for code in existing_codes:
                parts = code.split('.')
                if len(parts) == 2:
                    a = int(parts[0])
                    i = int(parts[1])
                    if a > max_area or (a == max_area and i > max_id):
                        max_area = a
                        max_id = i
            
            # Increment
            if max_id >= 99:
                max_area += 1
                max_id = 1
            else:
                max_id += 1
            new_code = f"{max_area:02d}.{max_id:02d}"

        self.data["categories"][topic] = new_code
        self._save()
        return new_code
