# 🎙️ AI VoiceOver & Talking Avatar Studio

A local full-stack studio that turns text into natural narration (Microsoft
**edge-tts**) and then makes a face *speak* that narration — either by
lip-syncing a video clip (**Wav2Lip**) or animating a single photo
(**SadTalker**).

- **Frontend:** plain HTML / CSS / JavaScript (single page, no framework)
- **Backend:** Python + Flask
- **Runs locally** on <http://localhost:5000>

```bash
git clone https://github.com/v1-coder-max/AITextToVideoGenerator.git
cd AITextToVideoGenerator
pip install -r requirements.txt
python app.py
```

**Or deploy a live web version (free):**

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/v1-coder-max/AITextToVideoGenerator)

→ See [**Deploy to Render**](#-deploy-to-render-free) below. (Voice Studio runs
live; Avatar rendering stays on Colab.)

---

## ✨ Features

### Voice Studio (Text → Speech)
- Large script editor + searchable voice picker (all edge-tts voices)
- Speed & pitch sliders
- Instant in-browser preview and **Download MP3**
- **Use in Avatar Studio** — sends the generated audio straight to the avatar
  stage, no re-upload needed

### Avatar Studio (Audio → Talking Video)
- **Lip Sync** mode → upload a video, drive it with audio (Wav2Lip)
- **Photo Avatar** mode → upload a single photo, animate it (SadTalker)
- Background job queue with live status (`queued → processing → done/error`)
- Streaming **console** showing the real subprocess logs
- In-browser preview and **Download MP4**

The two features work **independently** — you can use Voice Studio on its own,
or bring your own audio to Avatar Studio.

---

## 📦 Folder structure

```
AIVoiceOverProject/
├── app.py               # Flask backend (TTS + lipsync APIs)
├── requirements.txt
├── templates/
│   └── index.html
├── static/
│   ├── style.css
│   └── script.js
├── uploads/             # temp input files (auto-cleaned after each job)
├── outputs/             # generated mp3 / mp4 files
├── colab/               # render avatars on a free Colab GPU (see below)
│   ├── AvatarStudio_Colab.ipynb
│   └── COLAB_GUIDE.md
├── Wav2Lip/             # cloned separately (see setup)
└── SadTalker/           # cloned separately (see setup)
```

---

## 🚀 Setup

