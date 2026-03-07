#!/usr/bin/env bash

# SignalPirate - Perfect System-Wide Installer
# https://github.com/i12bp8/signalpirate-web

set -e

echo -e "\033[32m╔══════════════════════════════════════════════════╗\033[0m"
echo -e "\033[32m║         🏴‍☠️  SignalPirate System Installer  🏴‍☠️      ║\033[0m"
echo -e "\033[32m╚══════════════════════════════════════════════════╝\033[0m"
echo ""

if [ "$EUID" -ne 0 ]; then
  echo -e "\033[31m[!] Please run this installer as root.\033[0m"
  echo "If downloading via curl, use:"
  echo -e "  \033[36mcurl -sSL https://raw.githubusercontent.com/i12bp8/signalpirate-web/main/install.sh | sudo bash\033[0m"
  exit 1
fi

INSTALL_DIR="/opt/signalpirate-web"
USER_EXEC=$(logname || echo $SUDO_USER || echo $USER)

# 1. OS Detection & Dependency Installation
echo "[*] Detecting OS and installing dependencies..."
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$ID
    OS_LIKE=$ID_LIKE
else
    echo -e "\033[31m[!] Cannot determine OS.\033[0m"
    exit 1
fi

if [[ "$OS" == "ubuntu" || "$OS" == "debian" || "$OS" == "kali" || "$OS_LIKE" == *"debian"* || "$OS_LIKE" == *"ubuntu"* ]]; then
    apt-get update
    apt-get install -y python3 python3-pip python3-venv git rtl-433 hackrf libhackrf-dev udev curl
elif [[ "$OS" == "fedora" || "$OS_LIKE" == *"rhel"* || "$OS_LIKE" == *"fedora"* ]]; then
    dnf install -y python3 python3-pip python3-virtualenv git rtl-433 hackrf hackrf-devel systemd-udev curl
elif [[ "$OS" == "arch" || "$OS_LIKE" == *"arch"* ]]; then
    pacman -Sy --noconfirm python python-pip python-virtualenv git rtl-433 hackrf
else
    echo -e "\033[33m[!] Unsupported OS ($OS). Proceeding anyway, assuming dependencies are met.\033[0m"
fi

# 2. Setup Udev rules
echo "[*] Configuring udev rules for SDR hardware..."
echo 'SUBSYSTEMS=="usb", ATTRS{idVendor}=="1d50", ATTRS{idProduct}=="604b", MODE:="0660", GROUP:="plugdev"' > /etc/udev/rules.d/53-hackrf.rules
echo 'SUBSYSTEMS=="usb", ATTRS{idVendor}=="1d50", ATTRS{idProduct}=="6089", MODE:="0660", GROUP:="plugdev"' >> /etc/udev/rules.d/53-hackrf.rules
echo 'SUBSYSTEMS=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2838", MODE:="0660", GROUP:="plugdev"' > /etc/udev/rules.d/20-rtlsdr.rules
udevadm control --reload-rules
udevadm trigger

# Add user to plugdev group
usermod -aG plugdev $USER_EXEC || true

# 3. Clone / Update Repository
echo "[*] Syncing repository to $INSTALL_DIR..."
if [ -d "$INSTALL_DIR" ]; then
    cd "$INSTALL_DIR"
    git reset --hard
    git pull
else
    git clone https://github.com/i12bp8/signalpirate-web.git "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi
chown -R $USER_EXEC:$USER_EXEC "$INSTALL_DIR"

# 4. Setup Python Virtual Environment
echo "[*] Configuring Python virtual environment..."
sudo -u $USER_EXEC python3 -m venv venv
sudo -u $USER_EXEC ./venv/bin/pip install --upgrade pip
sudo -u $USER_EXEC ./venv/bin/pip install -r requirements.txt

# 5. Create Systemd Service
echo "[*] Creating Systemd background service..."
cat > /etc/systemd/system/signalpirate.service << EOF
[Unit]
Description=SignalPirate Web Interface
After=network.target

[Service]
Type=simple
User=$USER_EXEC
WorkingDirectory=$INSTALL_DIR
Environment="PATH=$INSTALL_DIR/venv/bin:/usr/bin:/usr/sbin"
ExecStart=$INSTALL_DIR/venv/bin/python3 $INSTALL_DIR/server.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable signalpirate.service
systemctl start signalpirate.service

# 6. Create Global CLI Command
echo "[*] Creating global 'signalpirate' command..."
cat > /usr/local/bin/signalpirate << 'EOF'
#!/usr/bin/env bash

if [ "$1" == "start" ]; then
    sudo systemctl start signalpirate
    echo "🏴‍☠️ SignalPirate started. Open http://localhost:8000"
elif [ "$1" == "stop" ]; then
    sudo systemctl stop signalpirate
    echo "🛑 SignalPirate stopped."
elif [ "$1" == "restart" ]; then
    sudo systemctl restart signalpirate
    echo "🔄 SignalPirate restarted."
elif [ "$1" == "status" ]; then
    systemctl status signalpirate
elif [ "$1" == "log" ] || [ "$1" == "logs" ]; then
    sudo journalctl -u signalpirate -n 100 -f
else
    echo "SignalPirate CLI"
    echo "Usage: signalpirate {start|stop|restart|status|logs}"
    echo ""
    echo "When started, the UI is available at http://localhost:8000"
fi
EOF
chmod +x /usr/local/bin/signalpirate

echo "---------------------------------"
echo -e "\033[32m✅ SYSTEM INSTALLATION COMPLETE!\033[0m"
echo ""
echo "SignalPirate is now running securely in the background (survives SSH disconnects)."
echo ""
echo "🌐 Open your browser to: http://localhost:8000"
echo ""
echo "🔧 To manage the service anywhere in your terminal, use:"
echo "  signalpirate start   # Starts the background server"
echo "  signalpirate stop    # Stops the server"
echo "  signalpirate logs    # View live signal streams and logs"
echo ""
echo "⚠️  Note: If you just plugged in your SDR, you might need to run 'signalpirate restart'."
