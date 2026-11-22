import os, errno, stat, asyncio, logging, threading, argparse
from collections import OrderedDict
from fuse import FUSE, FuseOSError, Operations
from pymobiledevice3.lockdown import create_using_mobdev2
from pymobiledevice3.services.afc import AfcService
from pymobiledevice3.services.house_arrest import HouseArrestService

logging.basicConfig(level=logging.INFO)
log = logging.getLogger('iphonefs')

CHUNK_SIZE = 1024 * 1024        # 1 MB per cache chunk
READAHEAD_CHUNKS = 3            # prefetch 3 chunks ahead
MAX_CACHE_CHUNKS = 64           # ~64 MB max cache


class ReadAheadCache:
    def __init__(self):
        self._cache = OrderedDict()
        self._lock = threading.Lock()
        self._pending = set()

    def _chunk_key(self, path, chunk_idx):
        return (path, chunk_idx)

    def get(self, path, chunk_idx):
        key = self._chunk_key(path, chunk_idx)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        return None

    def put(self, path, chunk_idx, data):
        key = self._chunk_key(path, chunk_idx)
        with self._lock:
            # Drop stale prefetch result if invalidated while fetch was in-flight
            if key not in self._pending:
                return
            self._cache[key] = data
            self._cache.move_to_end(key)
            if len(self._cache) > MAX_CACHE_CHUNKS:
                self._cache.popitem(last=False)
            self._pending.discard(key)

    def is_pending(self, path, chunk_idx):
        key = self._chunk_key(path, chunk_idx)
        with self._lock:
            return key in self._pending

    def mark_pending(self, path, chunk_idx):
        key = self._chunk_key(path, chunk_idx)
        with self._lock:
            self._pending.add(key)

    def invalidate(self, path):
        with self._lock:
            keys = [k for k in self._cache if k[0] == path]
            for k in keys:
                del self._cache[k]
            self._pending = {p for p in self._pending if p[0] != path}


