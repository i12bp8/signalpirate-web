#!/usr/bin/env python3
"""
SignalPirate Web — FastAPI server with WebSocket for real-time RF signal analysis.
"""
import asyncio
import json
import logging
import os
import shutil
import subprocess
import time
import webbrowser
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Add project root to path BEFORE importing any local modules or third-party packages that might conflict
ROOT = Path(__file__).parent.resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.rtl433_engine import RTL433Engine, Signal
from backend.protocol_db import ProtocolDB
from backend.vuln_db import VulnDB
from backend.sdr_detector import SDRDetector
from backend.config import load_config, save_config, get_config
from backend import ai_engine
from backend import signal_export
from backend import tx_engine
from backend import protocol_features

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("signalpirate.server")

# ── App State ────────────────────────────────────────────────────────────────
app = FastAPI(title="SignalPirate", version="4.0.0")

DATA_DIR = ROOT / "data"
CAPTURE_DIR = Path.home() / ".config" / "signalpirate" / "exports"
CAPTURE_DIR.mkdir(parents=True, exist_ok=True)

proto_db = ProtocolDB(str(DATA_DIR))
vuln_db = VulnDB(str(DATA_DIR))
proto_db.load()
vuln_db.load()
protocol_catalog = protocol_features.build_protocol_catalog(proto_db.get_all())
sdr = SDRDetector()
engine = RTL433Engine(protocol_db=proto_db, vuln_db=vuln_db)
INIT_SIGNAL_HISTORY_COUNT = 250

# Connected WebSocket clients
ws_clients: Set[WebSocket] = set()

# Signal history (kept in memory for API access)
signal_history: List[Dict[str, Any]] = []
MAX_HISTORY = 1000
_next_signal_id = 0
_seen_hashes: Dict[int, float] = {}

# ── Missing TX Helpers ────────────────────────────────────────────────────────
def _normalize_model_name(name: str) -> str:
    return str(name or "").strip().lower()

def _model_from_capture_filename(path: Path) -> str:
    return path.stem.split("_")[0] if "_" in path.stem else path.stem

def _find_companion_iq_file(path: Path) -> Optional[Path]:
    for suffix in (".cs8", ".c8"):
        candidate = path.with_suffix(suffix)
        if candidate.exists():
            return candidate
    return None

def _prefer_iq_backed_c8(path: Path) -> Path:
    if path.suffix == ".sub":
        iq_path = _find_companion_iq_file(path)
        if iq_path is not None:
            return iq_path
    return path

def _tx_profile_key(frequency: int, target_model: str) -> str:
    return f"{int(frequency)}:{_normalize_model_name(target_model) or '_generic'}"

def _get_saved_tx_profile(target_model: str, frequency: int) -> Optional[dict]:
    profiles = get_config().get("tx_profiles", {})
    if not isinstance(profiles, dict):
        return None
    profile = profiles.get(_tx_profile_key(frequency, target_model))
    return dict(profile) if isinstance(profile, dict) else None

def _save_tx_profile(target_model: str, frequency: int, profile: dict) -> dict:
    profiles = get_config().get("tx_profiles", {})
    if not isinstance(profiles, dict):
        profiles = {}
    profiles = dict(profiles)
    stored = {
        "target_model": str(target_model or ""),
        "frequency": int(frequency),
        "frequency_offset_hz": int(profile.get("frequency_offset_hz", 0)),
        "tx_vga": max(0, min(tx_engine.HACKRF_MAX_TX_VGA, int(profile.get("tx_vga", tx_engine.HACKRF_DEFAULT_TX_VGA)))),
        "score": int(profile.get("score", 0)),
        "updated_at": int(time.time()),
    }
    profiles[_tx_profile_key(frequency, target_model)] = stored
    save_config({"tx_profiles": profiles})
    return stored

def _build_replay_profile(path: Path) -> dict:
    if path.suffix not in {".cs8", ".c8"}:
        return {"tx_allowed": True, "reasons": [], "profile_type": "generic"}

    tx_params = _load_c8_tx_params(path)
    saved = _get_saved_tx_profile(tx_params.get("target_model", ""), tx_params["frequency"])
    if saved:
        effective_frequency = int(tx_params["frequency"]) + int(saved.get("frequency_offset_hz", 0))
        return {
            "tx_allowed": True,
            "reasons": [],
            "profile_type": "saved",
            "target_model": tx_params.get("target_model", ""),
            "sample_rate": tx_params["sample_rate"],
            "base_frequency": tx_params["frequency"],
            "frequency": effective_frequency,
            "frequency_offset_hz": int(saved.get("frequency_offset_hz", 0)),
            "tx_vga": int(saved.get("tx_vga", tx_engine.HACKRF_DEFAULT_TX_VGA)),
            "score": int(saved.get("score", 0)),
            "updated_at": int(saved.get("updated_at", 0)),
        }

    return {
        "tx_allowed": True,
        "reasons": [],
        "profile_type": "default",
        "target_model": tx_params.get("target_model", ""),
        "sample_rate": tx_params["sample_rate"],
        "base_frequency": tx_params["frequency"],
        "frequency": tx_params["frequency"],
        "frequency_offset_hz": 0,
        "tx_vga": tx_engine.HACKRF_DEFAULT_TX_VGA,
    }

