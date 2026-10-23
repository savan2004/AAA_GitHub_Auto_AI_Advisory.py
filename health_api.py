"""Separate HTTP health probe. Do not mistake it for the Telegram worker's health."""
import os
import time
from flask import Flask, jsonify

app = Flask(__name__)
_STARTED_MONOTONIC = time.monotonic()


@app.get("/health")
def health_check():
    # This only confirms the health API process is responding.
    return jsonify({"status": "alive", "component": "health-api", "telegram_worker_verified": False}), 200


@app.get("/status")
def status():
    return jsonify({"status": "alive", "component": "health-api", "telegram_worker_verified": False}), 200


@app.get("/uptime")
def uptime():
    return jsonify({"uptime_seconds": round(time.monotonic() - _STARTED_MONOTONIC, 2)}), 200


@app.get("/logs")
def logs():
    return jsonify({"error": "Logs are not exposed publicly. Use hosting log access."}), 501


@app.post("/restart")
def restart():
    return jsonify({"error": "Restart endpoint disabled. Use hosting process supervisor."}), 501


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("HEALTH_PORT", "5001")))
