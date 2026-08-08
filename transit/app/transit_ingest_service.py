import logging

from flask import Flask, jsonify

from upload_transit_to_bucket import run_once_from_env


app = Flask(__name__)


@app.get("/healthz")
def healthz():
    """Return service health without calling external dependencies."""
    return jsonify(status="ok"), 200


@app.post("/poll")
def poll():
    """Fetch one transit snapshot and write it to Cloud Storage."""
    try:
        result = run_once_from_env()
    except Exception:
        app.logger.exception("Transit poll failed")
        return jsonify(error="Transit poll failed"), 500

    return jsonify(message=result), 200