def _load_c8_tx_params(path: Path) -> dict:
    frequency = 433_920_000
    sample_rate = tx_engine.HACKRF_DEFAULT_SAMPLE_RATE
    target_model = _model_from_capture_filename(path)
    meta = {}

    meta_path = Path(str(path) + ".meta.json")
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
        except Exception as e:
            logger.warning("Failed reading TX metadata for %s: %s", path, e)
            meta = {}

    try:
        if meta.get("frequency") is not None:
            frequency = int(meta["frequency"])
    except Exception:
        pass

    try:
        if meta.get("sample_rate") is not None:
            sample_rate = int(meta["sample_rate"])
    except Exception:
        pass

    meta_target = str(meta.get("target_model", "") or "").strip()
    if meta_target:
        target_model = meta_target

    if frequency == 433_920_000:
        try:
            inferred_freq = signal_export._parse_iq_center_freq_hz(str(path))
            if inferred_freq:
                frequency = int(inferred_freq)
        except Exception:
            pass

    if sample_rate == tx_engine.HACKRF_DEFAULT_SAMPLE_RATE:
        try:
            sample_rate = int(signal_export._guess_iq_sample_rate_hz(str(path)))
        except Exception:
            pass

    return {
        "frequency": int(frequency),
        "sample_rate": int(sample_rate),
        "target_model": target_model,
        "meta": meta,
    }

def _rank_probe_signals(rows: List[Dict[str, Any]], target_model: str) -> List[Dict[str, Any]]:
    target_norm = _normalize_model_name(target_model)
    clean_rows = [dict(row) for row in rows if isinstance(row, dict)]
    clean_rows.sort(key=lambda row: (0 if _normalize_model_name(row.get("model", "")) == target_norm else 1, _normalize_model_name(row.get("model", ""))))
    return clean_rows

def _profile_from_candidate(tx_params: dict, candidate: Optional[dict] = None) -> dict:
    profile = _build_replay_profile(Path(tx_params.get("path", ""))) if tx_params.get("path") else {
        "tx_vga": tx_engine.HACKRF_DEFAULT_TX_VGA,
        "frequency_offset_hz": 0,
        "frequency": int(tx_params["frequency"]),
        "profile_type": "default",
    }
    if candidate:
        profile = dict(profile)
        profile["tx_vga"] = max(0, min(tx_engine.HACKRF_MAX_TX_VGA, int(candidate.get("tx_vga", profile.get("tx_vga", tx_engine.HACKRF_DEFAULT_TX_VGA)))))
        profile["frequency"] = int(candidate.get("frequency", tx_params["frequency"]))
        profile["frequency_offset_hz"] = int(candidate.get("frequency_offset_hz", profile["frequency"] - int(tx_params["frequency"])))
        profile["profile_type"] = "candidate"
        if candidate.get("label"):
            profile["label"] = str(candidate["label"])
    return profile

