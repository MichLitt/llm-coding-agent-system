# untyped_utils.py — utility module with NO type annotations; agent must add them

from pathlib import Path
import re


def read_file(path, encoding="utf-8"):
    """Read a text file and return its contents as a string."""
    return Path(path).read_text(encoding=encoding)


def write_file(path, content, encoding="utf-8"):
    """Write content to a file, creating parent directories if needed."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding=encoding)


def split_into_chunks(text, chunk_size, overlap=0):
    """Split text into overlapping chunks of chunk_size characters."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    step = max(1, chunk_size - overlap)
    return [text[i:i + chunk_size] for i in range(0, len(text), step)]


def extract_emails(text):
    """Return a list of email addresses found in text."""
    pattern = r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
    return re.findall(pattern, text)


def flatten(nested, depth=None):
    """Recursively flatten a nested list up to depth levels."""
    result = []
    for item in nested:
        if isinstance(item, list) and (depth is None or depth > 0):
            next_depth = None if depth is None else depth - 1
            result.extend(flatten(item, next_depth))
        else:
            result.append(item)
    return result


def group_by(items, key_fn):
    """Group a list of items into a dict by key_fn(item)."""
    groups = {}
    for item in items:
        k = key_fn(item)
        groups.setdefault(k, []).append(item)
    return groups


def merge_dicts(*dicts, deep=False):
    """Merge multiple dicts. If deep=True, recursively merge nested dicts."""
    result = {}
    for d in dicts:
        if deep:
            for k, v in d.items():
                if k in result and isinstance(result[k], dict) and isinstance(v, dict):
                    result[k] = merge_dicts(result[k], v, deep=True)
                else:
                    result[k] = v
        else:
            result.update(d)
    return result


def clamp(value, min_val, max_val):
    """Clamp value to [min_val, max_val]."""
    return max(min_val, min(max_val, value))


def paginate(items, page, page_size):
    """Return a slice of items for the given 1-indexed page."""
    if page < 1:
        raise ValueError("page must be >= 1")
    start = (page - 1) * page_size
    return items[start:start + page_size]
