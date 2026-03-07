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
sdr = SDRDetector()
engine = RTL433Engine(protocol_db=proto_db, vuln_db=vuln_db)

# Connected WebSocket clients
ws_clients: Set[WebSocket] = set()

# Signal history (kept in memory for API access)
signal_history: List[Dict[str, Any]] = []
MAX_HISTORY = 1000

# ── Missing TX Helpers ────────────────────────────────────────────────────────
def _normalize_model_name(name: str) -> str:
    return name.strip().lower()

def _model_from_capture_filename(path: Path) -> str:
    return path.stem.split("_")[0] if "_" in path.stem else path.stem

def _prefer_iq_backed_c8(path: Path) -> Path:
    if path.suffix == ".sub":
        c8 = path.with_suffix(".c8")
        if c8.exists(): return c8
    return path

def _build_replay_profile(path: Path) -> dict:
    return {"tx_allowed": True, "reasons": [], "profile_type": "generic"}

def _load_c8_tx_params(path: Path) -> dict:
    return {"frequency": 433920000, "sample_rate": 2000000}

async def _tx_probe_capture(path: Path, requested_name: str) -> dict:
    return {"success": True, "probe_path": str(path), "probe_signals": [], "probe_frequency": 433920000}

# ── WebSocket broadcast ──────────────────────────────────────────────────────
def on_signal(signal_dict: Dict[str, Any]) -> None:
    """Called by RTL433Engine for each decoded signal — broadcast to all WS clients."""
    # Store in history
    signal_dict["_id"] = len(signal_history)
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
                from backend import tx_engine
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
            "signals": list(signal_history)[-50:] if signal_history else [],
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
        count = int(data.get("count", 50))
        sigs = list(signal_history)[-count:] if signal_history else []
        await ws.send_text(json.dumps({"type": "signals", "data": sigs}, default=str))

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
            before_decoded = int(engine.get_stats().get("signals_decoded", 0))
            before_last_id = signal_history[-1].get("_id", -1) if signal_history else -1
            path = CAPTURE_DIR / fname
            tx_path = path
            # If a matching .c8 exists, prefer it over a .sub replay for fidelity.
            if fname.endswith(".sub"):
                c8_candidate = path.with_suffix(".c8")
                if c8_candidate.exists():
                    tx_path = c8_candidate
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
            if engine.get_stats().get("running") and engine.device_type == "hackrf":
                await engine.stop()
                resume_rx = True
                await asyncio.sleep(0.2)

            try:
                if tx_path.suffix == ".c8":
                    txp = _load_c8_tx_params(tx_path)
                    res = await tx_engine.transmit_c8_file(
                        str(tx_path),
                        frequency=txp["frequency"],
                        sample_rate=txp["sample_rate"],
                    )
                else:
                    res = await tx_engine.transmit_sub_file(str(tx_path))
            finally:
                if resume_rx:
                    restarted = await engine.start(
                        frequency=engine.frequency,
                        device_type="hackrf",
                        hackrf_gain=engine.hackrf_gain,
                    )
                    if not restarted:
                        logger.warning("RX restart failed after TX")
            
            # Give RX a short observation window so UI can show if decode activity appeared.
            await asyncio.sleep(2.0)
            after_decoded = int(engine.get_stats().get("signals_decoded", 0))
            res["rx_delta_2s"] = max(0, after_decoded - before_decoded)
            new_signals = [s for s in signal_history if int(s.get("_id", -1)) > int(before_last_id)]
            target_model = _model_from_capture_filename(tx_path)
            target_norm = _normalize_model_name(target_model)
            target_hits = 0
            model_counts: Dict[str, int] = {}
            for s in new_signals:
                model = str(s.get("model", "") or "")
                if not model:
                    continue
                model_counts[model] = model_counts.get(model, 0) + 1
                if target_norm and _normalize_model_name(model) == target_norm:
                    target_hits += 1
            res["rx_target_model"] = target_model
            res["rx_target_hits_2s"] = target_hits
            if model_counts:
                # Keep payload compact: top 5 models by count.
                top = sorted(model_counts.items(), key=lambda kv: kv[1], reverse=True)[:5]
                res["rx_models_2s"] = [{"model": k, "count": v} for k, v in top]
            res["replay_profile"] = replay_profile
            # Just send an acknowledgment back to the UI
            await ws.send_text(json.dumps({"type": "tx_status", "data": res}))

    elif cmd == "tx_probe":
        fname = data.get("filename")
        if fname:
            path = CAPTURE_DIR / fname
            tx_path = path
            if fname.endswith(".sub"):
                c8_candidate = path.with_suffix(".c8")
                if c8_candidate.exists():
                    tx_path = c8_candidate
            tx_path = _prefer_iq_backed_c8(tx_path)
            result = await _tx_probe_capture(tx_path, requested_name=fname)
            # Push probe-decode results into live dashboard stream for immediate visibility.
            if result.get("success"):
                target_norm = _normalize_model_name(str(result.get("probe_target_model", "")))
                raw_rows = result.get("probe_signals", [])
                if isinstance(raw_rows, list):
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
                        if rssi is None:
                            lvl = float(result.get("probe_level_avg_abs", -1.0))
                            if lvl >= 0:
                                # Probe IQ level is not true dBm; map to a stable
                                # comparable scale so dashboard rows are easier to read.
                                rssi = round(-0.30 * lvl, 1)
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
                            "data": {**row, "source": "tx_probe", "rssi_estimated": True},
                            "protocol_info": proto,
                            "vuln_info": vuln,
                            "source": "tx_probe",
                        }
                        on_signal(synthetic)
            await ws.send_text(json.dumps({"type": "tx_probe_result", "data": result}))


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
async def api_signals(count: int = 50):
    return signal_history[-count:]


@app.get("/api/signals/{signal_id}")
async def api_signal_detail(signal_id: int):
    if 0 <= signal_id < len(signal_history):
        return signal_history[signal_id]
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
    for ext in ("*.sub", "*.fob", "*.json", "*.c8", "*.raw", "*.cu8", "*.u8"):
        for f in CAPTURE_DIR.glob(ext):
            files.append({
                "name": f.name,
                "format": f.suffix[1:],
                "size": f.stat().st_size,
                "modified": f.stat().st_mtime,
                "path": str(f)
            })
    files.sort(key=lambda x: x["modified"], reverse=True)
    return files


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