async def _tx_probe_capture(path: Path, requested_name: str, candidate: Optional[dict] = None) -> dict:
    if not engine.get_stats().get("running"):
        return {"success": False, "error": "TX probe requires a running receiver.", "probe_path": str(path)}
    if engine.device_type == "hackrf":
        return {"success": False, "error": "TX probe requires a separate RX SDR. Current receiver is HackRF.", "probe_path": str(path)}

    tx_params = _load_c8_tx_params(path) if path.suffix in {".cs8", ".c8"} else {
        "frequency": 433_920_000,
        "sample_rate": tx_engine.HACKRF_DEFAULT_SAMPLE_RATE,
        "target_model": _model_from_capture_filename(path),
        "meta": {},
    }
    tx_params["path"] = str(path)
    profile = _profile_from_candidate(tx_params, candidate=candidate)
    target_model = tx_params.get("target_model") or _model_from_capture_filename(path)

    try:
        tx_file = tx_engine.describe_tx_file(str(path), sample_rate=tx_params["sample_rate"]) if path.suffix in {".cs8", ".c8"} else {
            "duration_s": 0.6,
            "sample_rate": tx_engine.HACKRF_DEFAULT_SAMPLE_RATE,
            "bytes": path.stat().st_size if path.exists() else 0,
        }
    except Exception as e:
        return {"success": False, "error": str(e), "probe_path": str(path)}

    observation_window_s = max(1.2, min(3.0, float(tx_file.get("duration_s", 0.6)) + 0.9))
    observed_rows: List[Dict[str, Any]] = []

    def _capture_listener(sig_dict: Dict[str, Any]) -> None:
        observed_rows.append(dict(sig_dict))

    engine.add_listener(_capture_listener)
    try:
        if path.suffix in {".cs8", ".c8"}:
            tx_result = await tx_engine.transmit_c8_file(
                str(path),
                frequency=int(profile["frequency"]),
                sample_rate=tx_params["sample_rate"],
                tx_vga=int(profile.get("tx_vga", tx_engine.HACKRF_DEFAULT_TX_VGA)),
            )
        else:
            tx_result = await tx_engine.transmit_sub_file(
                str(path),
                tx_vga=int(profile.get("tx_vga", tx_engine.HACKRF_DEFAULT_TX_VGA)),
            )
        if tx_result.get("success"):
            await asyncio.sleep(observation_window_s)
    finally:
        engine.remove_listener(_capture_listener)

    ranked_rows = _rank_probe_signals(observed_rows, target_model)
    score = tx_engine.score_probe_observation(ranked_rows, target_model=target_model)
    model_counts = sorted(score["models_seen"].items(), key=lambda item: item[1], reverse=True)

    return {
        **tx_result,
        "probe_path": str(path),
        "probe_signals": ranked_rows[:20],
        "probe_frequency": int(profile["frequency"]),
        "probe_sample_rate": int(tx_params["sample_rate"]),
        "probe_target_model": target_model,
        "probe_target_hits": int(score["target_hits"]),
        "probe_total_hits": int(score["total_hits"]),
        "probe_non_target_hits": int(score["non_target_hits"]),
        "probe_score": int(score["score"]),
        "probe_models": [{"model": model, "count": count} for model, count in model_counts[:5]],
        "probe_duration_s": float(tx_file.get("duration_s", 0.0)),
        "probe_window_s": observation_window_s,
        "replay_profile": profile,
    }

async def _tx_auto_tune_capture(path: Path, requested_name: str) -> dict:
    if not engine.get_stats().get("running"):
        return {"success": False, "error": "Auto tune requires a running receiver.", "probe_path": str(path)}
    if engine.device_type == "hackrf":
        return {"success": False, "error": "Auto tune requires a separate RX SDR. Current receiver is HackRF.", "probe_path": str(path)}
    if path.suffix not in {".cs8", ".c8"}:
        return {"success": False, "error": "Auto tune currently requires a .cs8 replay file.", "probe_path": str(path)}

    tx_params = _load_c8_tx_params(path)
    tx_params["path"] = str(path)
    tx_file = tx_engine.describe_tx_file(str(path), sample_rate=tx_params["sample_rate"])
    duration_s = float(tx_file.get("duration_s", 0.0))
    if duration_s > tx_engine.AUTO_TUNE_MAX_FILE_SECONDS:
        return {
            "success": False,
            "error": (
                f"Auto tune only runs on captures up to {tx_engine.AUTO_TUNE_MAX_FILE_SECONDS:.2f}s. "
                "Trim the replay first."
            ),
            "probe_path": str(path),
            "probe_duration_s": duration_s,
        }

    base_profile = _build_replay_profile(path)
    candidates = tx_engine.build_auto_tune_candidates(tx_params["frequency"])
    preferred = {
        "frequency": int(base_profile.get("frequency", tx_params["frequency"])),
        "frequency_offset_hz": int(base_profile.get("frequency_offset_hz", 0)),
        "tx_vga": int(base_profile.get("tx_vga", tx_engine.HACKRF_DEFAULT_TX_VGA)),
        "label": f"{int(base_profile.get('frequency', tx_params['frequency'])) / 1e6:.6f} MHz @ VGA {int(base_profile.get('tx_vga', tx_engine.HACKRF_DEFAULT_TX_VGA))}",
    }
    preferred_key = (preferred["frequency"], preferred["tx_vga"])
    deduped = [preferred]
    dedup_seen = {preferred_key}
    for candidate in candidates:
        key = (int(candidate["frequency"]), int(candidate["tx_vga"]))
        if key in dedup_seen:
            continue
        dedup_seen.add(key)
        deduped.append(candidate)

    attempts: List[Dict[str, Any]] = []
    best_result: Optional[Dict[str, Any]] = None
    best_key: Optional[Tuple[int, int, int, int]] = None

    for attempt_index, candidate in enumerate(deduped, start=1):
        result = await _tx_probe_capture(path, requested_name=requested_name, candidate=candidate)
        summary = {
            "attempt": attempt_index,
            "frequency": int(candidate["frequency"]),
            "frequency_offset_hz": int(candidate.get("frequency_offset_hz", 0)),
            "tx_vga": int(candidate["tx_vga"]),
            "success": bool(result.get("success")),
            "score": int(result.get("probe_score", -9999)),
            "target_hits": int(result.get("probe_target_hits", 0)),
            "total_hits": int(result.get("probe_total_hits", 0)),
            "error": result.get("error"),
        }
        attempts.append(summary)

        compare_key = (
            int(result.get("probe_score", -9999)),
            int(result.get("probe_target_hits", 0)),
            -abs(int(candidate.get("frequency_offset_hz", 0))),
            -int(candidate["tx_vga"]),
        )
        if best_result is None or compare_key > best_key:
            best_result = result
            best_key = compare_key

    if best_result is None:
        return {"success": False, "error": "Auto tune could not run any TX attempts.", "probe_path": str(path)}

    saved_profile = None
    if best_result.get("success") and (
        int(best_result.get("probe_target_hits", 0)) > 0 or int(best_result.get("probe_total_hits", 0)) > 0
    ):
        saved_profile = _save_tx_profile(
            best_result.get("probe_target_model", tx_params.get("target_model", "")),
            tx_params["frequency"],
            {
                "frequency_offset_hz": int(best_result.get("replay_profile", {}).get("frequency_offset_hz", 0)),
                "tx_vga": int(best_result.get("replay_profile", {}).get("tx_vga", tx_engine.HACKRF_DEFAULT_TX_VGA)),
                "score": int(best_result.get("probe_score", 0)),
            },
        )

    return {
        **best_result,
        "success": any(item["success"] for item in attempts),
        "auto_tune": True,
        "probe_path": str(path),
        "tuning_attempts": attempts,
        "tuning_candidate_count": len(attempts),
        "saved_profile": saved_profile,
        "replay_profile": dict(best_result.get("replay_profile", {})),
    }

