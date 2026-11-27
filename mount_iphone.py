import os, errno, stat, asyncio, logging, threading, argparse, time, sqlite3
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

# Exceptions that mean the TCP connection to the phone is dead
try:
    from pymobiledevice3.exceptions import ConnectionTerminatedError as _ConnTerm
except ImportError:
    _ConnTerm = None

_DEAD_CONN = tuple(filter(None, [
    BrokenPipeError,
    ConnectionResetError,
    ConnectionAbortedError,
    TimeoutError,
    OSError,
    _ConnTerm,
]))

# AFC errors that are NOT dead connections — let them bubble as filesystem errors
try:
    from pymobiledevice3.exceptions import AfcException as _AfcException
except ImportError:
    _AfcException = None


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

    def clear(self):
        with self._lock:
            self._cache.clear()
            self._pending.clear()



METADATA_TTL = 30
NEGATIVE_TTL = 10
DB_PATH = '/tmp/iphone_meta.db'

class MetaCache:
    def __init__(self, db_path=DB_PATH):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute('''CREATE TABLE IF NOT EXISTS meta (
            path TEXT PRIMARY KEY,
            st_mode INTEGER,
            st_nlink INTEGER,
            st_size INTEGER,
            negative INTEGER DEFAULT 0,
            expires REAL
        )''')
        self._conn.execute('''CREATE TABLE IF NOT EXISTS dirents (
            parent TEXT,
            name TEXT,
            expires REAL,
            PRIMARY KEY (parent, name)
        )''')
        self._conn.commit()

    def get_stat(self, path):
        with self._lock:
            row = self._conn.execute(
                'SELECT st_mode,st_nlink,st_size,negative,expires FROM meta WHERE path=?',
                (path,)
            ).fetchone()
        if not row:
            return None, None
        st_mode, st_nlink, st_size, negative, expires = row
        if time.time() > expires:
            self.invalidate(path)
            return None, None
        if negative:
            return None, True
        return dict(st_mode=st_mode, st_nlink=st_nlink, st_size=st_size), False

    def put_stat(self, path, stat_dict):
        expires = time.time() + METADATA_TTL
        with self._lock:
            self._conn.execute('''INSERT OR REPLACE INTO meta
                (path,st_mode,st_nlink,st_size,negative,expires)
                VALUES (?,?,?,?,0,?)''',
                (path, stat_dict['st_mode'], stat_dict.get('st_nlink',1),
                 stat_dict.get('st_size',0), expires))
            self._conn.commit()

    def put_negative(self, path):
        expires = time.time() + NEGATIVE_TTL
        with self._lock:
            self._conn.execute('''INSERT OR REPLACE INTO meta
                (path,st_mode,st_nlink,st_size,negative,expires)
                VALUES (?,0,0,0,1,?)''', (path, expires))
            self._conn.commit()

    def get_dir(self, path):
        with self._lock:
            rows = self._conn.execute(
                'SELECT name,expires FROM dirents WHERE parent=?', (path,)
            ).fetchall()
        if not rows:
            return None
        now = time.time()
        if any(r[1] < now for r in rows):
            self.invalidate_dir(path)
            return None
        return [r[0] for r in rows]

    def put_dir(self, path, entries):
        expires = time.time() + METADATA_TTL
        with self._lock:
            self._conn.execute('DELETE FROM dirents WHERE parent=?', (path,))
            self._conn.executemany(
                'INSERT INTO dirents (parent,name,expires) VALUES (?,?,?)',
                [(path, e, expires) for e in entries]
            )
            self._conn.commit()

    def invalidate(self, path):
        with self._lock:
            self._conn.execute('DELETE FROM meta WHERE path=?', (path,))
            self._conn.commit()

    def invalidate_dir(self, path):
        with self._lock:
            self._conn.execute('DELETE FROM dirents WHERE parent=?', (path,))
            self._conn.commit()

    def invalidate_all(self):
        with self._lock:
            self._conn.execute('DELETE FROM meta')
            self._conn.execute('DELETE FROM dirents')
            self._conn.commit()


