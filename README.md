# SignalPirate

A fast, lightweight web UI for capturing, analyzing, and managing sub-GHz RF signals. Uses `rtl_433` for radio interfacing and integrates an AI model to quickly explain captured packets.

Built for Linux.

## Features
- **Live Waterfall Dashboard**: Real-time signal interception via WebSocket.
- **AI Signal Analysis**: Ask questions about captured protocols directly in the browser.
- **Easy Export**: Save signals to `.c8` IQ files or JSON for analysis in URH (Universal Radio Hacker).
- **Dark Mode UI**: Clean, responsive, distraction-free interface.
- *(Experimental)* HackRF Transmit support for replaying raw `.c8` files.

## Installation
Run this one-liner to install dependencies, configure `udev` rules, and set up the Python environment:
```bash
curl -sSL https://raw.githubusercontent.com/signalpirate/signalpirate-web/main/install.sh | bash
```

### Manual Start (Post-Install)
```bash
cd signalpirate-web
source venv/bin/activate
python3 server.py
# Open http://localhost:8000
```

## Hardware Supported
- **RTL-SDR** (Primary receiver)
- **HackRF One** (Optional transmitter)

## AI Setup
To use the analysis chat, you need an [OpenRouter](https://openrouter.ai/) API key. Enter it in the "Settings" tab in the web UI.

> **Disclaimer:** This tool is for educational purposes and authorized auditing only. You are solely responsible for ensuring your RF transmissions comply with local laws (FCC, etc.). Do not transmit on frequencies you do not have permission for.
