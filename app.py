"""
AI VoiceOver & Talking Avatar Studio
------------------------------------
Flask backend that powers two studios:

  * Voice Studio  -> Text-to-Speech via Microsoft edge-tts
  * Avatar Studio -> Lip-sync video via Wav2Lip / talking-photo via SadTalker

Run with:  python app.py   (serves on http://localhost:5000)
"""

import os
import re
import sys
import uuid
import time
import glob
import shutil
import asyncio
import threading
import subprocess
from datetime import datetime

from flask import (
    Flask,
    request,
    jsonify,
    render_template,
    send_from_directory,
)
from flask_cors import CORS

# edge-tts is required for the Voice Studio. We import it lazily-friendly so the
# server can still boot (and report a helpful error) if it is missing.
try:
    import edge_tts
    EDGE_TTS_AVAILABLE = True
except ImportError:  # pragma: no cover - only triggers on a broken install
    edge_tts = None
    EDGE_TTS_AVAILABLE = False


# --------------------------------------------------------------------------- #
# Paths & configuration
# --------------------------------------------------------------------------- #
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
OUTPUT_DIR = os.path.join(BASE_DIR, "outputs")
WAV2LIP_DIR = os.path.join(BASE_DIR, "Wav2Lip")
SADTALKER_DIR = os.path.join(BASE_DIR, "SadTalker")
WAV2LIP_CHECKPOINT = os.path.join(WAV2LIP_DIR, "checkpoints", "wav2lip.pth")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

ALLOWED_VIDEO = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
ALLOWED_IMAGE = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
ALLOWED_AUDIO = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}

# Default voice used when the client does not specify one.
DEFAULT_VOICE = "en-US-JennyNeural"

app = Flask(__name__)
CORS(app)
app.config["MAX_CONTENT_LENGTH"] = 256 * 1024 * 1024  # 256 MB upload ceiling


# --------------------------------------------------------------------------- #
# In-memory job registry for the (potentially long-running) video pipeline
# --------------------------------------------------------------------------- #
JOBS = {}
JOBS_LOCK = threading.Lock()


def _new_job(mode):
    job_id = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[job_id] = {
            "id": job_id,
            "mode": mode,
            "status": "queued",      # queued | processing | done | error
            "logs": [],
            "output_url": None,
            "error": None,
            "created": time.time(),
        }
    return job_id


def _log(job_id, message):
    """Append a timestamped line to the job's console log."""
    stamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{stamp}] {message}"
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is not None:
            job["logs"].append(line)
    print(f"({job_id}) {line}", flush=True)


def _set_status(job_id, status, **fields):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is not None:
            job["status"] = status
            job.update(fields)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _ext(filename):
    return os.path.splitext(filename or "")[1].lower()


def _safe_stem(name, fallback):
    """Return a filesystem-safe stem for an uploaded filename."""
    stem = os.path.splitext(os.path.basename(name or ""))[0]
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_")
    return stem or fallback


def _run_subprocess(cmd, cwd, job_id):
    """
    Run a subprocess, streaming combined stdout/stderr into the job log.
    Returns the process return code.
    """
    _log(job_id, "RUN: " + " ".join(cmd))
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
        )
    except FileNotFoundError as exc:
        _log(job_id, f"ERROR: could not start process: {exc}")
        return 127

    for raw in iter(proc.stdout.readline, ""):
        line = raw.rstrip()
        if line:
            _log(job_id, line)
    proc.stdout.close()
    return proc.wait()


def _newest_video(folder, since_ts):
    """Find the newest .mp4 created in `folder` (recursively) after `since_ts`."""
    candidates = []
    for path in glob.glob(os.path.join(folder, "**", "*.mp4"), recursive=True):
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if mtime >= since_ts - 1:
            candidates.append((mtime, path))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


# --------------------------------------------------------------------------- #
# Routes: pages & static media
# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/outputs/<path:filename>")
def serve_output(filename):
    return send_from_directory(OUTPUT_DIR, filename)


@app.route("/uploads/<path:filename>")
def serve_upload(filename):
    return send_from_directory(UPLOAD_DIR, filename)


@app.route("/download/notebook")
def download_notebook():
    """Serve the Colab renderer notebook as a download for the UI helper."""
    return send_from_directory(
        os.path.join(BASE_DIR, "colab"),
        "AvatarStudio_Colab.ipynb",
        as_attachment=True,
    )


@app.route("/api/health")
def health():
    return jsonify(
        {
            "edge_tts": EDGE_TTS_AVAILABLE,
            "ffmpeg": shutil.which("ffmpeg") is not None,
            "wav2lip_ready": os.path.exists(WAV2LIP_CHECKPOINT),
            "sadtalker_ready": os.path.isdir(SADTALKER_DIR),
        }
    )


