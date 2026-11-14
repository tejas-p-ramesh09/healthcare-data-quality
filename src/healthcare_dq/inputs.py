"""Bounded, deterministic input discovery and strict JSON decoding."""

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal

MAX_FILE = 16 * 1024**2
MAX_TOTAL = 256 * 1024**2
MAX_FILES = 512
MAX_ENTRIES = 150_000


class InputError(ValueError):
    def __init__(self, code, path=None, pointer=None):
        self.code, self.path, self.pointer = code, path, pointer
        super().__init__(code)


@dataclass(frozen=True)
class InputFile:
    path: str
    data: bytes
    sha256: str


def discover(root):
    if not root.is_dir():
        raise InputError("INPUT_DIRECTORY_REQUIRED")
    paths = sorted(root.glob("*.json"))
    if not 0 < len(paths) <= MAX_FILES:
        raise InputError("INPUT_FILE_COUNT")
    files = []
    total = 0
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise InputError("INVALID_INPUT_FILE", path.name)
        with path.open("rb") as stream:
            data = stream.read(min(MAX_FILE, MAX_TOTAL - total) + 1)
        total += len(data)
        if len(data) > MAX_FILE or total > MAX_TOTAL:
            raise InputError("INPUT_BYTE_LIMIT", path.name)
        files.append(InputFile(path.name, data, hashlib.sha256(data).hexdigest()))
    return files


def manifest(files):
    entries = sorted((f.path, len(f.data), f.sha256) for f in files)
    return hashlib.sha256(json.dumps(entries, separators=(",", ":")).encode()).hexdigest()


def unique_object(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def invalid_constant(_):
    raise ValueError("non-finite value")


def parse(file):
    try:
        # Decimal preserves JSON quantities through normalization.
        value = json.loads(file.data.decode("utf-8"), object_pairs_hook=unique_object,
                           parse_float=Decimal, parse_constant=invalid_constant)
    except (ValueError, RecursionError):
        raise InputError("INVALID_JSON", file.path) from None
    stack = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        if depth > 64:
            raise InputError("JSON_DEPTH_LIMIT", file.path)
        if isinstance(item, str) and ("\x00" in item or any(0xD800 <= ord(c) <= 0xDFFF for c in item)):
            raise InputError("INVALID_JSON_STRING", file.path)
        if isinstance(item, dict):
            stack.extend((v, depth + 1) for v in item.values())
            stack.extend((k, depth + 1) for k in item)
        elif isinstance(item, list):
            stack.extend((v, depth + 1) for v in item)
    if (not isinstance(value, dict) or value.get("resourceType") != "Bundle"
            or value.get("type") != "collection" or not isinstance(value.get("entry"), list)):
        raise InputError("EXPECTED_COLLECTION_BUNDLE", file.path)
    return value["entry"]
