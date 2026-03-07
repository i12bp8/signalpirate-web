#!/usr/bin/env bash

# SignalPirate - Auto-Installer
# https://github.com/signalpirate/signalpirate-web

set -e

echo -e "\033[32m🏴‍☠️ SignalPirate Installer\033[0m"
echo "---------------------------------"

# 1. System Requirements
echo "[*] Checking system requirements..."
if ! command -v apt &> /dev/null; then
    echo -e "\033[31m[!] Error: This installer requires a Debian/Ubuntu based Linux distribution (apt).\033[0m"
    exit 1
fi

# 2. Install dependencies
echo "[*] Installing system dependencies (requires sudo)..."
sudo apt update
sudo apt install -y python3 python3-pip python3-venv git rtl-433 hackrf libhackrf-dev udev

# 3. Setup Udev rules for HackRF and RTL-SDR (so we don't need sudo to run)
echo "[*] Setting up udev rules for SDR devices..."
sudo sh -c 'echo "SUBSYSTEMS==\"usb\", ATTRS{idVendor}==\"1d50\", ATTRS{idProduct}==\"604b\", MODE:=\"0660\", GROUP:=\"plugdev\"" > /etc/udev/rules.d/53-hackrf.rules'
sudo sh -c 'echo "SUBSYSTEMS==\"usb\", ATTRS{idVendor}==\"1d50\", ATTRS{idProduct}==\"6089\", MODE:=\"0660\", GROUP:=\"plugdev\"" >> /etc/udev/rules.d/53-hackrf.rules'
sudo sh -c 'echo "SUBSYSTEMS==\"usb\", ATTRS{idVendor}==\"0bda\", ATTRS{idProduct}==\"2838\", MODE:=\"0660\", GROUP:=\"plugdev\"" > /etc/udev/rules.d/20-rtlsdr.rules'
sudo udevadm control --reload-rules
sudo udevadm trigger

# 4. Clone repository
echo "[*] Cloning SignalPirate repository..."
if [ -d "signalpirate-web" ]; then
    echo "[!] Directory 'signalpirate-web' already exists. Updating..."
    cd signalpirate-web
    git pull
else
    # Assuming it's uploaded to github
    git clone https://github.com/i12bp8/signalpirate-web.git
    cd signalpirate-web
fi

# 5. Setup Python Virtual Environment
echo "[*] Setting up Python virtual environment..."
python3 -m venv venv
source venv/bin/activate

# 6. Install Python requirements
echo "[*] Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

echo "---------------------------------"
echo -e "\033[32m✅ Installation Complete!\033[0m"
echo ""
echo "To start SignalPirate, run:"
echo "  cd signalpirate-web"
echo "  source venv/bin/activate"
echo "  python3 server.py"
echo ""
echo "Then open your browser to: http://localhost:8000"
echo "Note: You may need to unplug and re-plug your SDR devices for udev rules to apply."
