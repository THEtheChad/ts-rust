#!/usr/bin/env python3
"""P2: page-cache residency and eviction of a tree's regular files (no root needed; reads only, never writes).
  scripts/perf-rearch/p2/pagecache.py resident PATH...   resident MiB / total MiB (mincore)
  scripts/perf-rearch/p2/pagecache.py evict PATH...      posix_fadvise(DONTNEED) on every file (drops clean pages)
WSL's autoMemoryReclaim=gradual drops idle page cache, so a run that waited for an idle machine can start cold;
`evict` reproduces that state on purpose. Symlinks are not followed (realpath targets are listed explicitly)."""
import ctypes, mmap, os, sys

libc = ctypes.CDLL(None, use_errno=True)
libc.mincore.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_char_p]
libc.mmap.restype = ctypes.c_void_p
libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_long]
libc.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
PAGE = os.sysconf("SC_PAGE_SIZE")


def files(paths):
    for p in paths:
        if os.path.isfile(p) and not os.path.islink(p):
            yield p
            continue
        for d, _, names in os.walk(p):
            for n in names:
                f = os.path.join(d, n)
                if os.path.isfile(f) and not os.path.islink(f):
                    yield f


def resident(f):
    size = os.path.getsize(f)
    if size == 0:
        return 0, 0
    fd = os.open(f, os.O_RDONLY)
    try:
        addr = libc.mmap(None, size, mmap.PROT_READ, mmap.MAP_SHARED, fd, 0)
    finally:
        os.close(fd)
    if addr in (None, ctypes.c_void_p(-1).value):
        return 0, size
    n = (size + PAGE - 1) // PAGE
    vec = ctypes.create_string_buffer(n)
    libc.mincore(addr, size, vec)
    libc.munmap(addr, size)
    return sum(b & 1 for b in vec.raw) * PAGE, size


def main():
    cmd, paths = sys.argv[1], sys.argv[2:]
    if cmd == "resident":
        r = t = 0
        for f in files(paths):
            try:
                a, b = resident(f)
            except (OSError, ValueError):
                continue
            r, t = r + a, t + b
        print(f"resident {r / 2**20:.1f} MiB of {t / 2**20:.1f} MiB")
    elif cmd == "evict":
        n = 0
        for f in files(paths):
            try:
                fd = os.open(f, os.O_RDONLY)
            except OSError:
                continue
            try:
                os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
                n += 1
            finally:
                os.close(fd)
        print(f"evicted {n} files")
    else:
        print(__doc__)
        return 2


if __name__ == "__main__":
    sys.exit(main())
