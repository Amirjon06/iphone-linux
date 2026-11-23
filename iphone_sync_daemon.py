#!/usr/bin/env python3
"""
iphone-sync — auto-sync daemon
Watches for iPhone on WiFi via mDNS, mounts it, copies new photos to ~/Pictures/iPhone/
"""

import os
import sys
import time
import shutil
import logging
import asyncio
import threading
import subprocess
import hashlib
from pathlib import Path
from datetime import datetime

DEST_DIR        = Path.home() / "Pictures" / "iPhone"
MOUNT_POINT     = Path("/mnt/iPhone")
DCIM_PATH       = MOUNT_POINT / "DCIM"
POLL_INTERVAL   = 30
SYNC_INTERVAL   = 300
LOG_PATH        = Path.home() / ".local" / "share" / "iphone-linux" / "sync.log"
STATE_DB        = Path.home() / ".local" / "share" / "iphone-linux" / "synced.txt"

LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("iphone-sync")

def load_synced():
    if not STATE_DB.exists(): return set()
    return set(STATE_DB.read_text().splitlines())

def save_synced(synced):
    STATE_DB.parent.mkdir(parents=True, exist_ok=True)
    STATE_DB.write_text("\n".join(sorted(synced)))

def iphone_on_wifi():
    try:
        result = subprocess.run(
            ["python3", "-c",
             "from pymobiledevice3.remote.utils import get_device_list;"
             "devs = get_device_list(); print(len(devs))"],
            capture_output=True, text=True, timeout=10)
        count = int(result.stdout.strip()) if result.stdout.strip().isdigit() else 0
        return count > 0
    except Exception as e:
        log.debug(f"mDNS check error: {e}")
        return False

def is_mounted():
    return subprocess.run(["mountpoint", "-q", str(MOUNT_POINT)], capture_output=True).returncode == 0

def mount_iphone():
    MOUNT_POINT.mkdir(parents=True, exist_ok=True)
    if is_mounted(): return True
    log.info("Mounting iPhone...")
    try:
        result = subprocess.run(
            ["python3", "-m", "iphone_linux.mount", str(MOUNT_POINT)],
            capture_output=True, text=True, timeout=30)
        if result.returncode == 0 or is_mounted():
            log.info("Mount OK"); return True
        log.warning(f"Mount failed: {result.stderr.strip()}"); return False
    except Exception as e:
        log.warning(f"Mount error: {e}"); return False

def unmount_iphone():
    if not is_mounted(): return
    log.info("Unmounting iPhone...")
    subprocess.run(["fusermount", "-u", str(MOUNT_POINT)], capture_output=True)

IMAGE_EXTS = {".jpg", ".jpeg", ".heic", ".png", ".mov", ".mp4", ".dng", ".cr2"}

def iter_dcim_files():
    if not DCIM_PATH.exists(): return
    for root, dirs, files in os.walk(DCIM_PATH):
        dirs.sort()
        for f in sorted(files):
            if Path(f).suffix.lower() in IMAGE_EXTS:
                yield Path(root) / f

def dest_path_for(src):
    try:
        month = datetime.fromtimestamp(src.stat().st_mtime).strftime("%Y-%m")
    except Exception:
        month = "unknown"
    return DEST_DIR / month / src.name

def sync_photos():
    synced = load_synced(); copied = 0
    for src in iter_dcim_files():
        key = src.name
        if key in synced: continue
        dst = dest_path_for(src)
        if dst.exists(): synced.add(key); continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            log.info(f"  copying {src.name} → {dst.parent.name}/")
            shutil.copy2(str(src), str(dst))
            synced.add(key); copied += 1
        except Exception as e:
            log.warning(f"  failed {src.name}: {e}")
    save_synced(synced)
    return copied

def run():
    log.info("iphone-sync daemon started")
    DEST_DIR.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            if not iphone_on_wifi():
                if is_mounted():
                    log.info("iPhone left WiFi — unmounting"); unmount_iphone()
                time.sleep(POLL_INTERVAL); continue
            if not is_mounted():
                if not mount_iphone(): time.sleep(POLL_INTERVAL); continue
                time.sleep(3)
            log.info("Syncing photos...")
            n = sync_photos()
            log.info(f"Sync done — {n} new file(s) copied" if n else "Sync done — nothing new")
            time.sleep(SYNC_INTERVAL)
        except KeyboardInterrupt:
            log.info("Shutting down"); unmount_iphone(); break
        except Exception as e:
            log.error(f"Error: {e}", exc_info=True); time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    run()
