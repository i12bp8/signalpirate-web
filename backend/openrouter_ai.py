"""
OpenRouter AI Integration — Uses OpenRouter API for advanced signal analysis.

Falls back to local heuristic analysis when no API key is configured.
"""
import json
import logging
import os
import urllib.request
import urllib.error

logger = logging.getLogger("signalpirate.openrouter_ai")

CONFIG_DIR = os.path.expanduser("~/.config/signalpirate")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")


def load_config() -> dict:
    """Load configuration from disk."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_config(config: dict) -> None:
    """Save configuration to disk."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


class OpenRouterAI:
    """AI signal analysis via OpenRouter API."""

    API_URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self):
        config = load_config()
        self.api_key = config.get("openrouter_api_key", "")
        self.model = config.get("ai_model", "google/gemini-2.0-flash-001")
        self.enabled = bool(self.api_key)

    def set_api_key(self, key: str) -> None:
        """Set and persist the API key."""
        self.api_key = key.strip()
        self.enabled = bool(self.api_key)
        config = load_config()
        config["openrouter_api_key"] = self.api_key
        save_config(config)
        logger.info("OpenRouter API key saved")

    def set_model(self, model: str) -> None:
        """Set the AI model."""
        self.model = model
        config = load_config()
        config["ai_model"] = self.model
        save_config(config)

    def analyze_signal(self, signal_data: dict, protocol_info: dict = None,
                       vuln_info: list = None, custom_query: str = None) -> str:
        """Send signal data to OpenRouter for AI analysis."""
        if not self.enabled:
            return ""

        prompt = self._build_prompt(signal_data, protocol_info, vuln_info, custom_query)

        try:
            payload = json.dumps({
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are SignalPirate AI, an expert RF signal analyst. "
                            "Analyze the captured signal and provide: "
                            "1) What this signal is and what device likely sent it. "
                            "2) Security assessment — how vulnerable is this protocol? "
                            "3) What an attacker could do (educational context). "
                            "4) Mitigation recommendations. "
                            "Be concise, use bullet points. Max 200 words."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 500,
                "temperature": 0.3,
            }).encode("utf-8")

            req = urllib.request.Request(
                self.API_URL,
                data=payload,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com/signalpirate/signalpirate",
                    "X-Title": "SignalPirate",
                },
            )

            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return result["choices"][0]["message"]["content"]

        except urllib.error.HTTPError as e:
            logger.error(f"OpenRouter API error: {e.code} {e.reason}")
            return f"❌ AI Error: {e.code} — check your API key"
        except urllib.error.URLError as e:
            logger.error(f"OpenRouter connection error: {e}")
            return "❌ AI Error: Cannot reach OpenRouter API"
        except Exception as e:
            logger.error(f"OpenRouter error: {e}")
            return f"❌ AI Error: {e}"

    def _build_prompt(self, signal_data: dict, protocol_info: dict = None,
                      vuln_info: list = None, custom_query: str = None) -> str:
        """Build the analysis prompt from signal data."""
        lines = ["Analyze this captured RF signal:\n"]

        lines.append(f"Model: {signal_data.get('model', 'Unknown')}")
        freq = signal_data.get('frequency', signal_data.get('freq', 0))
        if freq:
            lines.append(f"Frequency: {freq / 1e6 if freq > 1e6 else freq:.3f} MHz")
        lines.append(f"Modulation: {signal_data.get('modulation', signal_data.get('mod', 'Unknown'))}")

        if protocol_info:
            lines.append(f"\nProtocol: {protocol_info.get('name', 'Unknown')}")
            lines.append(f"Category: {protocol_info.get('category', 'Unknown')}")
            lines.append(f"Security: {protocol_info.get('security', 'Unknown')}")
            lines.append(f"Security Level: {protocol_info.get('security_level', 'Unknown')}")
            desc = protocol_info.get("description", "")
            if desc:
                lines.append(f"Description: {desc}")

        if vuln_info:
            lines.append("\nKnown vulnerabilities:")
            for v in (vuln_info if isinstance(vuln_info, list) else [vuln_info]):
                lines.append(f"  - {v.get('id', '?')}: {v.get('title', '')}")
                lines.append(f"    Attack: {v.get('attack_type', '?')}")
                lines.append(f"    Severity: {v.get('severity', '?')}")

        # Include raw data keys
        data = signal_data.get("data", signal_data)
        extra_keys = {k: v for k, v in data.items()
                      if k not in ("model", "freq", "mod", "time", "rssi")}
        if extra_keys:
            lines.append(f"\nRaw data fields: {json.dumps(extra_keys, default=str)[:300]}")

        if custom_query:
            lines.append("\nUser Custom Query:")
            lines.append(f"\"{custom_query}\"")
            lines.append("Please address this specific query in your analysis.")

        return "\n".join(lines)

    def test_connection(self) -> tuple:
        """Test the API connection. Returns (success, message)."""
        if not self.api_key:
            return False, "No API key configured"

        try:
            payload = json.dumps({
                "model": self.model,
                "messages": [{"role": "user", "content": "Say 'OK' in one word."}],
                "max_tokens": 10,
            }).encode("utf-8")

            req = urllib.request.Request(
                self.API_URL,
                data=payload,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )

            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return True, f"Connected — model: {self.model}"

        except urllib.error.HTTPError as e:
            return False, f"HTTP {e.code}: {e.reason}"
        except Exception as e:
            return False, str(e)