# --------------------------------------------------------------------------- #
# Feature 1: Text to Speech (edge-tts)
# --------------------------------------------------------------------------- #
@app.route("/api/voices")
def api_voices():
    if not EDGE_TTS_AVAILABLE:
        return jsonify({"error": "edge-tts is not installed (pip install edge-tts)"}), 500
    try:
        voices = asyncio.run(edge_tts.list_voices())
    except Exception as exc:  # pragma: no cover - network dependent
        return jsonify({"error": f"Could not fetch voices: {exc}"}), 500

    cleaned = []
    for v in voices:
        cleaned.append(
            {
                "name": v.get("ShortName"),
                "gender": v.get("Gender"),
                "locale": v.get("Locale"),
                "friendly": v.get("FriendlyName", v.get("ShortName")),
            }
        )
    cleaned.sort(key=lambda x: (x["locale"] or "", x["name"] or ""))
    return jsonify({"voices": cleaned})


def _clamp_rate(value):
    """Map a slider value (-100..100, percent) to an edge-tts rate string."""
    try:
        pct = int(round(float(value)))
    except (TypeError, ValueError):
        pct = 0
    pct = max(-100, min(100, pct))
    return f"{'+' if pct >= 0 else ''}{pct}%"


def _clamp_pitch(value):
    """Map a slider value (-50..50, Hz) to an edge-tts pitch string."""
    try:
        hz = int(round(float(value)))
    except (TypeError, ValueError):
        hz = 0
    hz = max(-50, min(50, hz))
    return f"{'+' if hz >= 0 else ''}{hz}Hz"


async def _synthesize(text, voice, rate, pitch, out_path):
    communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    await communicate.save(out_path)


@app.route("/api/tts", methods=["POST"])
def api_tts():
    if not EDGE_TTS_AVAILABLE:
        return jsonify({"error": "edge-tts is not installed (pip install edge-tts)"}), 500

    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    voice = (data.get("voice") or DEFAULT_VOICE).strip()
    rate = _clamp_rate(data.get("rate", 0))
    pitch = _clamp_pitch(data.get("pitch", 0))

    if not text:
        return jsonify({"error": "Please enter some text to speak."}), 400
    if len(text) > 20000:
        return jsonify({"error": "Text is too long (max 20,000 characters)."}), 400

    filename = f"tts_{uuid.uuid4().hex[:10]}.mp3"
    out_path = os.path.join(OUTPUT_DIR, filename)

    try:
        asyncio.run(_synthesize(text, voice, rate, pitch, out_path))
    except Exception as exc:
        return jsonify({"error": f"Speech generation failed: {exc}"}), 500

    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        return jsonify({"error": "No audio was produced. Check the voice name and try again."}), 500

    return jsonify(
        {
            "success": True,
            "filename": filename,
            "audio_url": f"/outputs/{filename}",
            "voice": voice,
            "rate": rate,
            "pitch": pitch,
        }
    )


# --------------------------------------------------------------------------- #
# Feature 2: Lip Sync / Talking Avatar (Wav2Lip / SadTalker)
# --------------------------------------------------------------------------- #
def _run_wav2lip(job_id, face_path, audio_path, out_path):
    if not os.path.exists(WAV2LIP_CHECKPOINT):
        _set_status(
            job_id,
            "error",
            error="Wav2Lip checkpoint not found at Wav2Lip/checkpoints/wav2lip.pth",
        )
        _log(job_id, "ERROR: missing Wav2Lip checkpoint. See README for setup.")
        return

    cmd = [
        sys.executable,
        os.path.join("Wav2Lip", "inference.py"),
        "--checkpoint_path", os.path.join("Wav2Lip", "checkpoints", "wav2lip.pth"),
        "--face", face_path,
        "--audio", audio_path,
        "--outfile", out_path,
    ]
    code = _run_subprocess(cmd, cwd=BASE_DIR, job_id=job_id)
    if code == 0 and os.path.exists(out_path):
        _log(job_id, "Wav2Lip finished successfully.")
        _set_status(job_id, "done", output_url=f"/outputs/{os.path.basename(out_path)}")
    else:
        _log(job_id, f"Wav2Lip exited with code {code}.")
        _set_status(job_id, "error", error=f"Wav2Lip failed (exit code {code}). See logs.")


def _run_sadtalker(job_id, image_path, audio_path):
    if not os.path.isdir(SADTALKER_DIR):
        _set_status(job_id, "error", error="SadTalker is not installed at ./SadTalker/")
        _log(job_id, "ERROR: SadTalker folder not found. See README for setup.")
        return

    started = time.time()
    cmd = [
        sys.executable,
        os.path.join("SadTalker", "inference.py"),
        "--driven_audio", audio_path,
        "--source_image", image_path,
        "--result_dir", OUTPUT_DIR,
    ]
    code = _run_subprocess(cmd, cwd=BASE_DIR, job_id=job_id)
    result = _newest_video(OUTPUT_DIR, started)
    if code == 0 and result:
        rel = os.path.relpath(result, OUTPUT_DIR).replace(os.sep, "/")
        _log(job_id, f"SadTalker produced: {rel}")
        _set_status(job_id, "done", output_url=f"/outputs/{rel}")
    else:
        _log(job_id, f"SadTalker exited with code {code}.")
        _set_status(job_id, "error", error=f"SadTalker failed (exit code {code}). See logs.")


