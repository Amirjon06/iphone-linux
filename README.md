# iphone-linux

Mount your iPhone as a filesystem on Linux over WiFi — no cable, no iTunes, no iCloud.

```bash
$ ls /mnt/iPhone
Books  DCIM  Downloads  Music  PhotoData  Recordings  iTunes_Control

$ cp /mnt/iPhone/DCIM/100APPLE/IMG_0048.JPEG ~/photos/

$ echo "hello from linux" > /mnt/iPhone/Downloads/note.txt

$ iphone-linux mount-app 192.168.1.10 com.apple.Pages
$ ls /mnt/iPhone-Pages
Documents  Library  tmp
```

## Who This Is For

This project is for Linux users who want to:

- browse and copy photos off their iPhone without a cable
- read and write files in any app's Documents folder
- use their iPhone storage from scripts, editors, or the shell
- avoid iCloud, iTunes, or any Apple account dependency

If you just want to grab photos quickly, run `iphone-linux mount` and copy from `/mnt/iPhone/DCIM`.

## Why

Linux users who want to pull files off their iPhone have two bad options:

- **USB + ifuse** — broken on modern iOS (17+)
- **iCloud web** — slow, requires internet, only exposes some folders

iphone-linux uses the same WiFi protocol Xcode uses for wireless debugging, combined with FUSE, to make your iPhone appear as a normal Linux folder — with full read/write support, no account needed.

## Requirements

- Linux (Ubuntu 24.04+ tested)
- Python 3.10+
- iPhone with Developer Mode enabled (free, no Apple Developer account needed)
- Same WiFi network as your Linux machine

## Install

```bash
git clone https://github.com/Amirjon06/iphone-linux
cd iphone-linux
bash install.sh
```

The installer creates a Python venv, installs all dependencies, and adds `iphone-linux` to your PATH.

### Allow non-root access

By default FUSE mounts are root-only. To let your regular user access the mount:

```bash
echo "user_allow_other" | sudo tee -a /etc/fuse.conf
```

This is a one-time setup.

## First-Time Setup

1. On your iPhone: **Settings → Privacy & Security → Developer Mode → Enable**
2. Connect your iPhone and Linux machine to the same WiFi network
3. Run `iphone-linux mount` — tap **Trust** on your iPhone when prompted
4. Pairing is saved permanently — future mounts need no interaction

## Commands

```bash
iphone-linux mount [ip]                    # Mount iPhone at /mnt/iPhone
iphone-linux mount-ro [ip]                 # Mount read-only
iphone-linux mount-app <ip> <bundle-id>    # Mount a single app's sandbox
iphone-linux watch [mountpoint]            # Auto-mount; reconnects when phone comes back
iphone-linux apps [ip] [--user]            # List installed apps and bundle IDs
iphone-linux unmount                       # Unmount /mnt/iPhone
```

What they do:

- `mount` — discovers your iPhone on WiFi (or use the IP directly) and mounts it at `/mnt/iPhone`
- `mount-ro` — same as mount but read-only; writes are rejected at the filesystem level
- `mount-app` — mounts a single app's private sandbox using Apple's HouseArrest protocol
- `watch` — polls for your iPhone, mounts when found, remounts when it comes back after sleep
- `apps` — lists all installed apps and their bundle IDs; use `--user` to skip system apps
- `unmount` — unmounts and cleans up

## Per-App Sandbox Access

iOS sandboxes every app — each one has a private Documents folder with no access to other apps. `mount-app` uses Apple's HouseArrest protocol to mount exactly one app's sandbox, so you can read and write its files directly:

```bash
iphone-linux mount-app 192.168.1.10 com.apple.Pages
ls /mnt/iPhone-Pages/Documents
cp ~/report.pages /mnt/iPhone-Pages/Documents/

iphone-linux mount-app 192.168.1.10 com.hammerandchisel.discord
ls /mnt/iPhone-Discord/Library/Caches
```

To find the bundle ID for an app:

```bash
iphone-linux apps 192.168.1.10 --user
```

## Auto-Mount (Watch Mode)

`iphone-linux watch` keeps the mount alive automatically:

- Polls for your iPhone on WiFi using mDNS/Bonjour
- Mounts the moment it is found
- If the phone sleeps or leaves the network, unmounts cleanly
- Waits quietly with exponential backoff (5s → 60s) until the phone returns
- Remounts automatically when the phone wakes up or rejoins WiFi

```bash
iphone-linux watch           # mounts at /mnt/iPhone
iphone-linux watch /mnt/ios  # custom mountpoint
```

Run it in a terminal or install it as a systemd service (see below) to get auto-mount at login.

## Reliability

The filesystem handles connection drops transparently:

- If the phone sleeps mid-operation, the broken connection is detected automatically
- The next filesystem access (ls, cp, open) triggers a reconnect
- If reconnect succeeds, the operation retries — no visible error to the caller
- If the phone is off the network, operations return a temporary error until it's back

This means you can lock your phone, walk away, come back, unlock it, and the mount just works again.

## Auto-Mount at Login (systemd)

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

## Troubleshooting

### No iPhone found

- Make sure Developer Mode is enabled: **Settings → Privacy & Security → Developer Mode**
- Both devices must be on the same WiFi network
- Try specifying the IP directly: `iphone-linux mount 192.168.1.10`
- On some routers, mDNS is blocked between devices — use the IP

### Trust dialog never appeared

Unplug any USB cable, then run mount again. The Trust dialog only appears on the first connection. If it was dismissed, it won't reappear until you reset location and privacy: **Settings → Transfer or Reset iPhone → Reset → Reset Location & Privacy**.

### Mount works but files are missing or empty

This usually means the connection dropped mid-operation. Unmount and remount:

```bash
iphone-linux unmount
iphone-linux mount
```

### Stale mount after reboot

If the mount point shows as busy or stale:

```bash
sudo umount -l /mnt/iPhone
iphone-linux mount
```

### Permission denied writing files

Make sure `/etc/fuse.conf` contains `user_allow_other` and you are not using `mount-ro`.

## How It Works

1. Connects over WiFi via **pymobiledevice3** using the `mobdev2` protocol (the same protocol Xcode uses for wireless debugging)
2. Discovers your iPhone's IP automatically via mDNS/Bonjour (`_apple-mobdev2._tcp.local.`)
3. Authenticates with a saved pairing record (tap "Trust" once, never again)
4. Exposes the iPhone filesystem via **AFC** through **FUSE**, or a single app sandbox via **HouseArrest**
5. asyncio event loop runs in a background thread; FUSE sync callbacks use `run_coroutine_threadsafe()` to submit AFC requests
6. Read-ahead cache pre-fetches 3 MB ahead in background threads for fast sequential reads

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
- [x] `mount-app <ip> <bundle-id>` for per-app sandbox access
- [x] Read-ahead cache for large photo libraries
- [x] Systemd service for auto-mount at boot
- [x] Graceful reconnect on sleep — auto-retries on next access
- [x] Watch mode with exponential backoff — quiet polling, instant remount

## Performance

Read-ahead cache pre-fetches 3 chunks (3 MB) ahead in background threads.

| File | Cold (first access) | Warm (cached) | Speedup |
|------|--------------------|-----------------|---------| 
| 260 KB JPEG | 0.94 s | 0.039 s | **24×** |

Tested over 802.11ac WiFi, iOS 26.6.2, pymobiledevice3 11.63.0. Sequential reads — large photo libraries, video files — benefit most. Random reads and small files see less gain.

## Stack

pymobiledevice3 · fusepy · libfuse2t64 · zeroconf · Python 3

## License

MIT