def _emit_probe_rows_to_dashboard(result: dict) -> None:
    if not result.get("success"):
        return

    target_norm = _normalize_model_name(str(result.get("probe_target_model", "")))
    raw_rows = result.get("probe_signals", [])
    if not isinstance(raw_rows, list):
        return

    def _row_rank(row: Dict[str, Any]) -> tuple[int, str]:
        model = _normalize_model_name(str(row.get("model", "")))
        return (0 if (target_norm and model == target_norm) else 1, model)

    ranked_rows = sorted(
        [r for r in raw_rows if isinstance(r, dict)],
        key=_row_rank,
    )[:20]

    for i, row in enumerate(ranked_rows):
        raw_freq = row.get("freq", result.get("probe_frequency", 433_920_000))
        try:
            fval = float(raw_freq)
            freq_hz = fval * 1_000_000 if fval < 10_000 else fval
        except Exception:
            freq_hz = float(result.get("probe_frequency", 433_920_000))
        mod_raw = str(row.get("mod", row.get("modulation", "OOK")) or "OOK").strip().upper()
        if mod_raw in {"ASK", "AM", "OOK_PWM", "OOK_PPM", "OOK_MANCHESTER"}:
            mod = "OOK"
        elif "FSK" in mod_raw:
            mod = "FSK"
        else:
            mod = mod_raw or "OOK"
        rssi = row.get("rssi", row.get("snr"))
        proto = proto_db.match_signal(row) if proto_db else None
        vuln = vuln_db.match_signal(row, proto) if vuln_db else None
        synthetic = {
            "timestamp": time.time() + (i * 0.001),
            "model": row.get("model", "Probe-Unknown"),
            "protocol_id": row.get("protocol"),
            "frequency": freq_hz,
            "rssi": rssi,
            "modulation": mod,
            "iq_file": result.get("probe_path"),
            "data": {**row, "source": "tx_probe", "rssi_estimated": rssi is None},
            "protocol_info": proto,
            "vuln_info": vuln,
            "source": "tx_probe",
        }
        on_signal(synthetic)

