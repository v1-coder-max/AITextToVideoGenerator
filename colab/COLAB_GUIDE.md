# 🎬 Turning your voiceover into a talking avatar (Google Colab)

Your laptop's GPU (NVIDIA MX250, 2 GB) is too small to render these models
comfortably, so the reliable path is: **make the voice locally, render the
avatar on Colab's free GPU.**

```
 ┌─────────────────────────┐        ┌──────────────────────────────┐
 │  Local app (this repo)  │        │  Google Colab (free T4 GPU)  │
 │                         │        │                              │
 │  Voice Studio           │  MP3   │  AvatarStudio_Colab.ipynb    │
 │  type script ──► TTS  ──┼──────► │  upload audio + face ──► MP4 │
 └─────────────────────────┘download └──────────────────────────────┘
```

## Steps

1. **Generate the voice locally**
   - `python app.py` → open <http://localhost:5000>
   - **Voice Studio** → type your script → pick a voice → **Generate Speech**
   - Click **⬇ Download MP3** (this is the audio your avatar will speak)

2. **Get a face**
   - A **photo** (jpg/png) of a face → use *Option A (SadTalker)*, **or**
   - A short **face video** (mp4) → use *Option B (Wav2Lip)*
   - Not sure? **Wav2Lip works on a photo too** and is lighter — start there.

3. **Render on Colab**
   - **One click:** open the notebook straight from GitHub (or hit the
     **Open Colab ↗** button in the app):
     <https://colab.research.google.com/github/v1-coder-max/AITextToVideoGenerator/blob/main/colab/AvatarStudio_Colab.ipynb>
     *(the repo must be public for this link to load)*. No upload needed.
   - **Runtime → Change runtime type → GPU → Save.**
   - Run the cells top to bottom: Step 0, Step 1 (upload your MP3 + face), then
     **Option A** *or* **Option B**.
   - The last cell previews the video and **downloads the MP4** to your computer.

4. **(Optional) Use it back in the app**
   - The downloaded MP4 is your finished talking avatar — play/share it directly.
   - If you later set up Wav2Lip/SadTalker locally, the app's **Avatar Studio**
     does the exact same thing on your machine (see the main README).

## Which engine?

| | **SadTalker** (Option A) | **Wav2Lip** (Option B) |
|---|---|---|
| Input | one **photo** | **video** (or photo) |
| Motion | head + lips move | lips only |
| Speed / VRAM | heavier | lighter, faster |
| Best for | a portrait that "comes alive" | a real clip re-voiced |

## Why not render locally?

You *can* — the app's Avatar Studio runs the identical pipeline via `subprocess`.
But on a 2 GB GPU it will mostly fall back to **CPU** (several minutes per short
clip, and SadTalker may run out of memory). Colab's free GPU avoids that and
sidesteps the Windows/PyTorch dependency setup for Wav2Lip. Once you have a
machine with a larger GPU, follow the main **README** steps 3–6 to do it all
locally and skip Colab entirely.
