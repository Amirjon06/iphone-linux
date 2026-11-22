import asyncio
import logging
from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.services.afc import AfcService

log = logging.getLogger('iphonefs')

class IPhoneConnection:
    def __init__(self):
        self.lockdown = None
        self.afc = None

    async def connect(self):
        try:
            self.lockdown = await create_using_usbmux()
            log.info(f"Connected to {self.lockdown.name} running iOS {self.lockdown.product_version}")
            self.afc = AfcService(self.lockdown)
            return True
        except Exception as e:
            log.error(f"Failed to connect: {e}")
            return False

    def list_dir(self, path='/'):
        if not self.afc:
            return []
        try:
            return self.afc.listdir(path)
        except Exception as e:
            log.error(f"listdir {path} failed: {e}")
            return []

    def read_file(self, path, size, offset):
        if not self.afc:
            return b''
        try:
            with self.afc.open(path, 'r') as f:
                f.seek(offset)
                return f.read(size)
        except Exception as e:
            log.error(f"read {path} failed: {e}")
            return b''

    def get_file_info(self, path):
        if not self.afc:
            return None
        try:
            return self.afc.stat(path)
        except Exception:
            return None

async def test():
    conn = IPhoneConnection()
    if await conn.connect():
        print("Connected! Files in /DCIM:")
        print(conn.list_dir('/DCIM'))
    else:
        print("Not connected - iPhone not found")

if __name__ == '__main__':
    asyncio.run(test())