# ── WebSocket broadcast ──────────────────────────────────────────────────────
def on_signal(signal_dict: Dict[str, Any]) -> None:
    """Called by RTL433Engine for each decoded signal — broadcast to all WS clients."""
    global _next_signal_id, _seen_hashes
    
    cfg = get_config()
    
    # Sniper Mode filter: ONLY pass signals matching sniper model
    sniper_model = cfg.get("sniper_mode_model", "").strip()
    if sniper_model:
        model = str(signal_dict.get("model", "")).strip()
        if model.lower() != sniper_model.lower():
            return
            
    # Unique Scans filter: Drop identical repeating signals
    if cfg.get("unique_scans_only", False):
        sig_data = dict(signal_dict.get("data", {}))
        for k in ["time", "rssi", "snr", "noise", "msg1", "msg2", "mic", "fsk_pe"]:
            sig_data.pop(k, None)
        sig_hash = hash(f"{signal_dict.get('model')}_{signal_dict.get('frequency')}_{json.dumps(sig_data, sort_keys=True)}")
        now = time.time()
        
        # Debounce identical signals within a 60 second window
        if sig_hash in _seen_hashes and (now - _seen_hashes[sig_hash]) < 60:
            _seen_hashes[sig_hash] = now
            return
        _seen_hashes[sig_hash] = now

    # Store in history using a monotonic ID
    signal_dict["_id"] = _next_signal_id
    _next_signal_id += 1
    signal_dict["capabilities"] = protocol_features.get_signal_capabilities(signal_dict)
    
    signal_history.append(signal_dict)
    if len(signal_history) > MAX_HISTORY:
        signal_history.pop(0)

    # Broadcast to all connected clients
    msg = json.dumps({"type": "signal", "data": signal_dict}, default=str)
    dead: List[WebSocket] = []
    for ws in ws_clients:
        try:
            asyncio.ensure_future(ws.send_text(msg))
        except Exception:
            dead.append(ws)
    for ws in dead:
        ws_clients.discard(ws)


engine.add_listener(on_signal)


# ── Startup / Shutdown ────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    logger.info("🏴‍☠️ SignalPirate Web v4.0 starting...")
    cfg = load_config()
    await sdr.detect_devices()
    if sdr.primary_device:
        dev = sdr.primary_device
        logger.info(f"Primary SDR device: {dev.name}")
        logger.info(f"Found SDR: {dev.name} ({dev.device_type})")
        
        # Auto-enable TX if saved in config
        if cfg.get("research_mode"):
            try:
                tx_engine.enable_research_mode()
                logger.info("⚠️ TX Research Mode auto-enabled from config")
            except ImportError:
                pass

        if await sdr.check_rtl433():
            started = await engine.start(
                frequency=433920000,
                device_type=dev.device_type,
                rtl_autolevel=cfg.get("rtl_autolevel", True),
                rtl_squelch=cfg.get("rtl_squelch", True),
                rtl_gain=cfg.get("rtl_gain", 38)
            )
            if started:
                logger.info("✅ rtl_433 engine running — signals flowing")
            else:
                logger.warning("⚠️ Engine failed to start")
        else:
            logger.warning("⚠️ rtl_433 not found")
    else:
        logger.warning("⚠️ No SDR device detected — running in demo mode")


@app.on_event("shutdown")
async def shutdown():
    await engine.stop()
    logger.info("SignalPirate stopped")


# ── Static Files ──────────────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(str(ROOT / "static" / "index.html"))


# ── WebSocket ─────────────────────────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    ws_clients.add(ws)
    logger.info(f"WebSocket client connected ({len(ws_clients)} total)")

    # Send current state
    await ws.send_text(json.dumps({
        "type": "init",
        "data": {
            "stats": engine.get_stats(),
            "signals": list(signal_history)[-INIT_SIGNAL_HISTORY_COUNT:] if signal_history else [],
            "device": {
                "name": sdr.primary_device.name if sdr.primary_device else "No Device",
                "type": sdr.primary_device.device_type if sdr.primary_device else "none",
                "capabilities": list(sdr.primary_device.capabilities) if sdr.primary_device else [],
            },
            "config": load_config(),
        },
    }, default=str))

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
                await _handle_ws_command(ws, msg)
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        pass
    finally:
        ws_clients.discard(ws)
        logger.info(f"WebSocket client disconnected ({len(ws_clients)} total)")


