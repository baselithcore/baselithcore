import io
import zipfile
from pathlib import Path

path = Path("plugins/baselithbot")
excluded_dir_parts = {
    "__pycache__",
    "node_modules",
    ".ruff_cache",
    ".mypy_cache",
    ".pytest_cache",
    ".state",
    "build",
    "dist",
    ".egg-info",
}
excluded_ui_src_prefix = ("ui", "src")

zip_buffer = io.BytesIO()
with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
    for file_path in path.rglob("*"):
        if not file_path.is_file():
            continue
        parts = file_path.relative_to(path).parts
        if any(p.startswith(".") for p in parts):
            continue
        if any(p in excluded_dir_parts for p in parts):
            if len(parts) >= 2 and parts[0] == "ui" and parts[1] == "dist":
                pass
            else:
                continue
        if any(p.endswith(".egg-info") for p in parts):
            continue
        if (
            len(parts) >= 2
            and parts[0] == excluded_ui_src_prefix[0]
            and parts[1] == excluded_ui_src_prefix[1]
        ):
            continue
        if file_path.suffix in {".pyc", ".pyo"}:
            continue

        zip_file.write(file_path, file_path.relative_to(path))

with zipfile.ZipFile(zip_buffer, "r") as zf:
    has_dist = len([n for n in zf.namelist() if "ui/dist" in n]) > 0
print(f"Zip size: {len(zip_buffer.getvalue()) / 1024 / 1024:.2f} MB")
print(f"Has ui/dist: {has_dist}")
