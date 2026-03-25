"""
app.py  —  Flask web server for WalletSnapshot
Run:  python app.py
Then open:  http://localhost:5000
"""

import json
import os
import threading
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file, abort
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import snapshot_engine as engine

app      = Flask(__name__)
BASE     = Path(__file__).parent
WALLETS  = BASE / "wallets.json"
SETTINGS = BASE / "settings.json"
OUTPUTS  = BASE / "outputs"
OUTPUTS.mkdir(exist_ok=True)

scheduler = BackgroundScheduler(timezone="UTC")
scheduler.start()

# ── wallet persistence ────────────────────────────────────────────────────────
def load_wallets():
    if WALLETS.exists():
        return json.loads(WALLETS.read_text())
    return []

def save_wallets(data):
    WALLETS.write_text(json.dumps(data, indent=2))

# ── settings persistence ──────────────────────────────────────────────────────
def load_settings():
    if SETTINGS.exists():
        return json.loads(SETTINGS.read_text())
    return {"wait_secs": 12, "max_height": 3000}

def save_settings(data):
    SETTINGS.write_text(json.dumps(data, indent=2))

# ── run in background thread ──────────────────────────────────────────────────
_lock = threading.Lock()

def _run(wallets, wait_secs=12, max_height=3000):
    with _lock:
        engine.run_snapshot(wallets, wait_secs=wait_secs, max_height=max_height)

def trigger_run():
    if engine.status["running"]:
        return
    wallets = load_wallets()
    if not wallets:
        engine.status["message"] = "No wallets configured."
        return
    s = load_settings()
    wait_secs  = s.get("wait_secs", 12)
    max_height = s.get("max_height", 3000)
    t = threading.Thread(target=_run, args=(wallets,),
                         kwargs={"wait_secs": wait_secs, "max_height": max_height}, daemon=True)
    t.start()

# ── routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")

# wallets CRUD
@app.route("/api/wallets", methods=["GET"])
def get_wallets():
    return jsonify(load_wallets())

@app.route("/api/wallets", methods=["POST"])
def set_wallets():
    data = request.get_json()
    save_wallets(data)
    return jsonify({"ok": True})

# settings
@app.route("/api/settings", methods=["GET"])
def get_settings():
    return jsonify(load_settings())

@app.route("/api/settings", methods=["POST"])
def post_settings():
    data = request.get_json()
    s = load_settings()
    if "wait_secs" in data:
        s["wait_secs"] = max(3, int(data["wait_secs"]))
    if "max_height" in data:
        s["max_height"] = max(0, int(data["max_height"]))  # 0 = no limit
    save_settings(s)
    return jsonify({"ok": True})

# snapshot control
@app.route("/api/run", methods=["POST"])
def api_run():
    if engine.status["running"]:
        return jsonify({"ok": False, "msg": "Already running"}), 409
    trigger_run()
    return jsonify({"ok": True})

@app.route("/api/status")
def api_status():
    return jsonify(engine.status)

# schedule
@app.route("/api/schedule", methods=["GET"])
def get_schedule():
    job = scheduler.get_job("snapshot")
    if job and job.next_run_time:
        nxt = job.next_run_time.strftime("%Y-%m-%d %H:%M UTC")
        trg = str(job.trigger)
        return jsonify({"enabled": True, "next": nxt, "trigger": trg})
    return jsonify({"enabled": False})

@app.route("/api/schedule", methods=["POST"])
def set_schedule():
    body  = request.get_json()
    time_ = body.get("time", "")        # "HH:MM"
    days  = body.get("days", "daily")   # "daily" | "mon-fri" | "weekends"
    enabled = body.get("enabled", True)

    scheduler.remove_job("snapshot") if scheduler.get_job("snapshot") else None

    if enabled and time_:
        hh, mm = time_.split(":")
        day_map = {
            "daily"   : "*",
            "mon-fri" : "mon-fri",
            "weekends": "sat,sun",
        }
        dow = day_map.get(days, "*")
        scheduler.add_job(trigger_run, CronTrigger(hour=hh, minute=mm,
                          day_of_week=dow, timezone="UTC"),
                          id="snapshot", replace_existing=True)
        nxt = scheduler.get_job("snapshot").next_run_time.strftime("%Y-%m-%d %H:%M UTC")
        return jsonify({"ok": True, "next": nxt})

    return jsonify({"ok": True, "next": None})

# file download
@app.route("/api/download/<filename>")
def download(filename):
    path = OUTPUTS / filename
    if not path.exists() or not path.suffix == ".xlsx":
        abort(404)
    return send_file(str(path), as_attachment=True)

@app.route("/api/files")
def list_files():
    files = sorted(OUTPUTS.glob("*.xlsx"), key=os.path.getmtime, reverse=True)
    return jsonify([{"name": f.name,
                     "size": f"{f.stat().st_size//1024} KB",
                     "date": datetime.fromtimestamp(f.stat().st_mtime,
                             tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
                    for f in files[:30]])

@app.route("/api/delete/<filename>", methods=["DELETE"])
def delete_file(filename):
    path = OUTPUTS / filename
    if not path.exists() or path.suffix != ".xlsx":
        abort(404)
    path.unlink()
    return jsonify({"ok": True})

@app.route("/api/delete-all", methods=["DELETE"])
def delete_all_files():
    removed = 0
    for f in OUTPUTS.glob("*.xlsx"):
        f.unlink()
        removed += 1
    return jsonify({"ok": True, "removed": removed})

# ── start ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    is_server = os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("HEADLESS")

    print("\n" + "="*55)
    print(f"  WalletSnapshot  —  http://localhost:{port}")
    print("="*55 + "\n")

    # only auto-open browser when running locally
    if not is_server:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{port}")).start()

    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