async def _handle_ws_command(ws: WebSocket, msg: dict) -> None:
    """Handle commands from WebSocket clients."""
    cmd = msg.get("cmd")
    data = msg.get("data", {})

    if cmd == "set_frequency":
        freq = int(data.get("frequency", 433_920_000))
        ok = await engine.set_frequency(freq)
        await ws.send_text(json.dumps({"type": "frequency_changed", "data": {"frequency": freq, "ok": ok}}))

    elif cmd == "get_stats":
        await ws.send_text(json.dumps({"type": "stats", "data": engine.get_stats()}, default=str))

    elif cmd == "save_settings":
        new_cfg = data.get("settings", {})
        updated = save_config(new_cfg)
        if updated.get("research_mode"):
            tx_engine.enable_research_mode()
        else:
            tx_engine.disable_research_mode()
        
        # Apply the new SDR config live if we're running an RTL SDR
        cfg = get_config()
        if engine.get_stats().get("running"):
            await engine.start(
                frequency=engine.frequency,
                device_type=engine.device_type,
                rtl_autolevel=cfg.get("rtl_autolevel", True),
                rtl_squelch=cfg.get("rtl_squelch", True),
                rtl_gain=cfg.get("rtl_gain", 38)
            )
            
        await ws.send_text(json.dumps({"type": "config_saved", "data": updated}, default=str))

    elif cmd == "get_signals":
        count = int(data.get("count", INIT_SIGNAL_HISTORY_COUNT))
        sigs = list(signal_history)[-count:] if signal_history else []
        await ws.send_text(json.dumps({"type": "signals", "data": sigs}, default=str))

    elif cmd == "get_protocol_catalog":
        await ws.send_text(json.dumps({"type": "protocol_catalog", "data": protocol_catalog}, default=str))

    elif cmd == "export_signal":
        format_type = data.get("format", "sub")
        sig_id = data.get("signal_id")
        
        # Find the signal by its actual _id rather than assuming the list index matches
        sig = next((s for s in signal_history if s.get("_id") == sig_id), None)
        
        if sig is not None:
            
            # Reconstruct the expected 'export_data' dictionary structure for signal_export matching the app.js raw
            export_data = {
                "time": sig.get("timestamp", time.time()),
                "model": sig.get("model"),
                "freq": sig.get("frequency", 433920000),
                "mod": sig.get("modulation", "OOK"),
                "data": sig.get("data", {}),
                "iq_file": sig.get("iq_file"),
                "protocol_info": sig.get("protocol_info"),
            }
            protocol_info = sig.get("protocol_info")
            if protocol_info:
                export_data["protocol"] = protocol_info.get("name")
                
            path = ""
            try:
                if format_type == "sub":
                    path = signal_export.export_flipper_sub(export_data)
                elif format_type == "fob":
                    path = signal_export.export_pandawa_fob(export_data)
                elif format_type == "json":
                    path = signal_export.export_raw_json(export_data)
                elif format_type == "c8":
                    path = signal_export.export_hackrf_c8(export_data)
                    
                if path:
                    await ws.send_text(json.dumps({"type": "exported", "data": {"path": str(path)}}))
                else:
                    await ws.send_text(json.dumps({"type": "error", "data": {"message": "Export failed"}}))
            except Exception as e:
                logger.error(f"Export failed: {e}")
                await ws.send_text(json.dumps({"type": "error", "data": {"message": str(e)}}))
        else:
            await ws.send_text(json.dumps({"type": "error", "data": {"message": "Signal not found"}}))
            
    elif cmd == "ai_analyze_signal":
        sig_id = data.get("signal_id")
        sig = next((s for s in signal_history if s.get("_id") == sig_id), None)
        if sig is not None:
            sig_dict = {
                "model": sig.get("model"),
                "frequency": sig.get("frequency"),
                "modulation": sig.get("modulation"),
                "rssi": sig.get("rssi"),
                "data": sig.get("data"),
                "protocol_info": sig.get("protocol_info")
            }
            # Start stream marker
            await ws.send_text(json.dumps({"type": "ai_chunk", "data": {"id": "analyze", "signal_id": sig_id, "chunk": ""}}))
            async for text_chunk in ai_engine.analyze_signal(get_config(), sig_dict):
                await ws.send_text(json.dumps({"type": "ai_chunk", "data": {"id": "analyze", "signal_id": sig_id, "chunk": text_chunk}}))
            # End stream marker
            await ws.send_text(json.dumps({"type": "ai_chunk_end", "data": {"id": "analyze", "signal_id": sig_id}}))

    elif cmd == "ai_craft_payload":
        prompt = data.get("prompt", "")
        base_freq = data.get("base_freq", 433_920_000)
        
        await ws.send_text(json.dumps({"type": "ai_chunk", "data": {"id": "craft", "chunk": ""}}))
        async for text_chunk in ai_engine.craft_payload(get_config(), prompt, base_freq):
            await ws.send_text(json.dumps({"type": "ai_chunk", "data": {"id": "craft", "chunk": text_chunk}}))
        await ws.send_text(json.dumps({"type": "ai_chunk_end", "data": {"id": "craft"}}))

    elif cmd == "ai_chat":
        chat_id = data.get("chat_id")
        prompt = data.get("prompt", "")
        context = data.get("context")
        history = data.get("history", [])
        
        await ws.send_text(json.dumps({"type": "ai_chunk", "data": {"id": chat_id, "chunk": ""}}))
        async for text_chunk in ai_engine.chat(get_config(), prompt, context, history):
            await ws.send_text(json.dumps({"type": "ai_chunk", "data": {"id": chat_id, "chunk": text_chunk}}))
        await ws.send_text(json.dumps({"type": "ai_chunk_end", "data": {"id": chat_id}}))

    elif cmd == "clear_signals":
        signal_history.clear()
        await ws.send_text(json.dumps({"type": "cleared"}))
        
    elif cmd == "tx_signal":
        fname = data.get("filename")
        if fname:
            path = CAPTURE_DIR / fname
            tx_path = path
            # If a matching IQ export exists, prefer it over a .sub replay for fidelity.
            if fname.endswith(".sub"):
                iq_candidate = _find_companion_iq_file(path)
                if iq_candidate is not None:
                    tx_path = iq_candidate
            tx_path = _prefer_iq_backed_c8(tx_path)
            replay_profile = _build_replay_profile(tx_path)
            if not replay_profile.get("tx_allowed", True):
                res = {
                    "success": False,
                    "error": "Replay blocked: " + " ".join(replay_profile.get("reasons", [])[:2]),
                    "rx_delta_2s": 0,
                    "rx_target_model": _model_from_capture_filename(tx_path),
                    "rx_target_hits_2s": 0,
                    "replay_profile": replay_profile,
                }
                await ws.send_text(json.dumps({"type": "tx_status", "data": res}))
                return
            # HackRF cannot TX while rtl_433 has RX open. Pause RX, transmit, then resume.
            resume_rx = False
            can_probe_live = bool(engine.get_stats().get("running")) and engine.device_type != "hackrf"
            if engine.get_stats().get("running") and engine.device_type == "hackrf":
                await engine.stop()
                resume_rx = True
                await asyncio.sleep(0.2)

            try:
                if can_probe_live:
                    res = await _tx_probe_capture(tx_path, requested_name=fname, candidate=replay_profile)
                else:
                    if tx_path.suffix in {".c8", ".cs8"}:
                        txp = _load_c8_tx_params(tx_path)
                        res = await tx_engine.transmit_c8_file(
                            str(tx_path),
                            frequency=int(replay_profile.get("frequency", txp["frequency"])),
                            sample_rate=txp["sample_rate"],
                            tx_vga=int(replay_profile.get("tx_vga", tx_engine.HACKRF_DEFAULT_TX_VGA)),
                        )
                    else:
                        res = await tx_engine.transmit_sub_file(
                            str(tx_path),
                            tx_vga=int(replay_profile.get("tx_vga", tx_engine.HACKRF_DEFAULT_TX_VGA)),
                        )
            finally:
                if resume_rx:
                    restarted = await engine.start(
                        frequency=engine.frequency,
                        device_type="hackrf",
                        hackrf_gain=engine.hackrf_gain,
                    )
                    if not restarted:
                        logger.warning("RX restart failed after TX")

            res["replay_profile"] = replay_profile
            res["rx_target_model"] = str(res.get("probe_target_model") or replay_profile.get("target_model") or _model_from_capture_filename(tx_path))
            res["rx_target_hits_2s"] = int(res.get("probe_target_hits", 0))
            res["rx_delta_2s"] = int(res.get("probe_total_hits", 0))
            if res.get("probe_models"):
                res["rx_models_2s"] = list(res["probe_models"])
            elif not can_probe_live:
                res["rx_notice"] = "Replay sent without live probe. Use a separate RX SDR for verification."
            res["replay_profile"] = replay_profile
            # Just send an acknowledgment back to the UI
            await ws.send_text(json.dumps({"type": "tx_status", "data": res}))

    elif cmd == "tx_probe":
        fname = data.get("filename")
        if fname:
            path = CAPTURE_DIR / fname
            tx_path = path
            if fname.endswith(".sub"):
                iq_candidate = _find_companion_iq_file(path)
                if iq_candidate is not None:
                    tx_path = iq_candidate
            tx_path = _prefer_iq_backed_c8(tx_path)
            result = await _tx_probe_capture(tx_path, requested_name=fname)
            _emit_probe_rows_to_dashboard(result)
            await ws.send_text(json.dumps({"type": "tx_probe_result", "data": result}))

    elif cmd == "tx_auto_tune":
        fname = data.get("filename")
        if fname:
            path = CAPTURE_DIR / fname
            tx_path = _prefer_iq_backed_c8(path)
            result = await _tx_auto_tune_capture(tx_path, requested_name=fname)
            _emit_probe_rows_to_dashboard(result)
            await ws.send_text(json.dumps({"type": "tx_tune_result", "data": result}))


