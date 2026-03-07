"""
AI Signal Classifier — Lightweight local AI for signal pattern analysis.

Uses statistical heuristics and pattern matching — no external APIs required.
"""
import logging
import time
import math

logger = logging.getLogger("signalpirate.ai_classifier")


class AIClassifier:
    """AI-powered signal analysis using local heuristic methods."""

    # Known modulation patterns (pulse timing signatures)
    KNOWN_PATTERNS = {
        "OOK_PWM": {
            "description": "On-Off Keying with Pulse Width Modulation",
            "short_pulse_range": (200, 600),  # microseconds
            "long_pulse_range": (600, 1500),
            "common_in": ["garage doors", "remote controls", "keyfobs"],
        },
        "OOK_Manchester": {
            "description": "On-Off Keying with Manchester encoding",
            "pulse_range": (400, 800),
            "equal_pulses": True,
            "common_in": ["weather stations", "TPMS sensors"],
        },
        "FSK": {
            "description": "Frequency Shift Keying",
            "common_in": ["weather stations", "utility meters", "automotive TPMS"],
        },
    }

    # Security assessment rules
    SECURITY_RULES = [
        {
            "condition": lambda s: s.get("security") == "none",
            "level": "CRITICAL",
            "message": "⚠️ NO SECURITY: This device uses no encryption or authentication. Signals can be trivially captured and replayed.",
            "recommendation": "Replace with a device using rolling codes or AES encryption.",
        },
        {
            "condition": lambda s: "fixed" in str(s.get("security", "")).lower() or "fixed" in str(s.get("vulnerability", "")).lower(),
            "level": "CRITICAL",
            "message": "🔓 FIXED CODE: Uses static codes that never change. An attacker only needs to capture one transmission.",
            "recommendation": "Upgrade to rolling-code or encrypted system immediately.",
        },
        {
            "condition": lambda s: "rolling" in str(s.get("security", "")).lower() or "keeloq" in str(s.get("name", "")).lower(),
            "level": "WARNING",
            "message": "🔐 ROLLING CODE: Uses hopping codes but may be vulnerable to advanced attacks (RollJam, DPA side-channel).",
            "recommendation": "Consider upgrading to AES-128 based systems. Keep firmware updated.",
        },
        {
            "condition": lambda s: "encrypted" in str(s.get("security", "")).lower() or "aes" in str(s.get("description", "")).lower(),
            "level": "INFO",
            "message": "✅ ENCRYPTED: Uses modern encryption. Significantly more secure than fixed or rolling code systems.",
            "recommendation": "Keep firmware updated. Use long, random keys.",
        },
    ]

    def __init__(self):
        self.analysis_cache = {}

    def analyze_signal(self, signal_data, protocol_info=None):
        """
        Perform AI analysis on a signal.

        Returns a comprehensive analysis dict with:
        - classification: what type of signal this likely is
        - security_assessment: how secure the device is
        - explanation: human-readable description
        - recommendations: what the user should know
        """
        analysis = {
            "timestamp": time.time(),
            "classification": self._classify_signal(signal_data, protocol_info),
            "security_assessment": self._assess_security(signal_data, protocol_info),
            "explanation": self._generate_explanation(signal_data, protocol_info),
            "risk_score": self._calculate_risk_score(signal_data, protocol_info),
        }

        return analysis

    def _classify_signal(self, signal_data, protocol_info):
        """Classify signal type based on patterns."""
        model = signal_data.get("model", "Unknown")

        if protocol_info:
            return {
                "identified": True,
                "protocol": protocol_info.get("name", model),
                "category": protocol_info.get("category", "Unknown"),
                "modulation": protocol_info.get("modulation", "Unknown"),
                "encoding": protocol_info.get("encoding", "Unknown"),
                "confidence": 0.95,
            }

        # Unknown signal — try heuristic classification
        modulation = signal_data.get("mod", "")
        freq = signal_data.get("freq", 0)

        category_guess = "Unknown Device"
        confidence = 0.3

        # Frequency-based hints
        if 433000000 <= freq <= 434000000:
            category_guess = "433 MHz ISM Band Device"
            confidence = 0.4
        elif 314000000 <= freq <= 316000000:
            category_guess = "315 MHz Device (US/Asia)"
            confidence = 0.4
        elif 867000000 <= freq <= 869000000:
            category_guess = "868 MHz Device (EU)"
            confidence = 0.4

        return {
            "identified": False,
            "protocol": model,
            "category": category_guess,
            "modulation": modulation or "OOK (assumed)",
            "encoding": "Unknown",
            "confidence": confidence,
        }

    def _assess_security(self, signal_data, protocol_info):
        """Assess the security posture of the detected device."""
        if not protocol_info:
            return {
                "level": "UNKNOWN",
                "message": "🔍 Unable to assess security — protocol not identified.",
                "recommendation": "Capture more signals for analysis. Check device documentation.",
            }

        for rule in self.SECURITY_RULES:
            try:
                if rule["condition"](protocol_info):
                    return {
                        "level": rule["level"],
                        "message": rule["message"],
                        "recommendation": rule["recommendation"],
                    }
            except Exception:
                continue

        return {
            "level": "UNKNOWN",
            "message": "Security assessment inconclusive.",
            "recommendation": "Manual analysis recommended.",
        }

    def _calculate_risk_score(self, signal_data, protocol_info):
        """Calculate a 0-100 risk score."""
        if not protocol_info:
            return 50  # Unknown = medium risk

        score_map = {
            "critical": 95,
            "high": 75,
            "medium": 50,
            "low": 25,
        }
        security_level = protocol_info.get("security_level", "medium")
        return score_map.get(security_level, 50)

    def _generate_explanation(self, signal_data, protocol_info):
        """Generate a human-readable explanation of the signal."""
        model = signal_data.get("model", "Unknown")

        if not protocol_info:
            freq = signal_data.get("freq", 0)
            freq_mhz = freq / 1e6 if freq > 1e6 else freq
            return (
                f"📡 Detected an unidentified signal from '{model}' "
                f"on {freq_mhz:.3f} MHz. "
                f"This could be a remote control, sensor, or IoT device. "
                f"More captures may help identify the protocol."
            )

        name = protocol_info.get("name", model)
        category = protocol_info.get("category", "device")
        security = protocol_info.get("security", "unknown")
        desc = protocol_info.get("description", "")

        explanation = f"📡 **{name}** — {category}\n\n"
        explanation += f"{desc}\n\n"

        if security == "none":
            explanation += (
                "🔴 **Security: NONE** — This device transmits with no encryption "
                "or authentication. Any captured signal can be replayed to perform "
                "the same action (open door, trigger alarm, etc.).\n\n"
            )
        elif security == "rolling_code":
            explanation += (
                "🟡 **Security: Rolling Code** — Uses changing codes to prevent "
                "simple replay attacks, but may be vulnerable to advanced techniques "
                "like RollJam or cryptanalytic attacks.\n\n"
            )
        elif security == "encrypted":
            explanation += (
                "🟢 **Security: Encrypted** — Uses modern encryption. "
                "Significantly harder to attack.\n\n"
            )

        devices = protocol_info.get("devices", [])
        if devices:
            explanation += f"**Known devices**: {', '.join(devices[:5])}\n"

        return explanation

    def batch_analyze(self, signals):
        """Analyze multiple signals and provide summary insights."""
        if not signals:
            return {"summary": "No signals to analyze.", "signals": []}

        analyses = []
        categories = {}
        risk_scores = []

        for sig in signals:
            analysis = self.analyze_signal(
                sig.get("data", sig),
                sig.get("protocol_info")
            )
            analyses.append(analysis)
            cat = analysis["classification"]["category"]
            categories[cat] = categories.get(cat, 0) + 1
            risk_scores.append(analysis["risk_score"])

        avg_risk = sum(risk_scores) / len(risk_scores) if risk_scores else 0

        return {
            "total_signals": len(signals),
            "categories": categories,
            "average_risk_score": round(avg_risk, 1),
            "highest_risk": max(risk_scores) if risk_scores else 0,
            "summary": (
                f"Analyzed {len(signals)} signals across {len(categories)} categories. "
                f"Average risk score: {avg_risk:.0f}/100."
            ),
            "analyses": analyses,
        }
