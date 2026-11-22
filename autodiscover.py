#!/usr/bin/env python3
import os, subprocess, logging, time, socket
from zeroconf import ServiceBrowser, Zeroconf, ServiceListener, ServiceInfo

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
log = logging.getLogger('autodiscover')

INSTALL_DIR = os.path.expanduser('~/.local/share/iphone-linux')
MOUNT_SCRIPT = os.path.join(INSTALL_DIR, 'mount_iphone.py')
PYTHON = os.path.join(INSTALL_DIR, 'venv/bin/python3')
MOUNTPOINT = '/mnt/iPhone'
_mount_proc = None

def is_mounted():
    return subprocess.run(['mountpoint', '-q', MOUNTPOINT], capture_output=True).returncode == 0

def get_ip(zc, type_, name):
    info = ServiceInfo(type_, name)
    if not info.load_from_cache(zc):
        info.request(zc, 3000)
    for addr in info.parsed_addresses():
        try:
            socket.inet_pton(socket.AF_INET, addr)
            return addr
        except OSError:
            continue
    return None

def mount(ip):
    global _mount_proc
    if is_mounted():
        log.info("Already mounted")
        return
    if not ip:
        log.warning("No IP found, cannot mount")
        return
    log.info(f"iPhone at {ip} — mounting...")
    _mount_proc = subprocess.Popen(['sudo', PYTHON, MOUNT_SCRIPT, '--host', ip])

def unmount():
    global _mount_proc
    if not is_mounted():
        return
    log.info("iPhone left — unmounting...")
    subprocess.run(['sudo', 'umount', '-l', MOUNTPOINT], capture_output=True)
    if _mount_proc:
        _mount_proc.terminate()
        _mount_proc = None

class iPhoneListener(ServiceListener):
    def __init__(self, zc):
        self.zc = zc
    def add_service(self, zc, type_, name):
        log.info(f"Found: {name}")
        mount(get_ip(zc, type_, name))
    def remove_service(self, zc, type_, name):
        log.info(f"Lost: {name}")
        unmount()
    def update_service(self, zc, type_, name):
        pass

def main():
    log.info("Watching for iPhone on WiFi...")
    zc = Zeroconf()
    listener = iPhoneListener(zc)
    ServiceBrowser(zc, "_apple-mobdev2._tcp.local.", listener)
    ServiceBrowser(zc, "_remotepairing._tcp.local.", listener)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Stopping.")
    finally:
        zc.close()

if __name__ == '__main__':
    main()
