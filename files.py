"""Browse folders and read images on this machine, so a plot can be opened onto the board."""
import base64
import os
from pathlib import Path

IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml"}
MAX_IMAGE = 20 * 1024 * 1024


def resolve(path):
    p = Path(os.path.expanduser(path or "~"))
    if not p.is_absolute():
        raise ValueError("path must be absolute or start with ~")
    return p.resolve()


def list_dir(path):
    """Folders and image files in `path`, dotfiles hidden, images newest first."""
    d = resolve(path)
    if not d.exists():
        raise FileNotFoundError(d)
    if not d.is_dir():
        raise NotADirectoryError(f"not a folder: {d}")
    dirs, images = [], []
    for entry in os.scandir(d):
        if entry.name.startswith("."):
            continue
        try:
            if entry.is_dir():
                dirs.append({"name": entry.name, "dir": True})
            elif Path(entry.name).suffix.lower() in IMAGE_TYPES and entry.is_file():
                st = entry.stat()
                images.append({"name": entry.name, "dir": False, "size": st.st_size, "mtime": st.st_mtime})
        except OSError:
            continue  # broken symlink or file vanished mid-listing
    dirs.sort(key=lambda e: e["name"].casefold())
    images.sort(key=lambda e: e["mtime"], reverse=True)
    return {"path": str(d), "parent": str(d.parent) if d != d.parent else None, "entries": dirs + images}


def read_image(path):
    # Only image types are readable, so a logged-in page can't pull .env files or keys.
    p = resolve(path)
    mime = IMAGE_TYPES.get(p.suffix.lower())
    if not mime:
        raise ValueError(f"not an image: {p.name}")
    if p.is_dir():
        raise ValueError(f"not a file: {p.name}")
    if p.stat().st_size > MAX_IMAGE:
        raise ValueError(f"image larger than {MAX_IMAGE // 2**20} MB: {p.name}")
    data = base64.b64encode(p.read_bytes()).decode()
    return {"name": p.name, "mimeType": mime, "dataURL": f"data:{mime};base64,{data}"}


def call(fn, path):
    """Run list_dir/read_image, mapping errors to (status, body) for the HTTP layer."""
    try:
        return 200, fn(path)
    except FileNotFoundError:
        return 404, {"error": f"not found: {path}"}
    except PermissionError:
        return 403, {"error": f"permission denied: {path}"}
    except (ValueError, NotADirectoryError) as e:
        return 400, {"error": str(e)}
