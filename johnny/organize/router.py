import os
from johnny.core.planner import write_note
from johnny.core.utils import sanitize_topic_name, safe_rename, unique_path

class Router:
    def __init__(self, index_manager, root_dir):
        self.idx = index_manager
        self.root_dir = root_dir

    def route_file(self, file_path, topic):
        """
        Routes a file directly to its predefined semantic category folder.
        """
        # Phase 3 (Direct Routing Only)
        topic_sanitized = sanitize_topic_name(topic)
        jd_code = self.idx.get_category_prefix(topic)
        
        if not jd_code:
            print(f"Warning: Category '{topic}' not found in master index. Routing to Unsorted.")
            topic_sanitized = "Unsorted_Miscellaneous"
            jd_code = self.idx.get_category_prefix(topic_sanitized) or "80.01"

        print(f"Routing {os.path.basename(file_path)} to -> {jd_code}_{topic_sanitized}")
        target_folder = os.path.join(self.root_dir, f"{jd_code}_{topic_sanitized}")
        os.makedirs(target_folder, exist_ok=True)
        return self._move_file(file_path, target_folder)
            
    def route_special(self, source, folder, rel_dir="."):
        """Move a file or a whole folder into <root>/<folder>/<rel_dir>, keeping where it came from."""
        folder_path = os.path.join(self.root_dir, folder)
        target = os.path.normpath(os.path.join(folder_path, rel_dir))
        os.makedirs(target, exist_ok=True)
        write_note(folder_path, folder)
        print(f"Routing {os.path.basename(source)} to -> {folder}")
        return self._move_file(source, target)

    def _move_file(self, source, target_dir):
        base_name = os.path.basename(source)
        dest_path = os.path.join(target_dir, base_name)
        if dest_path != source:
            dest_path = unique_path(dest_path)
            try:
                safe_rename(source, dest_path)
            except Exception as e:
                print(f"Error moving {source}: {e}")
                return None
            print(f"Moved: {base_name} -> {dest_path}")
            return dest_path
        return source
