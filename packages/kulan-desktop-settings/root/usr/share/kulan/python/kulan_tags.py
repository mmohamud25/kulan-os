"""Kulan OS tags for files and folders, as on a Mac (Red, Orange, Yellow,
Green, Blue, Purple, Grey).

A file's tags are stored with the file, in the freedesktop.org extended
attribute "user.xdg.tags" (comma-separated; KDE uses the same one), so they
move and copy with it. Where a file system has no extended attributes, they
go in GIO metadata instead. A small index (~/.local/share/kulan/tags.json,
only on this computer) lets search find tagged files quickly; every result is
checked against the file itself, so the index can never show a tag a file no
longer has.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from gi.repository import Gio, GLib

TAGS = [("red", "Red"), ("orange", "Orange"), ("yellow", "Yellow"), ("green", "Green"),
        ("blue", "Blue"), ("purple", "Purple"), ("grey", "Grey")]
NAMES = {tag: name for tag, name in TAGS}
XATTR = "user.xdg.tags"
METADATA = "metadata::kulan-tags"
INDEX = Path(GLib.get_user_data_dir()) / "kulan" / "tags.json"
# One folder of links per tag in use, named in English (Kulan Files shows
# them, translated, under Tags in its sidebar).
TAG_FOLDERS = Path(GLib.get_user_data_dir()) / "kulan" / "tag-folders"
MAX_INDEX = 20000


def _clean(values) -> list[str]:
    """Known tags only, in Kulan's order (stored names are lowercase)."""
    wanted = {str(v).strip().lower() for v in values}
    wanted |= {"grey" for v in values if str(v).strip().lower() == "gray"}
    return [tag for tag, _name in TAGS if tag in wanted]


def read_tags(path: str) -> list[str]:
    try:
        raw = os.getxattr(path, XATTR).decode("utf-8", "replace")
        return _clean(raw.split(","))
    except OSError:
        pass
    try:
        info = Gio.File.new_for_path(path).query_info(METADATA, Gio.FileQueryInfoFlags.NONE, None)
        raw = info.get_attribute_string(METADATA) or ""
        return _clean(raw.split(","))
    except GLib.Error:
        return []


def write_tags(path: str, tags: list[str]) -> bool:
    """Set a file's tags (an empty list removes them). True if it worked."""
    tags = _clean(tags)
    value = ",".join(tags)
    try:
        if tags:
            os.setxattr(path, XATTR, value.encode())
        else:
            try:
                os.removexattr(path, XATTR)
            except OSError as exc:
                if exc.errno not in (61, 95):  # ENODATA: none set; EOPNOTSUPP: see below
                    raise
        stored = True
    except OSError:
        stored = False
    # Also in GIO metadata where extended attributes don't work (some USB
    # sticks, network folders); harmless where they do.
    try:
        gfile = Gio.File.new_for_path(path)
        if tags:
            gfile.set_attribute_string(METADATA, value, Gio.FileQueryInfoFlags.NONE, None)
        else:
            gfile.set_attribute(METADATA, Gio.FileAttributeType.INVALID, None, Gio.FileQueryInfoFlags.NONE, None)
        stored = True
    except GLib.Error:
        pass
    if stored:
        remember(path, tags)
    return stored


def load_index() -> dict[str, list[str]]:
    try:
        data = json.loads(INDEX.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(p): _clean(t) for p, t in data.items() if isinstance(t, list)}