> The **Voice Studio works immediately** after step 1 + 2.
> The **Avatar Studio** needs the extra model setup (steps 3–6) plus **ffmpeg**.
>
> 💡 **No big GPU?** Skip local model setup and render talking avatars on a free
> Colab GPU instead — see [`colab/COLAB_GUIDE.md`](colab/COLAB_GUIDE.md). You
> make the voice locally, then
> **[open the renderer notebook in Colab (one click)](https://colab.research.google.com/github/v1-coder-max/AITextToVideoGenerator/blob/main/colab/AvatarStudio_Colab.ipynb)**
> and upload your MP3 + a photo/video.

### 1. Core install (required for TTS)

```bash
pip install flask flask-cors edge-tts
# or:  pip install -r requirements.txt
```

### 2. Run it

```bash
python app.py
```

Open <http://localhost:5000>. On startup the server prints which capabilities it
detected (edge-tts, ffmpeg, Wav2Lip checkpoint, SadTalker). The same status is
shown as chips in the top-right of the UI.

---

### 3–6. Avatar Studio setup (optional, for video features)

**Install ffmpeg** and make sure it's on your `PATH`:
- Windows: `winget install Gyan.FFmpeg` (or download from <https://ffmpeg.org>)
- macOS: `brew install ffmpeg`
- Linux: `sudo apt install ffmpeg`

**Clone Wav2Lip** into `./Wav2Lip/`:

```bash
git clone https://github.com/Rudrabha/Wav2Lip
pip install -r Wav2Lip/requirements.txt
```

**Download the checkpoint** and place it at
`./Wav2Lip/checkpoints/wav2lip.pth`
(see the model links in the Wav2Lip repo's README). You also need the
face-detection weights the repo asks for (`s3fd.pth`).

**Clone SadTalker** into `./SadTalker/` (for Photo Avatar mode):

```bash
git clone https://github.com/OpenTalker/SadTalker
pip install -r SadTalker/requirements.txt
# then download its checkpoints per the SadTalker README (bash scripts/download_models.sh)
```

Restart `python app.py` — the Wav2Lip / SadTalker chips should turn green.

---

## 🌐 Deploy to Render (free)

Get a public URL where **Voice Studio runs live** (Avatar rendering stays on
Colab — Render's free tier has no GPU). The repo includes a
[`render.yaml`](render.yaml) Blueprint, so it's a few clicks:

1. Click the **Deploy to Render** button at the top of this README — *or* go to
   <https://dashboard.render.com> → **New → Blueprint**.
2. Connect your GitHub and pick the **AITextToVideoGenerator** repo.
3. Render reads `render.yaml` and proposes a free web service. Click
   **Apply / Create**.
4. Wait for the build (`pip install -r requirements.txt`) and first boot
   (`gunicorn app:app …`). You'll get a URL like
   `https://ai-voiceover-studio.onrender.com`.

**What runs there:** edge-tts speech generation, voice list, preview, and MP3
download. The Avatar Studio shows the "Render on Colab" path (no GPU on the free
tier).

**Free-tier notes:**
- The service **sleeps after ~15 min idle**; the next visit cold-starts in
  ~50 sec. That's normal.
- The filesystem is **ephemeral** — generated MP3s live only until the next
  restart/redeploy (fine for on-the-fly use; download anything you want to keep).

---

## 🔌 API reference

| Method | Endpoint              | Purpose |
|--------|-----------------------|---------|
| `GET`  | `/api/voices`         | List all edge-tts voices |
| `POST` | `/api/tts`            | `{ text, voice, rate, pitch }` → `{ audio_url, filename }` |
| `POST` | `/api/lipsync`        | multipart: `mode`, `video`\|`image`, `audio`\|`audio_filename` → `{ job_id }` |
| `GET`  | `/api/status/<job_id>`| `{ status, logs[], output_url, error }` |
| `GET`  | `/api/health`         | capability flags |
| `GET`  | `/outputs/<file>`     | serves generated mp3/mp4 |

**`rate`** is a percentage in `-100..100`; **`pitch`** is Hz in `-50..50`.
Reusing TTS audio in a lipsync job: pass `audio_filename` (the filename returned
by `/api/tts`) instead of uploading an `audio` file — the server reads it from
`outputs/` so nothing is re-uploaded.

---

## 🧰 How the pipeline runs

- **Wav2Lip:**
  ```
  python Wav2Lip/inference.py \
    --checkpoint_path Wav2Lip/checkpoints/wav2lip.pth \
    --face uploads/<video> --audio <audio> --outfile outputs/lipsync_<job>.mp4
  ```
- **SadTalker:**
  ```
  python SadTalker/inference.py \
    --driven_audio <audio> --source_image uploads/<photo> --result_dir outputs/
  ```
  (the newest `.mp4` produced under `outputs/` is returned)

Each job runs in a background thread; subprocess output is streamed line-by-line
into the in-app console. Uploaded input files in `uploads/` are deleted after the
job finishes — generated TTS audio in `outputs/` is kept so it stays reusable.

---

## 🛠️ Troubleshooting

| Symptom | Fix |
|---|---|
| Voices won't load | edge-tts needs internet to query Microsoft's voice list. Check your connection / proxy. |
| `Wav2Lip` chip is grey | Checkpoint missing — confirm `Wav2Lip/checkpoints/wav2lip.pth` exists. |
| `ffmpeg` chip is grey | Install ffmpeg and ensure it's on `PATH`, then restart the server. |
| Job fails instantly | Open the console box — the real Python traceback from Wav2Lip/SadTalker is streamed there. |
| Torch/CUDA errors | Those come from Wav2Lip/SadTalker's own environment; follow their repo install notes (they may need a specific torch build). |

---

## ⚠️ Notes
- Wav2Lip and SadTalker have heavy ML dependencies (PyTorch, etc.) and may need
  a GPU for reasonable speed. This project only **orchestrates** them via
  `subprocess`; install them per their own repositories.
- Intended for **local use**. `debug=True` and an open CORS policy are convenient
  for development but should not be exposed publicly as-is.
