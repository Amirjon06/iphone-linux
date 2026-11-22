# iphone-linux

Mount your iPhone as a filesystem on Linux over WiFi — no cable, no iTunes, no iCloud.

## Why

Linux users who want to pull photos off their iPhone have two bad options:

- **USB + ifuse** — broken on modern iOS (17+)
- **iCloud web** — slow, requires internet, can't access all folders

iphone-linux uses the same WiFi protocol Xcode uses for wireless debugging, combined with FUSE, to make your iPhone appear as a normal Linux folder — with full read/write support.

## Install

    git clone https://github.com/Amirjon06/iphone-linux
    cd iphone-linux
    bash install.sh

The installer creates a Python venv, installs all deps, and adds `iphone-linux` to your PATH.

### Allow non-root access

    echo "user_allow_other" | sudo tee -a /etc/fuse.conf

## Usage

    iphone-linux mount [ip]                    # Mount iPhone at /mnt/iPhone
    iphone-linux mount-ro [ip]                 # Mount read-only
    iphone-linux mount-app <ip> <bundle-id>    # Mount a single app's sandbox
    iphone-linux watch [mountpoint]            # Auto-mount; reconnects when phone comes back
    iphone-linux apps [ip] [--user]            # List installed apps and bundle IDs
    iphone-linux unmount                       # Unmount

## Per-app sandbox access

iOS sandboxes every app. `mount-app` uses Apple's HouseArrest protocol to mount exactly one app's sandbox:

    iphone-linux mount-app 192.168.1.10 com.apple.Pages
    ls /mnt/iPhone-Pages/Documents

    iphone-linux apps [ip] [--user]  # find bundle IDs

## Auto-mount (watch mode)

`iphone-linux watch` keeps the mount alive automatically:

- Polls for your iPhone on WiFi using mDNS/Bonjour
- Mounts the moment it's found
- If the phone sleeps or leaves the network, unmounts cleanly
- Waits quietly with exponential backoff (5s → 60s) until the phone returns
- Remounts automatically when the phone wakes up or rejoins WiFi

## Reliability

The filesystem handles connection drops transparently:

- If the phone sleeps mid-operation, the broken connection is detected
- The next filesystem access triggers a reconnect automatically
- If reconnect succeeds, the operation retries — no visible error to the caller

## Auto-mount at login (systemd)

    mkdir -p ~/.config/systemd/user
    cp systemd/iphone-linux.service ~/.config/systemd/user/
    systemctl --user daemon-reload
    systemctl --user enable --now iphone-linux

## First-time setup

1. iPhone: **Settings → Privacy & Security → Developer Mode → Enable**
2. Same WiFi network as your Linux machine
3. Run `iphone-linux mount` — tap **Trust** on your iPhone when prompted
4. Pairing is saved — future mounts need no interaction

## How it works

1. Connects over WiFi via **pymobiledevice3** using the `mobdev2` protocol
2. Discovers your iPhone's IP automatically via mDNS/Bonjour
3. Authenticates with a saved pairing record (tap "Trust" once, never again)
4. Exposes the iPhone filesystem via **AFC** through **FUSE**, or a single app sandbox via **HouseArrest**
5. asyncio event loop runs in a background thread; FUSE callbacks use `run_coroutine_threadsafe()`
6. Read-ahead cache pre-fetches 3 MB ahead in background threads

## Requirements

- Linux (Ubuntu 24.04+ tested)
- Python 3.10+
- iPhone on the same WiFi network
- Developer Mode enabled on iPhone

## Tests

    pytest test_iphone_linux.py -v

13 tests covering mount sanity, read, offset read, JPEG header validation, write, binary write, 1 MB write, overwrite, mkdir, rmdir, unlink, and rename — all run against a real iPhone over WiFi.

## Roadmap

- [x] Read-only mount over WiFi
- [x] Write support (copy files to and from iPhone)
- [x] Auto-mount when iPhone joins WiFi (mDNS/Bonjour)
- [x] One-command installer
- [x] Read-only mode (`--ro` flag)
- [x] Test suite (13 tests, all passing on real hardware)
- [x] `mount-app <ip> <bundle-id>` for per-app sandbox access
- [x] Read-ahead cache for large photo libraries
- [x] Systemd service for auto-mount at boot
- [x] Graceful reconnect on sleep — auto-retries on next access
- [x] Watch mode with exponential backoff — quiet polling, instant remount

## Performance

Read-ahead cache pre-fetches 3 chunks (3 MB) ahead in background threads. Measured on a real iPhone over WiFi:

    First copy (cold):   0.325s   (260 KB JPEG)
    Second copy (warm):  0.199s   39% faster

Sequential reads — large photo libraries, video files — benefit most.

## Stack

pymobiledevice3 · fusepy · libfuse2t64 · zeroconf · Python 3

## License

MIT
