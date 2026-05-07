import re
from pathlib import Path

SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._ -]+")


def sanitize_filename(value: str, default: str = "resume") -> str:
    name = Path(value or default).name
    name = name.replace("\\", "_").replace("/", "_")
    name = SAFE_FILENAME_RE.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip(" ._")

    if not name:
        name = default

    if name.upper() in {"CON", "PRN", "AUX", "NUL"}:
        name = f"{name}_file"

    return name[:120]