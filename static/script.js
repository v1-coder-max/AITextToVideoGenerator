/* =========================================================================
   AI VoiceOver & Talking Avatar Studio - frontend logic
   ========================================================================= */
(function () {
    "use strict";

    const $ = (sel) => document.querySelector(sel);
    const $$ = (sel) => Array.from(document.querySelectorAll(sel));

    // Shared state: the most recent TTS output, reusable in Avatar Studio.
    const state = {
        lastAudio: null,      // { filename, audio_url }
        reuseAudio: null,     // filename being reused for the next lipsync job
        mode: "wav2lip",
        polling: null,        // active setInterval handle for status polling
    };

    /* --------------------------------------------------------------------- */
    /* Console + toasts                                                       */
    /* --------------------------------------------------------------------- */
    const consoleEl = $("#console");

    function logLine(message, kind = "info") {
        const line = document.createElement("div");
        line.className = "l-" + kind;
        const stamp = new Date().toLocaleTimeString();
        line.textContent = `[${stamp}] ${message}`;
        consoleEl.appendChild(line);
        consoleEl.scrollTop = consoleEl.scrollHeight;
    }

    function logRaw(text) {
        // Color server log lines based on simple heuristics.
        let kind = "info";
        const lower = text.toLowerCase();
        if (lower.includes("error") || lower.includes("traceback") || lower.includes("failed")) kind = "err";
        else if (lower.includes("run:")) kind = "cmd";
        else if (lower.includes("success") || lower.includes("finished") || lower.includes("done")) kind = "ok";
        else if (lower.includes("warn")) kind = "warn";

        const line = document.createElement("div");
        line.className = "l-" + kind;
        line.textContent = text;
        consoleEl.appendChild(line);
        consoleEl.scrollTop = consoleEl.scrollHeight;
    }

    function toast(message, kind = "info") {
        const host = $("#toastHost");
        const el = document.createElement("div");
        el.className = "toast " + kind;
        el.textContent = message;
        host.appendChild(el);
        setTimeout(() => {
            el.style.opacity = "0";
            el.style.transition = "opacity .3s";
            setTimeout(() => el.remove(), 300);
        }, 4200);
    }

    $("#clearConsole").addEventListener("click", () => { consoleEl.innerHTML = ""; });

    /* --------------------------------------------------------------------- */
    /* Tabs                                                                   */
    /* --------------------------------------------------------------------- */
    $$(".tab").forEach((tab) => {
        tab.addEventListener("click", () => {
            $$(".tab").forEach((t) => t.classList.remove("active"));
            $$(".tab-panel").forEach((p) => p.classList.remove("active"));
            tab.classList.add("active");
            $("#panel-" + tab.dataset.tab).classList.add("active");
        });
    });

    function switchTab(name) {
        const tab = $(`.tab[data-tab="${name}"]`);
        if (tab) tab.click();
    }

    /* --------------------------------------------------------------------- */
    /* Backend health chips                                                   */
    /* --------------------------------------------------------------------- */
    async function loadHealth() {
        try {
            const res = await fetch("/api/health");
            const data = await res.json();
            $$("#envStatus .chip").forEach((chip) => {
                const ok = !!data[chip.dataset.key];
                chip.classList.toggle("ok", ok);
                chip.classList.toggle("bad", !ok);
                chip.title = ok ? "available" : "not detected";
            });
            // Recommend the Colab path when local rendering isn't ready.
            const localReady = !!data.ffmpeg && (!!data.wav2lip_ready || !!data.sadtalker_ready);
            $("#colabCard").classList.toggle("recommended", !localReady);
            if (!localReady) {
                $("#colabHint").innerHTML =
                    "Local rendering isn't set up (no ffmpeg / model checkpoint detected). " +
                    "Render the talking avatar on Colab's free GPU instead — grab the two files below and run the notebook.";
            }
        } catch (e) {
            logLine("Could not read backend health: " + e.message, "warn");
        }
    }

    /* Colab helper: keep the "Download audio" button pointed at the latest voice */
    function getActiveTtsAudio() {
        if (state.reuseAudio) return state.reuseAudio;
        if (state.lastAudio) return state.lastAudio.filename;
        return null;
    }
    function updateColabAudioBtn() {
        const btn = $("#colabAudioBtn");
        const name = getActiveTtsAudio();
        if (name) {
            btn.href = "/outputs/" + name;
            btn.setAttribute("download", name);
            btn.classList.remove("is-disabled");
            btn.title = "Download " + name;
        } else {
            btn.removeAttribute("href");
            btn.classList.add("is-disabled");
            btn.title = "Generate speech in Voice Studio first";
        }
    }
    $("#colabAudioBtn").addEventListener("click", (e) => {
        if (!getActiveTtsAudio()) {
            e.preventDefault();
            toast(
                "No generated audio yet — make it in Voice Studio. " +
                "(If you uploaded your own audio, it's already on your computer.)",
                "info"
            );
        }
    });

    /* --------------------------------------------------------------------- */
    /* Voice Studio                                                           */
    /* --------------------------------------------------------------------- */
    const voiceSelect = $("#voiceSelect");
    const voiceFilter = $("#voiceFilter");
    let allVoices = [];

    function renderVoices(filterText) {
        const f = (filterText || "").toLowerCase();
        const prev = voiceSelect.value;
        voiceSelect.innerHTML = "";
        const matched = allVoices.filter((v) => {
            if (!f) return true;
            return (
                (v.name && v.name.toLowerCase().includes(f)) ||
                (v.locale && v.locale.toLowerCase().includes(f)) ||
                (v.friendly && v.friendly.toLowerCase().includes(f))
            );
        });
        matched.forEach((v) => {
            const opt = document.createElement("option");
            opt.value = v.name;
            opt.textContent = `${v.name}  ·  ${v.gender || ""}`;
            voiceSelect.appendChild(opt);
        });
        if (!matched.length) {
            const opt = document.createElement("option");
            opt.textContent = "No voices match filter";
            opt.disabled = true;
            voiceSelect.appendChild(opt);
        }
        // Try to keep prior selection.
        if (prev && matched.some((v) => v.name === prev)) voiceSelect.value = prev;
    }

    async function loadVoices() {
        try {
            logLine("Loading available voices...");
            const res = await fetch("/api/voices");
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || "Failed to load voices");
            allVoices = data.voices || [];
            renderVoices("");
            // Default to en-US-JennyNeural if present.
            const jenny = allVoices.find((v) => v.name === "en-US-JennyNeural");
            if (jenny) voiceSelect.value = jenny.name;
            logLine(`Loaded ${allVoices.length} voices.`, "ok");
        } catch (e) {
            logLine("Voice load error: " + e.message, "err");
            toast("Could not load voices: " + e.message, "error");
        }
    }

    voiceFilter.addEventListener("input", (e) => renderVoices(e.target.value));

    // Sliders
    const rate = $("#rate"), pitch = $("#pitch");
    const rateVal = $("#rateVal"), pitchVal = $("#pitchVal");
    rate.addEventListener("input", () => {
        rateVal.textContent = (rate.value > 0 ? "+" : "") + rate.value + "%";
    });
    pitch.addEventListener("input", () => {
        pitchVal.textContent = (pitch.value > 0 ? "+" : "") + pitch.value + " Hz";
    });

    function setBusy(btn, busy) {
        btn.disabled = busy;
        btn.querySelector(".btn-label").classList.toggle("hidden", busy);
        btn.querySelector(".spinner").classList.toggle("hidden", !busy);
    }

    const genSpeechBtn = $("#generateSpeech");
    genSpeechBtn.addEventListener("click", async () => {
        const text = $("#ttsText").value.trim();
        if (!text) { toast("Please enter some text first.", "error"); return; }
        const voice = voiceSelect.value;
        if (!voice) { toast("Please pick a voice.", "error"); return; }

        setBusy(genSpeechBtn, true);
        logLine(`Generating speech with ${voice} (rate ${rate.value}%, pitch ${pitch.value}Hz)...`, "cmd");
        try {
            const res = await fetch("/api/tts", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    text, voice,
                    rate: parseInt(rate.value, 10),
                    pitch: parseInt(pitch.value, 10),
                }),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || "TTS failed");

            state.lastAudio = { filename: data.filename, audio_url: data.audio_url };
            const audio = $("#ttsAudio");
            audio.src = data.audio_url + "?t=" + Date.now();
            $("#downloadAudio").href = data.audio_url;
            $("#downloadAudio").setAttribute("download", data.filename);
            $("#ttsResult").classList.remove("hidden");
            updateColabAudioBtn();
            logLine("Speech generated: " + data.filename, "ok");
            toast("Speech generated successfully.", "success");
        } catch (e) {
            logLine("TTS error: " + e.message, "err");
            toast("Speech failed: " + e.message, "error");
        } finally {
            setBusy(genSpeechBtn, false);
        }
    });

    // Workflow: pass the generated audio straight into Avatar Studio.
    $("#useInAvatar").addEventListener("click", () => {
        if (!state.lastAudio) { toast("Generate speech first.", "error"); return; }
        applyReuseAudio(state.lastAudio.filename);
        switchTab("avatar");
        toast("Generated audio loaded into Avatar Studio.", "success");
    });

    /* --------------------------------------------------------------------- */
    /* Avatar Studio                                                          */
    /* --------------------------------------------------------------------- */
    const faceInput = $("#faceInput");
    const audioInput = $("#audioInput");
    const faceDrop = $("#faceDrop");
    const audioDrop = $("#audioDrop");

    // Mode toggle (wav2lip vs sadtalker)
    $$(".mode-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
            $$(".mode-btn").forEach((b) => b.classList.remove("active"));
            btn.classList.add("active");
            state.mode = btn.dataset.mode;
            const isVideo = state.mode === "wav2lip";
            $("#faceLabel").textContent = isVideo ? "Face video (mp4)" : "Face photo (jpg/png)";
            $("#faceDzText").textContent = isVideo
                ? "Click to choose a video file"
                : "Click to choose a photo";
            $("#faceDrop .dz-icon").textContent = isVideo ? "🎬" : "🖼️";
            faceInput.setAttribute("accept", isVideo ? "video/*" : "image/*");
            // Reset chosen face since the accepted type changed.
            faceInput.value = "";
            faceDrop.classList.remove("has-file");
            $("#faceDzText").textContent = isVideo
                ? "Click to choose a video file"
                : "Click to choose a photo";
        });
    });

    function wireDropzone(drop, input, textEl, onFile) {
        input.addEventListener("change", () => {
            if (input.files.length) {
                textEl.textContent = input.files[0].name;
                drop.classList.add("has-file");
                if (onFile) onFile(input.files[0]);
            }
        });
        ["dragenter", "dragover"].forEach((ev) =>
            drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("dragover"); })
        );
        ["dragleave", "drop"].forEach((ev) =>
            drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("dragover"); })
        );
        drop.addEventListener("drop", (e) => {
            if (e.dataTransfer.files.length) {
                input.files = e.dataTransfer.files;
                input.dispatchEvent(new Event("change"));
            }
        });
    }

    wireDropzone(faceDrop, faceInput, $("#faceDzText"));
    wireDropzone(audioDrop, audioInput, $("#audioDzText"), () => clearReuseAudio());

    // Reuse banner helpers
    function applyReuseAudio(filename) {
        state.reuseAudio = filename;
        $("#reuseName").textContent = filename;
        $("#reuseBanner").classList.remove("hidden");
        // Clear any manually selected audio file.
        audioInput.value = "";
        $("#audioDzText").textContent = "Click to choose an audio file";
        audioDrop.classList.remove("has-file");
        updateColabAudioBtn();
    }
    function clearReuseAudio() {
        state.reuseAudio = null;
        $("#reuseBanner").classList.add("hidden");
        updateColabAudioBtn();
    }
    $("#clearReuse").addEventListener("click", clearReuseAudio);

    // Generate video
    const genVideoBtn = $("#generateVideo");
    const statusPill = $("#statusPill");

    function setPill(status) {
        statusPill.textContent = status;
        statusPill.className = "status-pill " + status;
        statusPill.classList.remove("hidden");
    }

    genVideoBtn.addEventListener("click", async () => {
        if (!faceInput.files.length) {
            toast(state.mode === "wav2lip" ? "Choose a video first." : "Choose a photo first.", "error");
            return;
        }
        const hasUploadAudio = audioInput.files.length > 0;
        if (!hasUploadAudio && !state.reuseAudio) {
            toast("Add an audio file or use generated speech.", "error");
            return;
        }

        const form = new FormData();
        form.append("mode", state.mode);
        form.append(state.mode === "wav2lip" ? "video" : "image", faceInput.files[0]);
        if (hasUploadAudio) {
            form.append("audio", audioInput.files[0]);
        } else {
            form.append("audio_filename", state.reuseAudio);
        }

        setBusy(genVideoBtn, true);
        $("#videoResult").classList.add("hidden");
        setPill("queued");
        logLine(`Submitting ${state.mode} job...`, "cmd");

        try {
            const res = await fetch("/api/lipsync", { method: "POST", body: form });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || "Job submission failed");
            logLine("Job queued: " + data.job_id, "ok");
            pollStatus(data.job_id);
        } catch (e) {
            logLine("Lipsync error: " + e.message, "err");
            toast("Failed: " + e.message, "error");
            setBusy(genVideoBtn, false);
            setPill("error");
        }
    });

    let lastLogCount = 0;
    function pollStatus(jobId) {
        if (state.polling) clearInterval(state.polling);
        lastLogCount = 0;

        state.polling = setInterval(async () => {
            try {
                const res = await fetch("/api/status/" + jobId);
                const data = await res.json();
                if (!res.ok) throw new Error(data.error || "Status check failed");

                // Stream only new log lines.
                if (Array.isArray(data.logs) && data.logs.length > lastLogCount) {
                    for (let i = lastLogCount; i < data.logs.length; i++) logRaw(data.logs[i]);
                    lastLogCount = data.logs.length;
                }
                setPill(data.status);

                if (data.status === "done") {
                    clearInterval(state.polling);
                    state.polling = null;
                    setBusy(genVideoBtn, false);
                    const video = $("#resultVideo");
                    video.src = data.output_url + "?t=" + Date.now();
                    $("#downloadVideo").href = data.output_url;
                    $("#videoResult").classList.remove("hidden");
                    logLine("Video ready: " + data.output_url, "ok");
                    toast("Video generated successfully.", "success");
                } else if (data.status === "error") {
                    clearInterval(state.polling);
                    state.polling = null;
                    setBusy(genVideoBtn, false);
                    logLine("Job failed: " + (data.error || "unknown error"), "err");
                    toast(data.error || "Video generation failed.", "error");
                }
            } catch (e) {
                clearInterval(state.polling);
                state.polling = null;
                setBusy(genVideoBtn, false);
                setPill("error");
                logLine("Polling error: " + e.message, "err");
            }
        }, 1500);
    }

    /* --------------------------------------------------------------------- */
    /* Init                                                                   */
    /* --------------------------------------------------------------------- */
    logLine("Studio ready.", "ok");
    updateColabAudioBtn();
    loadHealth();
    loadVoices();
})();
