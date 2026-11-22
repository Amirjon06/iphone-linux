![demo](demo.gif)

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
