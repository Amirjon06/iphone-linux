import os, errno, stat, asyncio, logging, threading, argparse
from fuse import FUSE, FuseOSError, Operations
from pymobiledevice3.lockdown import create_using_mobdev2
from pymobiledevice3.services.afc import AfcService

logging.basicConfig(level=logging.INFO)
log = logging.getLogger('iphonefs')

class IPhoneFS(Operations):
    def __init__(self, afc, loop, read_only=False):
        self.afc = afc
        self.loop = loop
        self.read_only = read_only
        self._open_files = {}
        self._next_fh = 1
        log.info(f"IPhoneFS initialized (read_only={read_only})")

    def _run(self, coro):
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return future.result(timeout=30)

    def _check_writable(self):
        if self.read_only:
            raise FuseOSError(errno.EROFS)

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
            async def _read_chunk():
                async with self.afc.open(path, mode='r') as afc_file:
                    await afc_file.seek(offset)
                    return await afc_file.read(size)
            future = asyncio.run_coroutine_threadsafe(_read_chunk(), self.loop)
            return future.result(timeout=30)
        except Exception:
            try:
                data = self._run(self.afc.get_file_contents(path))
                return data[offset:offset + size]
            except Exception:
                raise FuseOSError(errno.EIO)

    def release(self, path, fh):
        if fh in self._open_files:
            entry = self._open_files[fh]
            if entry['writable'] and entry['buf'] is not None:
                try:
                    self._run(self.afc.set_file_contents(path, bytes(entry['buf'])))
                except Exception:
                    pass
            del self._open_files[fh]
        return 0

    def flush(self, path, fh):
        if fh not in self._open_files:
            return 0
        entry = self._open_files[fh]
        if not entry['writable'] or entry['buf'] is None:
            return 0
        try:
            self._run(self.afc.set_file_contents(path, bytes(entry['buf'])))
        except Exception:
            raise FuseOSError(errno.EIO)
        return 0

    def create(self, path, mode):
        self._check_writable()
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
        return len(data)

    def truncate(self, path, length, fh=None):
        self._check_writable()
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
        try:
            self._run(self.afc.rm(path))
        except Exception:
            raise FuseOSError(errno.EIO)

    def rename(self, old, new):
        self._check_writable()
        try:
            self._run(self.afc.rename(old, new))
        except Exception:
            raise FuseOSError(errno.EIO)

def main():
    parser = argparse.ArgumentParser(description='Mount iPhone as a filesystem over WiFi')
    parser.add_argument('--host', required=True, help='iPhone IP address')
    parser.add_argument('--mountpoint', default='/mnt/iPhone', help='Where to mount')
    parser.add_argument('--ro', action='store_true', help='Mount read-only')
    args = parser.parse_args()

    os.makedirs(args.mountpoint, exist_ok=True)
    loop = asyncio.new_event_loop()

    async def setup():
        lockdown = await create_using_mobdev2(hostname=args.host)
        log.info(f"Connected! iOS {lockdown.product_version} ({lockdown.udid})")
        afc = AfcService(lockdown)
        return afc

    afc = loop.run_until_complete(setup())
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    log.info(f"Mounting at {args.mountpoint} ({'read-only' if args.ro else 'read-write'})")
    FUSE(IPhoneFS(afc, loop, read_only=args.ro), args.mountpoint, nothreads=True, foreground=True, allow_other=True)

if __name__ == '__main__':
    main()