RESUME_DIR = '/tmp/iphone_resume'

def _resume_key(path):
    import hashlib
    return hashlib.md5(path.encode()).hexdigest()

def _resume_path(path):
    os.makedirs(RESUME_DIR, exist_ok=True)
    return os.path.join(RESUME_DIR, _resume_key(path) + '.bin')

def _resume_save(path, buf):
    try:
        rp = _resume_path(path)
        with open(rp, 'wb') as f:
            f.write(bytes(buf))
    except Exception:
        pass

def _resume_load(path):
    try:
        rp = _resume_path(path)
        if os.path.exists(rp):
            with open(rp, 'rb') as f:
                data = f.read()
            logging.getLogger("iphone-fs").info(f"[resume] resuming {path} from {len(data)} bytes")
            return bytearray(data)
    except Exception:
        pass
    return None

def _resume_clear(path):
    try:
        rp = _resume_path(path)
        if os.path.exists(rp):
            os.remove(rp)
    except Exception:
        pass

class IPhoneFS(Operations):
    def __init__(self, afc, loop, host, app=None, read_only=False):
        self.afc = afc
        self.loop = loop
        self._host = host
        self._app = app
        self.read_only = read_only
        self._open_files = {}
        self._next_fh = 1
        self._cache = ReadAheadCache()
        self._meta = MetaCache()
        self._reconnect_lock = threading.Lock()
        self._reconnecting = False
        log.info(f"IPhoneFS initialized (read_only={read_only})")

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _reconnect(self):
        """Re-establish the AFC connection after the phone woke up."""
        with self._reconnect_lock:
            if self._reconnecting:
                return False
            self._reconnecting = True

        try:
            log.info("iPhone connection lost — reconnecting...")
            self._cache.clear()
            self._meta.invalidate_all()

            async def _setup():
                lockdown = await create_using_mobdev2(hostname=self._host)
                log.info(f"Reconnected! iOS {lockdown.product_version}")
                # iOS 26 drops heartbeat repeatedly — force restart it after each reconnect
                try:
                    await lockdown._ensure_heartbeat()
                except Exception:
                    pass
                if self._app:
                    house_arrest = HouseArrestService(lockdown, bundle_id=self._app)
                    return house_arrest.get_afc_service()
                return AfcService(lockdown)

            future = asyncio.run_coroutine_threadsafe(_setup(), self.loop)
            self.afc = future.result(timeout=30)
            log.info("Reconnect successful.")
            return True
        except Exception as e:
            log.warning(f"Reconnect failed: {e}")
            return False
        finally:
            with self._reconnect_lock:
                self._reconnecting = False

    def _run(self, coro):
        """Run a coroutine on the event loop, auto-reconnecting on dead connection."""
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            return future.result(timeout=30)
        except Exception as exc:
            # AFC errors (AfcFileNotFoundError etc) inherit OSError — check first
            # so they never get treated as dead connections
            if _AfcException and isinstance(exc, _AfcException):
                raise FuseOSError(errno.EIO)
            if isinstance(exc, _DEAD_CONN):
                log.warning(f"Connection error ({type(exc).__name__}), attempting reconnect...")
                for attempt in range(10):
                    import time
                    time.sleep(2)
                    if self._reconnect():
                        raise FuseOSError(errno.ESTALE)
                raise FuseOSError(errno.EIO)
            raise

    def _check_writable(self):
        if self.read_only:
            raise FuseOSError(errno.EROFS)

    # ------------------------------------------------------------------
    # Read-ahead cache
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # FUSE operations
    # ------------------------------------------------------------------

    def _stat(self, path):
        info = self._run(self.afc.stat(path))
        if info['st_ifmt'] == 'S_IFDIR':
            return dict(st_mode=(stat.S_IFDIR | 0o755), st_nlink=int(info.get('st_nlink', 2)))
        return dict(st_mode=(stat.S_IFREG | 0o644), st_nlink=1, st_size=int(info.get('st_size', 0)))

    def getattr(self, path, fh=None):
        cached, negative = self._meta.get_stat(path)
        if negative:
            raise FuseOSError(errno.ENOENT)
        if cached is not None:
            return cached
        for attempt in range(2):
            try:
                result = self._stat(path)
                self._meta.put_stat(path, result)
                return result
            except FuseOSError as e:
                if e.errno == errno.ESTALE and attempt == 0:
                    continue
                self._meta.put_negative(path)
                raise FuseOSError(errno.ENOENT)
            except Exception:
                self._meta.put_negative(path)
                raise FuseOSError(errno.ENOENT)

    def readdir(self, path, fh):
        cached = self._meta.get_dir(path)
        if cached is not None:
            return ['.', '..'] + cached
        for attempt in range(2):
            try:
                entries = self._run(self.afc.listdir(path))
                self._meta.put_dir(path, entries)
                return ['.', '..'] + entries
            except FuseOSError as e:
                if e.errno == errno.ESTALE and attempt == 0:
                    continue
                raise FuseOSError(errno.ENOENT)
            except Exception:
                raise FuseOSError(errno.ENOENT)

    def open(self, path, flags):
        import os as _os
        writable = bool(flags & (_os.O_WRONLY | _os.O_RDWR))
        if writable and self.read_only:
            raise FuseOSError(errno.EROFS)
        # If any entry for this path was recently flushed, invalidate cache so
        # the next read goes to the phone, not stale prefetch data
        for entry in self._open_files.values():
            if entry.get('path') == path and entry.get('flushed'):
                self._cache.invalidate(path)
                self._meta.invalidate(path)
                break
        # Probe connection; if dead, reconnect before returning handle
        for attempt in range(2):
            try:
                self._run(self.afc.stat(path))
                break
            except FuseOSError as e:
                if e.errno == errno.ESTALE and attempt == 0:
                    continue
                raise FuseOSError(errno.EIO)
            except Exception:
                raise FuseOSError(errno.EIO)
        fh = self._next_fh
        self._next_fh += 1
        self._open_files[fh] = {'path': path, 'buf': None, 'writable': writable}
        return fh

    def read(self, path, size, offset, fh):
        try:
            chunk_idx = offset // CHUNK_SIZE
            chunk_offset = offset % CHUNK_SIZE

            if self._cache.get(path, chunk_idx) is None:
                self._fetch_chunk(path, chunk_idx)

            self._prefetch(path, chunk_idx)

            result = bytearray()
            remaining = size
            cur_chunk = chunk_idx
            cur_offset = chunk_offset

            while remaining > 0:
                data = self._cache.get(path, cur_chunk)
                if data is None:
                    self._fetch_chunk(path, cur_chunk)
                    data = self._cache.get(path, cur_chunk)
                    if data is None:
                        raise IOError('chunk fetch failed')
                    data = data or b''
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
                    _resume_clear(path)
                except Exception:
                    pass
            elif entry.get('flushed'):
                _resume_clear(path)
            if entry.get('writable'):
                self._cache.invalidate(path)
                self._meta.invalidate(path)
            del self._open_files[fh]
        return 0

    async def _write_chunked(self, path, data):
        MAX_RETRIES = 10
        RETRY_DELAY = 3   # seconds between retries
        CHUNK = 512 * 1024  # 512 KB chunks

        attempt = 0
        while attempt < MAX_RETRIES:
            bytes_written = 0
            handle = None
            try:
                handle = await self.afc.fopen(path, 'w')
                while bytes_written < len(data):
                    chunk = data[bytes_written:bytes_written + CHUNK]
                    await self.afc.fwrite(handle, chunk)
                    bytes_written += len(chunk)
                    _resume_save(path, data[:bytes_written])
                await self.afc.fclose(handle)
                handle = None
                _resume_clear(path)
                self._cache.invalidate(path)
                return
            except Exception as e:
                if handle is not None:
                    try:
                        await self.afc.fclose(handle)
                    except Exception:
                        pass
                _resume_save(path, data[:bytes_written])
                logging.getLogger('iphonefs').warning(
                    f'[resume] write interrupted at {bytes_written}/{len(data)} bytes, ' +
                    f'retrying in {RETRY_DELAY}s (attempt {attempt+1}/{MAX_RETRIES}): {e}'
                )
                attempt += 1
                await asyncio.sleep(RETRY_DELAY)

        raise FuseOSError(errno.EIO)

    def flush(self, path, fh):
        if fh not in self._open_files:
            return 0
        entry = self._open_files[fh]
        if not entry['writable'] or entry['buf'] is None:
            return 0
        if entry.get('flushed'):
            return 0
        try:
            data = bytes(entry['buf'])
            _resume_save(path, data)
            log.info(f"[flush] writing {len(data)} bytes to {path}")
            future = asyncio.run_coroutine_threadsafe(
                self._write_chunked(path, data), self.loop)
            future.result(timeout=300)
            _resume_clear(path)
            entry['flushed'] = True
            self._cache.clear()
            self._meta.invalidate(path)
            log.info(f"[flush] done writing {path}")
        except Exception as e:
            log.error(f"[flush] FAILED: {e}")
            raise FuseOSError(errno.EIO)
        return 0

    def create(self, path, mode):
        self._check_writable()
        self._cache.invalidate(path)
        self._meta.invalidate(path)
        resume_buf = _resume_load(path)
        if resume_buf is None:
            # No resume — start fresh
            try:
                self._run(self.afc.set_file_contents(path, b''))
            except Exception:
                raise FuseOSError(errno.EIO)
        else:
            log.info(f"[resume] picking up {path} from {len(resume_buf)//1024}KB")
        fh = self._next_fh
        self._next_fh += 1
        self._open_files[fh] = {'path': path, 'buf': resume_buf if resume_buf is not None else bytearray(), 'writable': True}
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
        _resume_save(path, buf)
        return len(data)

    def truncate(self, path, length, fh=None):
        self._check_writable()
        self._cache.invalidate(path)
        # If fh given and open, truncate buffer directly
        if fh is not None and fh in self._open_files:
            entry = self._open_files[fh]
            if entry.get('writable') and entry['buf'] is not None:
                buf = entry['buf']
                if length > len(buf):
                    buf.extend(b'\x00' * (length - len(buf)))
                else:
                    del buf[length:]
                return
        # Also check by path — cp calls truncate(fh=None) while file is open
        for _fh, entry in self._open_files.items():
            if entry.get('path') == path and entry.get('writable') and entry['buf'] is not None:
                buf = entry['buf']
                if length > len(buf):
                    buf.extend(b'\x00' * (length - len(buf)))
                else:
                    del buf[length:]
                return
        # Only hit the phone if the file is NOT open in memory
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
        self._meta.invalidate(path)
        try:
            self._run(self.afc.rm(path))
        except Exception:
            raise FuseOSError(errno.EIO)

    def rename(self, old, new):
        self._check_writable()
        self._cache.invalidate(old)
        self._cache.invalidate(new)
        self._meta.invalidate(old)
        self._meta.invalidate(new)
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
        # iOS 26 drops heartbeat immediately after connect — force restart it
        try:
            await lockdown._ensure_heartbeat()
        except Exception:
            pass
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
    FUSE(IPhoneFS(afc, loop, host=args.host, app=args.app, read_only=args.ro),
         args.mountpoint, nothreads=True, foreground=True, allow_other=True)


if __name__ == '__main__':
    main()
