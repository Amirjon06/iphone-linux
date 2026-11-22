Mount your iPhone as a filesystem on Linux over WiFi — no cable, no iTunes, no iCloud.

```bash
$ ls /mnt/iPhone
Books  DCIM  Downloads  Music  PhotoData  Recordings  iTunes_Control

$ cp /mnt/iPhone/DCIM/100APPLE/IMG_0048.JPEG ~/photos/

$ echo "hello from linux" > /mnt/iPhone/Downloads/note.txt

$ iphone-linux mount-app com.apple.Pages
$ ls /mnt/iPhone-Pages
Documents  Library  tmp
```

## Why

Linux users who want to pull photos off their iPhone have two bad options:

- **USB + ifuse** — broken on modern iOS (17+)
- **iCloud web** — slow, requires internet, can't access all folders

iphone-linux uses the same WiFi protocol Xcode uses for wireless debugging, combined with FUSE, to make your iPhone appear as a normal Linux folder — with full read/write support.

## Install

```bash
git clone https://github.com/Amirjon06/iphone-linux
cd iphone-linux
bash install.sh
```

The installer creates a Python venv, installs all deps, and adds `iphone-linux` to your PATH.

### Allow non-root access

By default FUSE mounts are root-only. To let your regular user access the mount:

```bash
echo "user_allow_other" | sudo tee -a /etc/fuse.conf
```

This is a one-time setup.

## Usage

```bash
iphone-linux mount [ip]              # Mount iPhone at /mnt/iPhone (auto-discovers IP)
iphone-linux mount-ro                # Mount read-only
iphone-linux mount-app <bundle-id>   # Mount a single app's sandbox
iphone-linux watch                   # Auto-mount when iPhone joins WiFi
iphone-linux unmount                 # Unmount
```

## Per-app sandbox access

iOS sandboxes every app — each one gets its own private Documents folder with no access to other apps. `mount-app` uses Apple's HouseArrest protocol to mount exactly one app's sandbox, so you can read and write its files directly:

```bash
iphone-linux mount-app com.apple.Pages
ls /mnt/iPhone-Pages/Documents    # Pages documents
cp ~/report.pages /mnt/iPhone-Pages/Documents/

iphone-linux mount-app com.hammerandchisel.discord
ls /mnt/iPhone-Discord/Library/Caches
```

No other Linux tool exposes this. You need the app's bundle ID — find it by searching "bundle id" + the app name, or browsing [VP Browser](https://github.com/nickreoutsos/vpbrowser).

## First-time setup

1. iPhone: **Settings → Privacy & Security → Developer Mode → Enable**
2. Same WiFi network as your Linux machine
3. Run `iphone-linux mount` — tap **Trust** on your iPhone when prompted
4. Pairing is saved — future mounts need no interaction

## How it works

1. Connects over WiFi via **pymobiledevice3** using the `mobdev2` protocol (same as Xcode wireless debugging)
2. Discovers your iPhone's IP automatically via mDNS/Bonjour (`_apple-mobdev2._tcp.local.`)
3. Authenticates with a saved pairing record (tap "Trust" once, never again)
4. Exposes the iPhone filesystem via **AFC** through **FUSE**, or a single app sandbox via **HouseArrest**
5. asyncio event loop runs in a background thread; FUSE sync callbacks use `run_coroutine_threadsafe()` to submit AFC requests
6. Reads use AFC file handles with seek() for efficient offset-based I/O — no full-file downloads

## Requirements

- Linux (Ubuntu 24.04+ tested)
- Python 3.10+
- iPhone on the same WiFi network
- Developer Mode enabled on iPhone

## Tests

```bash
# Mount first, then:
pytest test_iphone_linux.py -v
```

13 tests covering mount sanity, read, offset read, JPEG header validation, write, binary write, 1 MB write, overwrite, mkdir, rmdir, unlink, and rename — all run against a real iPhone over WiFi.

## Roadmap

- [x] Read-only mount over WiFi
- [x] Write support (copy files to and from iPhone)
- [x] Auto-mount when iPhone joins WiFi (mDNS/Bonjour)
- [x] One-command installer
- [x] Read-only mode (`--ro` flag)
- [x] Test suite (13 tests, all passing on real hardware)
- [x] `--app <bundle-id>` flag for per-app sandbox access
- [ ] Read-ahead cache for large photo libraries
- [x] Systemd service for auto-mount at boot

## Stack

pymobiledevice3 · fusepy · libfuse2t64 · zeroconf · Python 3

## License

MIT

## Auto-mount at login (systemd)

Install the service so your iPhone mounts automatically whenever you log in and it's on the same WiFi:

```bash
mkdir -p ~/.config/systemd/user
cp systemd/iphone-linux.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now iphone-linux
```

Check status:

```bash
systemctl --user status iphone-linux
journalctl --user -u iphone-linux -f
```

