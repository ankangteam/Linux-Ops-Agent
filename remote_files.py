"""Fixed remote Python helper. Sent over SSH; never accepts executable code as data."""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import uuid

MAX_BYTES = 64 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def touch_version(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return "MISSING"
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("Touch supports only regular files with one hard link")
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def checked_path(raw, roots):
    path = Path(raw)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Use an absolute path without '..'")
    # Reject symlinks even when they currently resolve inside an allowed root.
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError("Symbolic links are not supported")
    resolved = path.resolve()
    if not any(resolved == root or root in resolved.parents for root in roots):
        raise ValueError("Path is outside OPS_FILE_ROOTS")
    return path


def read_bytes(path):
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("Only regular files are supported")
    with path.open("rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("Only regular files with one hard link are supported")
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("File exceeds 64 KiB limit")
    data.decode("utf-8")
    if b"\0" in data:
        raise ValueError("Binary files are not supported")
    return data


def execute(request):
    roots = []
    for raw in request["roots"]:
        root = Path(raw)
        if not root.is_absolute() or root == Path(root.anchor) or ".." in root.parts:
            raise ValueError("Configure specific absolute directories, not filesystem root")
        if any(part.is_symlink() for part in (root, *root.parents)):
            raise ValueError("Allowed roots must not contain symbolic links")
        if not root.is_dir():
            raise ValueError("Allowed root does not exist: " + str(root))
        roots.append(root.resolve())
    if not roots:
        raise ValueError("Set OPS_FILE_ROOTS to enable file tools")
    path = checked_path(request["path"], roots)
    op = request["op"]
    if op == "touch_snapshot":
        version = touch_version(path)
        return {"exists": version != "MISSING", "version": version}
    if op == "touch":
        version = touch_version(path)
        if version != request["expected_version"]:
            raise ValueError("File changed after preview; propose touch again")
        if version == "MISSING":
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
            os.close(fd)
        else:
            os.utime(path, None, follow_symlinks=False)
        return {"touched": str(path), "created": version == "MISSING", "backup": None}
    if op == "list":
        entries = []
        with os.scandir(path) as iterator:
            for entry in iterator:
                if len(entries) == 200:
                    return {"entries": entries, "truncated": True}
                entries.append({"name": entry.name, "type": "symlink" if entry.is_symlink() else "directory" if entry.is_dir(follow_symlinks=False) else "file"})
        return {"entries": sorted(entries, key=lambda e: e["name"]), "truncated": False}
    if op == "snapshot":
        if not path.exists():
            return {"exists": False, "sha256": "MISSING", "content": ""}
        data = read_bytes(path)
        return {"exists": True, "sha256": digest(data), "content": data.decode("utf-8")}
    if op == "read":
        data = read_bytes(path)
        lines = data.decode("utf-8").splitlines(keepends=True)
        start, count = request["start_line"], request["line_count"]
        if type(start) is not int or type(count) is not int or start < 1 or not 1 <= count <= 200:
            raise ValueError("start_line >= 1; line_count between 1 and 200")
        return {"sha256": digest(data), "total_lines": len(lines), "start_line": start,
                "content": "".join(lines[start - 1:start - 1 + count])}
    if op == "search":
        query = request["query"]
        if not isinstance(query, str) or not query or len(query) > 256:
            raise ValueError("Search requires a literal string of 1–256 characters")
        matches, scanned, skipped = [], 0, 0
        for directory, dirs, files in os.walk(path, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not (Path(directory) / d).is_symlink())
            scanned += 1
            for name in sorted(files):
                scanned += 1
                if scanned > 500:
                    return {"matches": matches, "truncated": True, "skipped": skipped}
                file = Path(directory) / name
                try:
                    checked_path(str(file), roots)
                    lines = read_bytes(file).decode("utf-8").splitlines()
                except (OSError, ValueError, UnicodeError):
                    skipped += 1
                    continue
                for number, line in enumerate(lines, 1):
                    if query in line:
                        matches.append({"path": str(file), "line": number, "text": line[:300]})
                        if len(matches) >= 30:
                            return {"matches": matches, "truncated": True, "skipped": skipped}
            if scanned > 500:
                return {"matches": matches, "truncated": True, "skipped": skipped}
        return {"matches": matches, "truncated": False, "skipped": skipped}
    if op == "mkdir":
        path.mkdir(mode=0o750, parents=False, exist_ok=False)
        return {"created": str(path)}
    if op == "write":
        data = request["content"].encode("utf-8")
        if len(data) > MAX_BYTES or b"\0" in data:
            raise ValueError("Only UTF-8 text up to 64 KiB is supported")
        old = read_bytes(path) if path.exists() else None
        expected = request["expected_sha256"]
        if (digest(old) if old is not None else "MISSING") != expected:
            raise ValueError("File changed after preview; read and propose again")
        backup = None
        mode = 0o640
        if old is not None:
            info = path.stat()
            if os.geteuid() not in (0, info.st_uid):
                raise ValueError("Editing requires ownership by the SSH account")
            mode = stat.S_IMODE(info.st_mode)
            if mode & 0o7000:
                raise ValueError("Special permission bits are not supported")
            if request.get("backup", False) is True:
                backup = path.with_name(path.name + ".ops-backup-" + uuid.uuid4().hex)
                with backup.open("xb") as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    stream.write(old)
                    stream.flush()
                    os.fsync(stream.fileno())
        fd, temp = tempfile.mkstemp(prefix=".ops-edit-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                os.fchmod(stream.fileno(), mode)
                if old is not None:
                    os.fchown(stream.fileno(), info.st_uid if os.geteuid() == 0 else -1, info.st_gid)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            checked_path(str(path), roots)
            if old is None:
                os.link(temp, path)  # No overwrite if another process created the file.
            else:
                if digest(read_bytes(path)) != expected:
                    raise ValueError("File changed during write; operation aborted")
                os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        return {"written": str(path), "sha256": digest(data), "backup": str(backup) if backup else None}
    raise ValueError("Unsupported file operation")


if __name__ == "__main__":
    try:
        request = json.loads(sys.stdin.read())
        print(json.dumps(execute(request), ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        sys.exit(1)