def _process_job(job_id, mode, face_path, audio_path, cleanup_paths):
    _set_status(job_id, "processing")
    _log(job_id, f"Starting {mode} job...")
    try:
        if mode == "wav2lip":
            out_path = os.path.join(OUTPUT_DIR, f"lipsync_{job_id}.mp4")
            _run_wav2lip(job_id, face_path, audio_path, out_path)
        else:
            _run_sadtalker(job_id, face_path, audio_path)
    except Exception as exc:  # pragma: no cover - defensive
        _log(job_id, f"FATAL: {exc}")
        _set_status(job_id, "error", error=str(exc))
    finally:
        # Clean up temporary uploaded inputs (never the reusable TTS output).
        for path in cleanup_paths:
            try:
                if path and os.path.exists(path) and os.path.commonpath(
                    [os.path.abspath(path), UPLOAD_DIR]
                ) == UPLOAD_DIR:
                    os.remove(path)
                    _log(job_id, f"Cleaned up temp file: {os.path.basename(path)}")
            except OSError:
                pass


@app.route("/api/lipsync", methods=["POST"])
def api_lipsync():
    mode = (request.form.get("mode") or "wav2lip").strip().lower()
    if mode not in {"wav2lip", "sadtalker"}:
        return jsonify({"error": "mode must be 'wav2lip' or 'sadtalker'"}), 400

    job_id = _new_job(mode)
    cleanup_paths = []

    # ---- Face input: a video (wav2lip) or an image (sadtalker) ---------------
    face_file = request.files.get("video") or request.files.get("image") or request.files.get("face")
    if face_file is None or not face_file.filename:
        return jsonify({"error": "Please provide a video (Lip Sync) or photo (Avatar)."}), 400

    face_ext = _ext(face_file.filename)
    if mode == "wav2lip" and face_ext not in ALLOWED_VIDEO and face_ext not in ALLOWED_IMAGE:
        return jsonify({"error": f"Unsupported face file type: {face_ext}"}), 400
    if mode == "sadtalker" and face_ext not in ALLOWED_IMAGE:
        return jsonify({"error": "Photo Avatar mode needs an image (jpg/png)."}), 400

    face_stem = _safe_stem(face_file.filename, "face")
    face_path = os.path.join(UPLOAD_DIR, f"{job_id}_{face_stem}{face_ext}")
    face_file.save(face_path)
    cleanup_paths.append(face_path)

    # ---- Audio input: an uploaded file OR a reused TTS output ----------------
    audio_path = None
    reuse_name = (request.form.get("audio_filename") or "").strip()
    audio_file = request.files.get("audio")

    if audio_file is not None and audio_file.filename:
        audio_ext = _ext(audio_file.filename)
        if audio_ext not in ALLOWED_AUDIO:
            return jsonify({"error": f"Unsupported audio file type: {audio_ext}"}), 400
        audio_stem = _safe_stem(audio_file.filename, "audio")
        audio_path = os.path.join(UPLOAD_DIR, f"{job_id}_{audio_stem}{audio_ext}")
        audio_file.save(audio_path)
        cleanup_paths.append(audio_path)
    elif reuse_name:
        # Reuse audio generated by the Voice Studio (lives in outputs/).
        safe = os.path.basename(reuse_name)
        candidate = os.path.join(OUTPUT_DIR, safe)
        if not os.path.exists(candidate):
            return jsonify({"error": "Generated audio not found on server. Re-generate it first."}), 400
        audio_path = candidate  # not added to cleanup -> stays reusable
    else:
        return jsonify({"error": "Please provide an audio file or use generated speech."}), 400

    # ---- Kick off the background worker --------------------------------------
    worker = threading.Thread(
        target=_process_job,
        args=(job_id, mode, face_path, audio_path, cleanup_paths),
        daemon=True,
    )
    worker.start()

    return jsonify({"success": True, "job_id": job_id, "status": "queued"})


@app.route("/api/status/<job_id>")
def api_status(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return jsonify({"error": "Unknown job id"}), 404
        # Return a shallow copy so we don't leak the lock-guarded object.
        return jsonify(
            {
                "id": job["id"],
                "mode": job["mode"],
                "status": job["status"],
                "logs": list(job["logs"]),
                "output_url": job["output_url"],
                "error": job["error"],
            }
        )


# --------------------------------------------------------------------------- #
# Entrypoint
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Hosts like Render inject $PORT to bind to; default to 5000 for local dev.
    port = int(os.environ.get("PORT", 5000))
    # Run the debug reloader only locally (when no $PORT is provided).
    debug = "PORT" not in os.environ
    print("=" * 60)
    print(" AI VoiceOver & Talking Avatar Studio")
    print("=" * 60)
    print(f" edge-tts available : {EDGE_TTS_AVAILABLE}")
    print(f" ffmpeg on PATH     : {shutil.which('ffmpeg') is not None}")
    print(f" Wav2Lip checkpoint : {os.path.exists(WAV2LIP_CHECKPOINT)}")
    print(f" SadTalker present  : {os.path.isdir(SADTALKER_DIR)}")
    print(f" Open http://localhost:{port}")
    print("=" * 60)
    app.run(host="0.0.0.0", port=port, debug=debug, threaded=True)