class IPhoneFS(Operations):
    def __init__(self, afc, loop, read_only=False):
        self.afc = afc
        self.loop = loop
        self.read_only = read_only
        self._open_files = {}
        self._next_fh = 1
        self._cache = ReadAheadCache()
        log.info(f"IPhoneFS initialized (read_only={read_only})")

    def _run(self, coro):
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return future.result(timeout=30)

    def _check_writable(self):
        if self.read_only:
            raise FuseOSError(errno.EROFS)

    def _fetch_chunk(self, path, chunk_idx):
        """Fetch one chunk from AFC and store in cache."""
        if self._cache.get(path, chunk_idx) is not None:
            return
        if self._cache.is_pending(path, chunk_idx):
            return
        self._cache.mark_pending(path, chunk_idx)
        offset = chunk_idx * CHUNK_SIZE
        try:
            async def _read():
                handle = await self.afc.fopen(path, 'r')
                try:
                    await self.afc.fseek(handle, offset, 0)
                    return await self.afc.fread(handle, CHUNK_SIZE)
                finally:
                    await self.afc.fclose(handle)
            future = asyncio.run_coroutine_threadsafe(_read(), self.loop)
            data = future.result(timeout=30)
            self._cache.put(path, chunk_idx, data)
        except Exception:
            # Don't cache error result — clear pending so next read retries from device
            with self._cache._lock:
                self._cache._pending.discard((path, chunk_idx))

    def _prefetch(self, path, start_chunk):
        """Kick off background prefetch for upcoming chunks."""
        for i in range(1, READAHEAD_CHUNKS + 1):
            chunk_idx = start_chunk + i
            if self._cache.get(path, chunk_idx) is None and not self._cache.is_pending(path, chunk_idx):
                t = threading.Thread(target=self._fetch_chunk, args=(path, chunk_idx), daemon=True)
                t.start()

    def getattr(self, path, fh=None):
        try:
            info = self._run(self.afc.stat(path))
            if info['st_ifmt'] == 'S_IFDIR':
                return dict(st_mode=(stat.S_IFDIR | 0o755), st_nlink=int(info.get('st_nlink', 2)))
            else:
                return dict(st_mode=(stat.S_IFREG | 0o644), st_nlink=1, st_size=int(info.get('st_size', 0)))
        except Exception:
            raise FuseOSError(errno.ENOENT)

    def readdir(self, path, fh):
        try:
            entries = self._run(self.afc.listdir(path))
            return ['.', '..'] + entries
        except Exception:
            raise FuseOSError(errno.ENOENT)

    def open(self, path, flags):
        import os as _os
        writable = bool(flags & (_os.O_WRONLY | _os.O_RDWR))
        if writable and self.read_only:
            raise FuseOSError(errno.EROFS)
        fh = self._next_fh
        self._next_fh += 1
        self._open_files[fh] = {'path': path, 'buf': None, 'writable': writable}
        return fh

    def read(self, path, size, offset, fh):
        try:
            chunk_idx = offset // CHUNK_SIZE
            chunk_offset = offset % CHUNK_SIZE

            # Ensure this chunk is cached
            if self._cache.get(path, chunk_idx) is None:
                self._fetch_chunk(path, chunk_idx)

            # Kick off prefetch for upcoming chunks
            self._prefetch(path, chunk_idx)

            # Assemble result from cache chunks
            result = bytearray()
            remaining = size
            cur_chunk = chunk_idx
            cur_offset = chunk_offset

            while remaining > 0:
                data = self._cache.get(path, cur_chunk)
                if data is None:
                    self._fetch_chunk(path, cur_chunk)
                    data = self._cache.get(path, cur_chunk) or b''
                chunk_data = data[cur_offset:cur_offset + remaining]
                if not chunk_data:
                    break
                result.extend(chunk_data)
                remaining -= len(chunk_data)
                cur_chunk += 1
                cur_offset = 0

            return bytes(result)
        except Exception:
            try:
                data = self._run(self.afc.get_file_contents(path))
                return data[offset:offset + size]
            except Exception:
                raise FuseOSError(errno.EIO)

    def release(self, path, fh):
        if fh in self._open_files:
            entry = self._open_files[fh]
            # flush() already wrote; release() only writes if flush() was never called
            if entry['writable'] and entry['buf'] is not None and not entry.get('flushed'):
                try:
                    self._run(self._write_chunked(path, bytes(entry['buf'])))
                except Exception:
                    pass
            del self._open_files[fh]
        return 0

    async def _write_chunked(self, path, data):
        WRITE_CHUNK = 1024 * 1024  # 1 MB
        handle = await self.afc.fopen(path, 'w')
        try:
            for i in range(0, len(data), WRITE_CHUNK):
                await self.afc.fwrite(handle, data[i:i + WRITE_CHUNK])
        finally:
            await self.afc.fclose(handle)
        self._cache.invalidate(path)

    def flush(self, path, fh):
        if fh not in self._open_files:
            return 0
        entry = self._open_files[fh]
        if not entry['writable'] or entry['buf'] is None:
            return 0
        try:
            self._run(self._write_chunked(path, bytes(entry['buf'])))
            entry['flushed'] = True
        except Exception:
            raise FuseOSError(errno.EIO)
        return 0

    def create(self, path, mode):
        self._check_writable()
        self._cache.invalidate(path)
        try:
            self._run(self.afc.set_file_contents(path, b''))
        except Exception:
            raise FuseOSError(errno.EIO)
        fh = self._next_fh
        self._next_fh += 1
        self._open_files[fh] = {'path': path, 'buf': bytearray(), 'writable': True}
        return fh

    def write(self, path, data, offset, fh):
        self._check_writable()
        if fh not in self._open_files:
            raise FuseOSError(errno.EBADF)
        entry = self._open_files[fh]
        if entry['buf'] is None:
            try:
                entry['buf'] = bytearray(self._run(self.afc.get_file_contents(path)))
            except Exception:
                entry['buf'] = bytearray()
        buf = entry['buf']
        if offset + len(data) > len(buf):
            buf.extend(b'\x00' * (offset + len(data) - len(buf)))
        buf[offset:offset + len(data)] = data
        self._cache.invalidate(path)
        return len(data)

    def truncate(self, path, length, fh=None):
        self._check_writable()
        self._cache.invalidate(path)
        try:
            data = self._run(self.afc.get_file_contents(path))
        except Exception:
            data = b''
        data = data[:length].ljust(length, b'\x00')
        try:
            self._run(self.afc.set_file_contents(path, data))
        except Exception:
            raise FuseOSError(errno.EIO)

    def mkdir(self, path, mode):
        self._check_writable()
        try:
            self._run(self.afc.makedirs(path))
        except Exception:
            raise FuseOSError(errno.EIO)

    def rmdir(self, path):
        self._check_writable()
        try:
            self._run(self.afc.rm(path, force=True))
        except Exception:
            raise FuseOSError(errno.EIO)

    def unlink(self, path):
        self._check_writable()
        self._cache.invalidate(path)
        try:
            self._run(self.afc.rm(path))
        except Exception:
            raise FuseOSError(errno.EIO)

    def rename(self, old, new):
        self._check_writable()
        self._cache.invalidate(old)
        self._cache.invalidate(new)
        try:
            self._run(self.afc.rename(old, new))
        except Exception:
            raise FuseOSError(errno.EIO)


def main():
    parser = argparse.ArgumentParser(description='Mount iPhone as a filesystem over WiFi')
    parser.add_argument('--host', required=True, help='iPhone IP address')
    parser.add_argument('--mountpoint', default=None, help='Where to mount')
    parser.add_argument('--ro', action='store_true', help='Mount read-only')
    parser.add_argument('--app', default=None, help='Bundle ID for per-app sandbox (e.g. com.apple.Pages)')
    args = parser.parse_args()

    if args.mountpoint is None:
        if args.app:
            appname = args.app.split('.')[-1]
            args.mountpoint = f'/mnt/iPhone-{appname}'
        else:
            args.mountpoint = '/mnt/iPhone'

    os.makedirs(args.mountpoint, exist_ok=True)
    loop = asyncio.new_event_loop()

    async def setup():
        lockdown = await create_using_mobdev2(hostname=args.host)
        log.info(f"Connected! iOS {lockdown.product_version} ({lockdown.udid})")
        if args.app:
            log.info(f"Opening app sandbox: {args.app}")
            house_arrest = HouseArrestService(lockdown, bundle_id=args.app)
            afc = house_arrest.get_afc_service()
        else:
            afc = AfcService(lockdown)
        return afc

    afc = loop.run_until_complete(setup())
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    mode = 'read-only' if args.ro else 'read-write'
    target = f"app:{args.app}" if args.app else 'iPhone'
    log.info(f"Mounting {target} at {args.mountpoint} ({mode})")
    FUSE(IPhoneFS(afc, loop, read_only=args.ro), args.mountpoint, nothreads=True, foreground=True, allow_other=True)

if __name__ == '__main__':
    main()
