"""Descriptor-relative, no-follow I/O; cooperative locks and reversible transactions."""
import base64
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import uuid


class SafetyError(RuntimeError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encode(obj):
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def absolute(path):
    p = Path(os.path.abspath(os.path.expanduser(str(path))))
    for ancestor in reversed([p] + list(p.parents)):
        if ancestor.is_symlink():
            raise SafetyError("Symlink path component refused: " + str(ancestor))
    return p


class Tree:
    """Root held open: descendant path swaps cannot redirect operations outside it."""
    def __init__(self, root, create=False, private=False):
        self.root = absolute(root)
        if create:
            # Walk from / with mkdirat/openat; never follow concurrent symlink swaps.
            fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
            try:
                for part in self.root.parts[1:]:
                    try:
                        os.mkdir(part, 0o700 if private else 0o755, dir_fd=fd)
                    except FileExistsError:
                        pass
                    nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    os.close(fd)
                    fd = nxt
            finally:
                os.close(fd)
        self.fd = os.open(str(self.root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        if private:
            s = os.fstat(self.fd)
            if s.st_uid != os.getuid() or s.st_mode & 0o077:
                self.close()
                raise SafetyError("Private directory must be owned by you and mode 0700: " + str(root))

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @staticmethod
    def parts(rel):
        p = PurePosixPath(rel)
        if not rel or p.is_absolute() or ".." in p.parts or "\x00" in rel or "\\" in rel:
            raise SafetyError("Unsafe relative path")
        return p.parts

    @contextlib.contextmanager
    def parent(self, rel, create=False, made=None):
        parts = self.parts(rel)
        fd = os.dup(self.fd)
        prefix = []
        try:
            for part in parts[:-1]:
                prefix.append(part)
                if create:
                    try:
                        os.mkdir(part, 0o700, dir_fd=fd)
                        if made is not None:
                            made.append("/".join(prefix))
                    except FileExistsError:
                        pass
                nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = nxt
            yield fd, parts[-1]
        finally:
            os.close(fd)

    def exists(self, rel):
        try:
            with self.parent(rel) as (fd, name):
                os.stat(name, dir_fd=fd, follow_symlinks=False)
            return True
        except FileNotFoundError:
            return False

    def read(self, rel, limit=4 * 1024 * 1024):
        with self.parent(rel) as (fd, name):
            f = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            try:
                s = os.fstat(f)
                if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1:
                    raise SafetyError("Non-regular or hard-linked file refused: " + rel)
                if s.st_size > limit:
                    raise SafetyError("File exceeds read limit: " + rel)
                chunks = []
                total = 0
                while True:
                    chunk = os.read(f, min(65536, limit + 1 - total))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > limit:
                        raise SafetyError("File grew beyond read limit: " + rel)
                return b"".join(chunks)
            finally:
                os.close(f)

    def hash(self, rel):
        try:
            return digest(self.read(rel))
        except FileNotFoundError:
            return None

    def write(self, rel, data, expected=None, made=None, mode=0o600):
        """None means exclusive creation, otherwise compare hash then atomic replace.

        flock serializes Chorotheia writers. POSIX has no atomic compare-and-rename
        against unrelated editors; the tiny last-check/rename race is documented.
        """
        with self.parent(rel, create=True, made=made) as (fd, name):
            tmp = ".chorotheia-tmp-" + uuid.uuid4().hex
            f = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=fd)
            try:
                with os.fdopen(f, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                if expected is None:
                    # linkat is an atomic no-clobber publish; target never followed.
                    os.link(tmp, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
                else:
                    if self.hash(rel) != expected:
                        raise SafetyError("Concurrent edit detected: " + rel)
                    os.replace(tmp, name, src_dir_fd=fd, dst_dir_fd=fd)
                os.fsync(fd)
            finally:
                try:
                    os.unlink(tmp, dir_fd=fd)
                except FileNotFoundError:
                    pass

    def remove(self, rel, expected):
        if self.hash(rel) != expected:
            raise SafetyError("Modified file preserved: " + rel)
        with self.parent(rel) as (fd, name):
            os.unlink(name, dir_fd=fd)
            os.fsync(fd)

    def rmdir(self, rel):
        try:
            with self.parent(rel) as (fd, name):
                os.rmdir(name, dir_fd=fd)
        except OSError:
            pass  # Never recurse; populated or replaced directories survive.

    def snapshot(self):
        """SHA-256 every existing regular file, including Git internals; no follows."""
        result = {}

        def walk(fd, prefix):
            for name in sorted(os.listdir(fd)):
                rel = prefix + name
                s = os.stat(name, dir_fd=fd, follow_symlinks=False)
                mode = stat.S_IMODE(s.st_mode)
                if stat.S_ISLNK(s.st_mode):
                    result[rel] = ["symlink", os.readlink(name, dir_fd=fd), mode]
                elif stat.S_ISDIR(s.st_mode):
                    result[rel + "/"] = ["directory", mode]
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        walk(child, rel + "/")
                    finally:
                        os.close(child)
                elif stat.S_ISREG(s.st_mode):
                    f = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                    try:
                        if not stat.S_ISREG(os.fstat(f).st_mode):
                            raise SafetyError("File type changed during scan")
                        sha = hashlib.sha256()
                        while True:
                            chunk = os.read(f, 1024 * 1024)
                            if not chunk:
                                break
                            sha.update(chunk)
                        result[rel] = ["file", sha.hexdigest(), mode]
                    finally:
                        os.close(f)
                else:
                    result[rel] = ["special", mode]
        walk(self.fd, "")
        return result


@contextlib.contextmanager
def lock(home, key):
    name = "locks/" + digest(str(key).encode()) + ".lock"
    with home.parent(name, create=True) as (fd, leaf):
        f = os.open(leaf, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=fd)
        try:
            s = os.fstat(f)
            if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1:
                raise SafetyError("Invalid lock file")
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SafetyError("Another Chorotheia writer holds this project lock")
            yield
        finally:
            os.close(f)  # Lock files are persistent; never delete a live lock inode.


class Transaction:
    def __init__(self, tree, home, label):
        self.tree, self.home = tree, home
        self.id = uuid.uuid4().hex
        self.rel = "backups/transactions/" + self.id + ".json"
        self.doc = {"id": self.id, "label": label, "root": str(tree.root),
                    "phase": "pending", "entries": [], "directories": [], "external_directories": {}}
        self.jhash = None

    def save(self):
        b = encode(self.doc)
        self.home.write(self.rel, b, self.jhash)
        self.jhash = digest(b)

    def put(self, rel, data, expected=None, mode=0o600, private=False, other=None):
        target = other or (self.home if private else self.tree)
        old = None
        if expected is None and target.exists(rel):
            raise SafetyError("Exclusive creation target occupied: " + rel)
        if expected is not None:
            old = target.read(rel)
            if digest(old) != expected:
                raise SafetyError("Concurrent edit before transaction: " + rel)
        entry = {"path": rel, "private": private, "external_root": str(other.root) if other else None,
                 "applied": False, "before": base64.b64encode(old).decode() if old is not None else None,
                 "after": digest(data), "before_hash": expected}
        self.doc["entries"].append(entry)
        self.save()  # Write-ahead record survives interruption.
        dirs = self.doc["external_directories"].setdefault(str(other.root), []) if other else self.doc["directories"]
        target.write(rel, data, expected, None if private else dirs, mode)
        entry["applied"] = True
        self.save()

    def rollback(self):
        preserved = []
        for e in reversed(self.doc["entries"]):
            p = e["path"]
            external = Tree(e["external_root"]) if e.get("external_root") else None
            target = external or (self.home if e.get("private") else self.tree)
            try:
                current = target.hash(p)
                if current is None:
                    continue
                if not e.get("applied"):
                    preserved.append(p)  # Ambiguous crash window: never guess ownership.
                    continue
                if current != e["after"]:
                    preserved.append(p)
                    continue
                if e["before"] is None:
                    target.remove(p, e["after"])
                else:
                    target.write(p, base64.b64decode(e["before"]), e["after"])
            except (OSError, SafetyError):
                preserved.append(p)
            finally:
                if external:
                    external.close()
        for d in reversed(self.doc["directories"]):
            self.tree.rmdir(d)
        for root, dirs in self.doc.get("external_directories", {}).items():
            with Tree(root) as external:
                for d in reversed(dirs):
                    external.rmdir(d)
        self.doc["phase"] = "rolled_back"
        self.doc["preserved"] = preserved
        self.save()
        return preserved

    def __enter__(self):
        self.save()
        return self

    def __exit__(self, typ, value, tb):
        if typ:
            self.rollback()
        else:
            self.doc["phase"] = "committed"
            self.save()