# ── REST API ──────────────────────────────────────────────────────────────────
@app.get("/api/status")
async def api_status():
    return {
        "engine": engine.get_stats(),
        "device": {
            "name": sdr.primary_device.name if sdr.primary_device else "No Device",
            "type": sdr.primary_device.device_type if sdr.primary_device else "none",
        },
        "signals_count": len(signal_history),
    }

@app.get("/api/detect")
async def api_detect():
    """Trigger a manual rescan of SDR hardware."""
    devices = await sdr.detect_devices()
    return sdr.get_status()

@app.post("/api/export")
async def api_export(body: dict):
    format_type = body.get("format", "sub")
    sig_id = body.get("signal_id")
    sig = next((s for s in signal_history if s.get("_id") == sig_id), None)
    
    if not sig:
        return JSONResponse({"ok": False, "error": "Signal not found"})
        
    export_data = {
        "time": sig.get("timestamp", time.time()),
        "model": sig.get("model"),
        "freq": sig.get("frequency", 433920000),
        "mod": sig.get("modulation", "OOK"),
        "data": sig.get("data", {}),
        "iq_file": sig.get("iq_file"),
        "protocol_info": sig.get("protocol_info"),
    }
    
    protocol_info = sig.get("protocol_info")
    if protocol_info:
        export_data["protocol"] = protocol_info.get("name")
        
    path = ""
    try:
        if format_type == "sub":
            path = signal_export.export_flipper_sub(export_data)
        elif format_type == "fob":
            path = signal_export.export_pandawa_fob(export_data)
        elif format_type == "json":
            path = signal_export.export_raw_json(export_data)
        elif format_type == "c8":
            path = signal_export.export_hackrf_c8(export_data)
            
        if path:
            return {"ok": True, "path": str(path)}
        else:
            return JSONResponse({"ok": False, "error": "Export path not generated"})
            
    except Exception as e:
        logger.error(f"Export failed: {e}")
        return JSONResponse({"ok": False, "error": str(e)})


