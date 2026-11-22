#!/usr/bin/env bash
set -e

echo "=== iphone-linux installer ==="
echo ""

echo "[1/4] Installing system dependencies..."
sudo apt-get update -q
sudo apt-get install -y libfuse2t64 fuse avahi-daemon python3-venv python3-pip

echo "[2/4] Setting up Python environment..."
INSTALL_DIR="$HOME/.local/share/iphone-linux"
mkdir -p "$INSTALL_DIR"
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip -q
"$INSTALL_DIR/venv/bin/pip" install pymobiledevice3 fusepy zeroconf -q

echo "[3/4] Installing scripts..."
cp "$(dirname "$0")/mount_iphone.py" "$INSTALL_DIR/"
cp "$(dirname "$0")/autodiscover.py" "$INSTALL_DIR/"
sudo mkdir -p /mnt/iPhone

echo "[4/4] Creating CLI command..."
mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/iphone-linux" << 'CLIEOF'
#!/usr/bin/env bash
INSTALL_DIR="$HOME/.local/share/iphone-linux"
PYTHON="$INSTALL_DIR/venv/bin/python3"
case "$1" in
    mount)
        sudo "$PYTHON" "$INSTALL_DIR/mount_iphone.py"
        ;;
    unmount|umount)
        sudo umount -l /mnt/iPhone && echo "Unmounted."
        ;;
    watch)
        "$PYTHON" "$INSTALL_DIR/autodiscover.py"
        ;;
    *)
        echo "Usage: iphone-linux [mount|unmount|watch]"
        echo "  mount    — Mount iPhone at /mnt/iPhone"
        echo "  unmount  — Unmount"
        echo "  watch    — Auto-mount when iPhone joins WiFi"
        ;;
esac
CLIEOF
chmod +x "$HOME/.local/bin/iphone-linux"

# Add ~/.local/bin to PATH if not already there
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
    export PATH="$HOME/.local/bin:$PATH"
fi

echo ""
echo "✓ Done! Try it:"
echo "  iphone-linux mount"
echo "  iphone-linux watch"
echo "  iphone-linux unmount"