def save_index(index: dict[str, list[str]]) -> None:
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    items = dict(list(index.items())[-MAX_INDEX:])
    fd, tmp = tempfile.mkstemp(prefix=".tags.", dir=INDEX.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(items, fh)
        os.chmod(tmp, 0o600)
        os.replace(tmp, INDEX)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def remember(path: str, tags: list[str]) -> None:
    """Keep the index in step with a file (Files calls this as it shows files)."""
    index = load_index()
    if tags:
        if index.get(path) == tags:
            return
        index.pop(path, None)
        index[path] = tags
    elif path in index:
        del index[path]
    else:
        return
    save_index(index)
    sync_tag_folders(index)


def _link_names(paths: list[str]) -> dict[str, str]:
    """A link name for each path: its own name, or "name (2).txt" when two
    tagged files share a name."""
    names: dict[str, str] = {}
    for path in sorted(paths):
        base = os.path.basename(path.rstrip("/")) or path
        stem, ext = os.path.splitext(base)
        name, n = base, 2
        while name in names:
            name = f"{stem} ({n}){ext}"
            n += 1
        names[name] = path
    return names


def _no_links_on_the_way(folder: Path) -> bool:
    """True when neither the folder nor the folders above it (inside the data
    folder) are symbolic links, so changes stay inside Kulan's own folders."""
    data = Path(GLib.get_user_data_dir())
    path = folder
    while True:
        if path.is_symlink():
            return False
        if path == data or path.parent == path:
            return True
        path = path.parent


def sync_tag_folders(index: dict[str, list[str]] | None = None) -> None:
    """Make each tag's folder hold links to exactly the files with that tag,
    and remove the folders of tags nobody uses. Only links Kulan made itself
    (listed in the folder's .kulan-links) are ever removed; anything else put
    there stays. Errors are ignored: tags themselves are already saved."""
    if index is None:
        index = load_index()
    for tag, name in TAGS:
        try:
            _sync_one(TAG_FOLDERS / name, _link_names(
                [p for p, tags in index.items() if tag in tags and os.path.lexists(p)]))
        except OSError:
            pass


def _sync_one(folder: Path, wanted: dict[str, str]) -> None:
    if not _no_links_on_the_way(folder):
        return
    manifest = folder / ".kulan-links"
    try:
        ours = set(manifest.read_text(encoding="utf-8").splitlines())
    except (OSError, UnicodeDecodeError):
        ours = set()
    for link_name in list(ours):
        link = folder / link_name
        if "/" in link_name or link_name in ("", ".", ".."):
            ours.discard(link_name)
            continue
        try:
            current = os.readlink(link)
        except OSError:
            ours.discard(link_name)  # gone, or no longer a link: not ours any more
            continue
        if wanted.get(link_name) != current:
            try:
                os.unlink(link)
            except OSError:
                pass
            ours.discard(link_name)
    if not wanted:
        try:
            manifest.unlink()
        except OSError:
            pass
        try:
            folder.rmdir()  # only when empty: anything else put there stays
        except OSError:
            pass
        return
    folder.mkdir(parents=True, exist_ok=True)
    for link_name, target in wanted.items():
        link = folder / link_name
        if os.path.islink(link) and os.readlink(link) == target:
            ours.add(link_name)
            continue
        if os.path.lexists(link):
            continue  # something that isn't Kulan's link: leave it
        try:
            os.symlink(target, link)
            ours.add(link_name)
        except OSError:
            pass
    tmp = folder / ".kulan-links.new"
    tmp.write_text("".join(f"{n}\n" for n in sorted(ours)), encoding="utf-8")
    os.replace(tmp, manifest)

def find(tag: str) -> list[str]:
    """Paths of files that have the tag now (checked one by one), newest first."""
    tag = _clean([tag])
    if not tag:
        return []
    tag = tag[0]
    index = load_index()
    found, stale = [], []
    for path, tags in index.items():
        if tag not in tags:
            continue
        if not os.path.exists(path):
            stale.append(path)
        elif tag in read_tags(path):
            found.append(path)
        else:
            stale.append(path)
    if stale:
        for path in stale:
            index.pop(path, None)
        save_index(index)
    found.sort(key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0, reverse=True)
    return found


def tag_for_words(words: list[str]) -> str | None:
    """'red', 'tag red', 'red tag', 'tagged red' -> 'red'; otherwise None."""
    words = [w.strip().lower() for w in words if w.strip()]
    rest = [w for w in words if w not in ("tag", "tags", "tagged", "#")]
    if len(rest) != 1:
        return None
    word = rest[0].lstrip("#")
    if word == "gray":
        word = "grey"
    return word if word in NAMES else None