@app.get("/api/signals")
async def api_signals(count: int = INIT_SIGNAL_HISTORY_COUNT):
    return signal_history[-count:]


@app.get("/api/signals/{signal_id}")
async def api_signal_detail(signal_id: int):
    sig = next((s for s in signal_history if s.get("_id") == signal_id), None)
    if sig is not None:
        return sig
    raise HTTPException(status_code=404, detail="Signal not found")


@app.post("/api/frequency")
async def api_set_frequency(body: dict):
    freq = int(body.get("frequency", 433_920_000))
    ok = await engine.set_frequency(freq)
    return {"ok": ok, "frequency": freq}


@app.get("/api/library")
async def api_library():
    """List saved capture files."""
    files = []
    for ext in ("*.sub", "*.fob", "*.json", "*.cs8", "*.c8", "*.xml", "*.zip", "*.raw", "*.cu8", "*.u8"):
        for f in CAPTURE_DIR.glob(ext):
            if f.name.endswith(".urh.zip"):
                continue
            companion_zip = f.with_suffix(".urh.zip")
            variant_meta = Path(str(f) + ".variant.json")
            files.append({
                "name": f.name,
                "format": f.suffix[1:],
                "size": f.stat().st_size,
                "modified": f.stat().st_mtime,
                "path": str(f),
                "can_tx": f.suffix in {".cs8", ".c8", ".sub"},
                "has_urh_zip": companion_zip.exists(),
                "is_variant": variant_meta.exists(),
            })
    files.sort(key=lambda x: x["modified"], reverse=True)
    return files


@app.get("/api/protocols")
async def api_protocols():
    return protocol_catalog


@app.post("/api/editor/build")
async def api_editor_build(body: dict):
    sig_id = body.get("signal_id")
    edits = body.get("edits", {})
    sig = next((s for s in signal_history if s.get("_id") == sig_id), None)
    if not sig:
        return JSONResponse({"ok": False, "error": "Signal not found"})

    try:
        result = protocol_features.build_protocol_variant(sig, edits, CAPTURE_DIR)
        return {"ok": True, **result}
    except Exception as e:
        logger.error(f"Variant build failed: {e}")
        return JSONResponse({"ok": False, "error": str(e)})


@app.get("/api/library/{filename}")
async def api_library_file(filename: str):
    path = CAPTURE_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(
        str(path),
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    port = int(os.environ.get("PORT", 8433))
    print(f"""
╔══════════════════════════════════════════════════╗
║         🏴‍☠️  SignalPirate Web v4.0  🏴‍☠️            ║
║                                                  ║
║   http://localhost:{port}                         ║
║                                                  ║
║   ⚠️  EDUCATIONAL & RESEARCH PURPOSES ONLY  ⚠️    ║
╚══════════════════════════════════════════════════╝
""")
    webbrowser.open(f"http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
