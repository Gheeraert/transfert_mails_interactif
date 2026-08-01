#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Whisper GUI (Tkinter) — extraction/transcription audio/vidéo avec auto-détection GPU.
- Vérifie ffmpeg, openai-whisper, torch
- Détecte CUDA + VRAM (si dispo) et suggère un modèle par défaut
- Interface Tkinter : choix fichier, dossier de sortie, modèle, langue, device, formats
- Exports : .txt, .srt, .vtt, .json (optionnel)
"""

from __future__ import annotations

import os
import sys
import json
import time
import shutil
import threading
import traceback
from pathlib import Path
from dataclasses import dataclass
from queue import Queue, Empty

import tkinter as tk
from tkinter import ttk, filedialog, messagebox


# ---------------------------
# Dépendances / matériel
# ---------------------------

@dataclass
class HardwareInfo:
    ffmpeg_path: str | None
    whisper_ok: bool
    torch_ok: bool
    cuda_ok: bool
    gpu_name: str | None
    vram_gb: float | None
    torch_version: str | None
    cuda_version: str | None


def detect_hardware() -> HardwareInfo:
    ffmpeg_path = shutil.which("ffmpeg")

    whisper_ok = False
    try:
        import whisper  # noqa: F401
        whisper_ok = True
    except Exception:
        whisper_ok = False

    torch_ok = False
    cuda_ok = False
    gpu_name = None
    vram_gb = None
    torch_version = None
    cuda_version = None

    try:
        import torch  # noqa: F401
        torch_ok = True
        torch_version = getattr(torch, "__version__", None)
        cuda_version = getattr(torch.version, "cuda", None)

        cuda_ok = bool(torch.cuda.is_available())
        if cuda_ok:
            try:
                gpu_name = torch.cuda.get_device_name(0)
                props = torch.cuda.get_device_properties(0)
                vram_gb = round(props.total_memory / (1024**3), 2)
            except Exception:
                gpu_name = "CUDA device (unknown)"
                vram_gb = None
    except Exception:
        torch_ok = False

    return HardwareInfo(
        ffmpeg_path=ffmpeg_path,
        whisper_ok=whisper_ok,
        torch_ok=torch_ok,
        cuda_ok=cuda_ok,
        gpu_name=gpu_name,
        vram_gb=vram_gb,
        torch_version=torch_version,
        cuda_version=cuda_version,
    )


def suggest_default_model(hw: HardwareInfo) -> str:
    """
    Heuristique simple :
    - GPU >= ~10GB : large
    - GPU 6–10GB  : medium
    - GPU < 6GB   : small
    - CPU         : small (ou base si machine lente)
    """
    if hw.cuda_ok and hw.vram_gb is not None:
        if hw.vram_gb >= 10:
            return "large"
        if hw.vram_gb >= 6:
            return "medium"
        return "small"
    # CPU
    return "small"


def pick_device(hw: HardwareInfo, choice: str) -> str:
    """
    choice: "auto" | "cuda" | "cpu"
    """
    choice = (choice or "auto").lower()
    if choice == "cuda":
        return "cuda" if hw.cuda_ok else "cpu"
    if choice == "cpu":
        return "cpu"
    # auto
    return "cuda" if hw.cuda_ok else "cpu"

def is_heavy_model(name: str) -> bool:
    n = (name or "").lower()
    return n in {"medium", "large", "large-v2", "large-v3"}

# ---------------------------
# Exports (SRT / VTT)
# ---------------------------

def _format_timestamp_srt(seconds: float) -> str:
    # hh:mm:ss,mmm
    if seconds < 0:
        seconds = 0
    ms = int(round(seconds * 1000.0))
    h = ms // 3_600_000
    ms -= h * 3_600_000
    m = ms // 60_000
    ms -= m * 60_000
    s = ms // 1_000
    ms -= s * 1_000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _format_timestamp_vtt(seconds: float) -> str:
    # hh:mm:ss.mmm
    if seconds < 0:
        seconds = 0
    ms = int(round(seconds * 1000.0))
    h = ms // 3_600_000
    ms -= h * 3_600_000
    m = ms // 60_000
    ms -= m * 60_000
    s = ms // 1_000
    ms -= s * 1_000
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def write_txt(out_path: Path, text: str) -> None:
    out_path.write_text((text or "").strip() + "\n", encoding="utf-8")


def write_json(out_path: Path, data: dict) -> None:
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_srt(out_path: Path, segments: list[dict]) -> None:
    lines = []
    for i, seg in enumerate(segments, start=1):
        start = _format_timestamp_srt(float(seg.get("start", 0.0)))
        end = _format_timestamp_srt(float(seg.get("end", 0.0)))
        text = (seg.get("text") or "").strip()
        lines.append(str(i))
        lines.append(f"{start} --> {end}")
        lines.append(text)
        lines.append("")  # blank line
    out_path.write_text("\n".join(lines), encoding="utf-8")


def write_vtt(out_path: Path, segments: list[dict]) -> None:
    lines = ["WEBVTT", ""]
    for seg in segments:
        start = _format_timestamp_vtt(float(seg.get("start", 0.0)))
        end = _format_timestamp_vtt(float(seg.get("end", 0.0)))
        text = (seg.get("text") or "").strip()
        lines.append(f"{start} --> {end}")
        lines.append(text)
        lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------
# Tkinter App
# ---------------------------

class WhisperGUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Whisper — Transcription (GUI)")
        self.geometry("980x640")

        self.queue: Queue[tuple[str, str]] = Queue()
        self.worker_thread: threading.Thread | None = None
        self.stop_spinner = False
        self.busy_win = None
        self.busy_pb = None

        # Détection hardware
        self.hw = detect_hardware()

        # Variables UI
        self.input_path = tk.StringVar(value="")
        self.output_dir = tk.StringVar(value="")
        self.base_name = tk.StringVar(value="")  # si vide -> stem de l'input

        self.device_choice = tk.StringVar(value="auto")  # auto/cuda/cpu
        self.model_choice = tk.StringVar(value=suggest_default_model(self.hw))
        self.language = tk.StringVar(value="fr")  # "" = auto
        self.task = tk.StringVar(value="transcribe")  # transcribe/translate
        self.prompt_text_safe = None  # Variable tampon pour le thread

        # Verbosité : pour l’API python, True imprime segments ; None tente une barre tqdm (souvent moche en GUI)
        self.verbose_mode = tk.StringVar(value="log")  # quiet/log
        self.fp16 = tk.BooleanVar(value=True)  # auto-ajusté selon device au lancement

        self.out_txt = tk.BooleanVar(value=True)
        self.out_srt = tk.BooleanVar(value=True)
        self.out_vtt = tk.BooleanVar(value=False)
        self.out_json = tk.BooleanVar(value=False)

        # Cache modèle (évite de recharger si même modèle/device)
        self._model_cache: dict[tuple[str, str], object] = {}

        self._build_ui()
        self._render_hw_banner()
        self.after(100, self._poll_queue)

    def _busy_on(self, title: str, message: str) -> None:
        if self.busy_win is not None:
            return

        win = tk.Toplevel(self)
        win.title(title)
        win.transient(self)
        win.grab_set()
        win.resizable(False, False)

        ttk.Label(win, text=message, padding=12, justify="left").pack(fill="x")

        pb = ttk.Progressbar(win, mode="indeterminate", length=420)
        pb.pack(padx=12, pady=(0, 12))
        pb.start(10)

        # placement centré (simple)
        self.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() // 2) - 240
        y = self.winfo_rooty() + (self.winfo_height() // 2) - 70
        win.geometry(f"+{x}+{y}")

        self.busy_win = win
        self.busy_pb = pb

    def _busy_off(self) -> None:
        if self.busy_win is None:
            return
        try:
            if self.busy_pb is not None:
                self.busy_pb.stop()
        finally:
            try:
                self.busy_win.grab_release()
            except Exception:
                pass
            self.busy_win.destroy()
            self.busy_win = None
            self.busy_pb = None

    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 6}

        # Bandeau infos
        self.info = ttk.Label(self, text="", justify="left")
        self.info.pack(fill="x", **pad)

        # Cadre fichiers
        files = ttk.LabelFrame(self, text="Fichiers")
        files.pack(fill="x", **pad)

        row = ttk.Frame(files)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="Entrée (audio/vidéo) :").pack(side="left")
        ttk.Entry(row, textvariable=self.input_path).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(row, text="Choisir…", command=self._choose_input).pack(side="left")

        row = ttk.Frame(files)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="Dossier de sortie :").pack(side="left")
        ttk.Entry(row, textvariable=self.output_dir).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(row, text="Choisir…", command=self._choose_output_dir).pack(side="left")

        row = ttk.Frame(files)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="Nom de base (optionnel) :").pack(side="left")
        ttk.Entry(row, textvariable=self.base_name, width=30).pack(side="left", padx=6)
        ttk.Label(row, text="(si vide : nom du fichier d’entrée)").pack(side="left")

        # Cadre paramètres
        params = ttk.LabelFrame(self, text="Paramètres")
        params.pack(fill="x", **pad)

        grid = ttk.Frame(params)
        grid.pack(fill="x", **pad)

        ttk.Label(grid, text="Modèle :").grid(row=0, column=0, sticky="w")
        model_box = ttk.Combobox(
            grid,
            textvariable=self.model_choice,
            values=["tiny", "base", "small", "medium", "large", "large-v2", "large-v3"],
            state="readonly",
            width=12,
        )
        model_box.grid(row=0, column=1, sticky="w", padx=6)

        ttk.Label(grid, text="Device :").grid(row=0, column=2, sticky="w")
        dev_box = ttk.Combobox(grid, textvariable=self.device_choice, values=["auto", "cuda", "cpu"], state="readonly", width=10)
        dev_box.grid(row=0, column=3, sticky="w", padx=6)

        ttk.Label(grid, text="Langue :").grid(row=0, column=4, sticky="w")
        ttk.Entry(grid, textvariable=self.language, width=8).grid(row=0, column=5, sticky="w", padx=6)
        ttk.Label(grid, text="(ex: fr, en ; vide = auto)").grid(row=0, column=6, sticky="w")

        ttk.Label(grid, text="Tâche :").grid(row=1, column=0, sticky="w", pady=4)
        task_box = ttk.Combobox(grid, textvariable=self.task, values=["transcribe", "translate"], state="readonly", width=12)
        task_box.grid(row=1, column=1, sticky="w", padx=6)

        ttk.Label(grid, text="Console/log :").grid(row=1, column=2, sticky="w")
        vb = ttk.Combobox(grid, textvariable=self.verbose_mode, values=["quiet", "log"], state="readonly", width=10)
        vb.grid(row=1, column=3, sticky="w", padx=6)

        ttk.Checkbutton(grid, text="FP16 (GPU)", variable=self.fp16).grid(row=1, column=4, sticky="w", padx=6)

        # Cadre Contexte / Prompt
        ctx_frame = ttk.LabelFrame(self, text="Contexte / Vocabulaire (Optionnel)")
        ctx_frame.pack(fill="x", **pad)

        lbl_hint = ttk.Label(
            ctx_frame,
            text="Mots-clés ou phrases pour guider le style et l'orthographe (ex: Port-Royal, Astrée, XVIIe).",
            font=("Segoe UI", 8, "italic")  # Police petite pour ne pas encombrer
        )
        lbl_hint.pack(anchor="w", padx=6, pady=(2, 0))

        self.txt_prompt = tk.Text(ctx_frame, height=2, width=80, wrap="word")
        self.txt_prompt.pack(fill="x", padx=6, pady=6)

        # Formats
        fmts = ttk.Frame(params)
        fmts.pack(fill="x", **pad)
        ttk.Label(fmts, text="Sorties :").pack(side="left")
        ttk.Checkbutton(fmts, text="TXT", variable=self.out_txt).pack(side="left", padx=6)
        ttk.Checkbutton(fmts, text="SRT", variable=self.out_srt).pack(side="left", padx=6)
        ttk.Checkbutton(fmts, text="VTT", variable=self.out_vtt).pack(side="left", padx=6)
        ttk.Checkbutton(fmts, text="JSON (détails)", variable=self.out_json).pack(side="left", padx=6)

        # Actions
        actions = ttk.Frame(self)
        actions.pack(fill="x", **pad)

        self.btn_start = ttk.Button(actions, text="Transcrire", command=self._start)
        self.btn_start.pack(side="left")

        self.status = ttk.Label(actions, text="Prêt.")
        self.status.pack(side="left", padx=12)

        ttk.Button(actions, text="Re-détecter matériel", command=self._refresh_hw).pack(side="right")

        # Log
        logs = ttk.LabelFrame(self, text="Journal")
        logs.pack(fill="both", expand=True, **pad)

        self.log_text = tk.Text(logs, height=18, wrap="word")
        self.log_text.pack(fill="both", expand=True, padx=8, pady=8)

        # Scrollbar
        sb = ttk.Scrollbar(self.log_text, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")

    def _render_hw_banner(self) -> None:
        parts = []
        parts.append(f"ffmpeg: {'OK' if self.hw.ffmpeg_path else 'NON trouvé'}")
        parts.append(f"openai-whisper: {'OK' if self.hw.whisper_ok else 'NON importable'}")
        parts.append(f"torch: {'OK' if self.hw.torch_ok else 'NON importable'}")
        if self.hw.torch_ok:
            parts.append(f"torch={self.hw.torch_version}")
            parts.append(f"cuda_build={self.hw.cuda_version}")
        if self.hw.cuda_ok:
            parts.append(f"GPU: {self.hw.gpu_name or 'CUDA'} ({self.hw.vram_gb or '?'} GB)")
        else:
            parts.append("GPU: CPU (CUDA indisponible)")
        self.info.configure(text=" | ".join(parts))

    def log(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{ts}] {msg}\n")
        self.log_text.see("end")

    def _choose_input(self) -> None:
        p = filedialog.askopenfilename(
            title="Choisir un fichier audio/vidéo",
            filetypes=[
                ("Médias", "*.wav *.mp3 *.m4a *.flac *.ogg *.mp4 *.mkv *.mov *.webm *.aac"),
                ("Tous les fichiers", "*.*"),
            ],
        )
        if p:
            self.input_path.set(p)
            if not self.output_dir.get():
                self.output_dir.set(str(Path(p).parent))

    def _choose_output_dir(self) -> None:
        p = filedialog.askdirectory(title="Choisir un dossier de sortie")
        if p:
            self.output_dir.set(p)

    def _refresh_hw(self) -> None:
        self.hw = detect_hardware()
        self.model_choice.set(suggest_default_model(self.hw))
        self._render_hw_banner()
        self.log("Matériel/dépendances re-détectés.")

    def _start(self) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showinfo("Whisper", "Une transcription est déjà en cours.")
            return

        in_path = Path(self.input_path.get().strip())
        out_dir = Path(self.output_dir.get().strip()) if self.output_dir.get().strip() else None
        raw_prompt = self.txt_prompt.get("1.0", "end-1c").strip()
        self.prompt_text_safe = raw_prompt if raw_prompt else None

        if not in_path.exists():
            messagebox.showerror("Whisper", "Fichier d’entrée introuvable.")
            return
        if out_dir is None or not out_dir.exists():
            messagebox.showerror("Whisper", "Dossier de sortie invalide.")
            return

        # Checks minimaux
        if not self.hw.ffmpeg_path:
            messagebox.showerror("Whisper", "ffmpeg n’est pas trouvé dans le PATH. Whisper ne pourra pas décoder l’entrée.")
            return
        if not self.hw.whisper_ok:
            messagebox.showerror("Whisper", "Le module openai-whisper n’est pas importable. Installe-le : pip install openai-whisper")
            return
        if not self.hw.torch_ok:
            messagebox.showerror("Whisper", "PyTorch n’est pas importable. Installe torch (CUDA si souhaité).")
            return

        model_name = self.model_choice.get().strip().lower()
        dev_choice = self.device_choice.get().strip().lower()

        # Warning GPU : si l'utilisateur veut CUDA (ou auto) mais que CUDA n'est pas dispo
        if (dev_choice in {"auto", "cuda"}) and (not self.hw.cuda_ok):
            msg = (
                "CUDA (GPU) n'est pas disponible dans cet environnement.\n\n"
                "La transcription se fera sur CPU, ce qui peut être très lent, "
                "surtout avec les modèles medium/large.\n\n"
                "Conseils :\n"
                "• vérifier l'installation PyTorch CUDA\n"
                "• ou choisir un modèle plus léger (small/base)\n"
                "• ou forcer Device=CPU si c'est volontaire"
            )
            if is_heavy_model(model_name):
                msg += "\n\n⚠️ Modèle sélectionné : " + model_name + " (risque de lenteur importante sur CPU)."

            # On propose d'annuler si l'utilisateur ne s'y attendait pas
            if not messagebox.askokcancel("Whisper — GPU indisponible", msg):
                return

        self.btn_start.configure(state="disabled")
        self.status.configure(text="Transcription en cours…")
        self.log("Démarrage transcription…")

        self.stop_spinner = False
        self.worker_thread = threading.Thread(target=self._worker, daemon=True)
        self.worker_thread.start()
        self.after(200, self._spinner_tick)

    def _spinner_tick(self) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            # petit spinner “vivant”
            cur = self.status.cget("text")
            self.status.configure(text=cur + " ·" if len(cur) < 30 else "Transcription en cours…")
            self.after(350, self._spinner_tick)
        else:
            self.status.configure(text="Prêt.")

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "log":
                    self.log(payload)
                elif kind == "done":
                    self.log(payload)
                    self.btn_start.configure(state="normal")
                elif kind == "error":
                    self.log(payload)
                    self.btn_start.configure(state="normal")
                    messagebox.showerror("Whisper — erreur", payload)
                elif kind == "busy_on":
                    # payload = "titre|||message"
                    title, msg = payload.split("|||", 1)
                    self._busy_on(title, msg)
                elif kind == "busy_off":
                    self._busy_off()
        except Empty:
            pass
        self.after(120, self._poll_queue)

    def _worker(self) -> None:
        try:
            # Imports ici pour que les erreurs tombent dans le worker (et pas au lancement GUI)
            import whisper

            in_path = Path(self.input_path.get().strip())
            out_dir = Path(self.output_dir.get().strip())
            base = (self.base_name.get().strip() or in_path.stem).strip()

            model_name = self.model_choice.get().strip()
            device = pick_device(self.hw, self.device_choice.get().strip())
            if self.device_choice.get().strip().lower() in {"auto", "cuda"} and device == "cpu":
                self.queue.put(("log", "⚠️ CUDA indisponible : exécution sur CPU."))

            lang = self.language.get().strip()
            lang = lang if lang else None

            task = self.task.get().strip() or "transcribe"

            # Ajuste fp16 : seulement utile sur GPU
            fp16 = bool(self.fp16.get()) and (device == "cuda")

            initial_prompt_val = self.prompt_text_safe
            if initial_prompt_val:
                self.queue.put(("log", f"Prompt initial utilisé : « {initial_prompt_val} »"))

            self.queue.put(("log", f"Entrée: {in_path.name}"))
            self.queue.put(("log", f"Sortie: {out_dir} (base='{base}')"))
            self.queue.put(("log", f"Modèle: {model_name} | Device: {device} | Langue: {lang or 'auto'} | Tâche: {task} | fp16={fp16}"))

            # Cache modèle
            cache_key = (model_name, device)
            model = self._model_cache.get(cache_key)

            if model is None:
                self.queue.put(("log", "Chargement du modèle…"))

                self.queue.put((
                    "busy_on",
                    f"Whisper|||Chargement du modèle « {model_name} »…\n"
                    "(Si nécessaire, téléchargement automatique dans le cache)"
                ))
                try:
                    model = whisper.load_model(model_name)
                finally:
                    self.queue.put(("busy_off", ""))
                try:
                    model = model.to(device)
                except Exception:
                    # Certains environnements torch peuvent ne pas aimer .to("cuda") si CUDA non dispo
                    self.queue.put(("log", "Impossible de déplacer le modèle sur CUDA, bascule CPU."))
                    device = "cpu"
                    fp16 = False
                    cache_key = (model_name, device)
                self._model_cache[cache_key] = model
                self.queue.put(("log", "Modèle chargé."))
            else:
                self.queue.put(("log", "Modèle récupéré depuis le cache."))

            # Verbosité : en GUI, on préfère "quiet" puis on affiche les segments à la fin
            verbose_choice = self.verbose_mode.get().strip().lower()
            verbose_param = (verbose_choice != "quiet")

            self.queue.put(("log", "Transcription… (patience)"))
            t0 = time.time()
            result = model.transcribe(
                str(in_path),
                language=lang,
                task=task,
                fp16=fp16,
                verbose=verbose_param,
                initial_prompt=initial_prompt_val  # <--- ICI
            )
            dt = time.time() - t0
            self.queue.put(("log", f"Transcription terminée en {dt:.1f}s."))

            segments = result.get("segments", []) or []
            text = (result.get("text") or "").strip()

            # Affiche un aperçu segments (après coup, mais utile)
            if segments:
                self.queue.put(("log", f"{len(segments)} segments détectés. Aperçu :"))
                for s in segments[:10]:
                    st = float(s.get("start", 0.0))
                    en = float(s.get("end", 0.0))
                    tx = (s.get("text") or "").strip()
                    self.queue.put(("log", f"  [{st:6.1f} → {en:6.1f}] {tx}"))
                if len(segments) > 10:
                    self.queue.put(("log", "  …"))

            # Exports
            out_txt = out_dir / f"{base}.txt"
            out_srt = out_dir / f"{base}.srt"
            out_vtt = out_dir / f"{base}.vtt"
            out_json = out_dir / f"{base}.json"

            n_written = 0
            if self.out_txt.get():
                write_txt(out_txt, text)
                n_written += 1
                self.queue.put(("log", f"Écrit: {out_txt.name}"))
            if self.out_srt.get():
                write_srt(out_srt, segments)
                n_written += 1
                self.queue.put(("log", f"Écrit: {out_srt.name}"))
            if self.out_vtt.get():
                write_vtt(out_vtt, segments)
                n_written += 1
                self.queue.put(("log", f"Écrit: {out_vtt.name}"))
            if self.out_json.get():
                write_json(out_json, result)
                n_written += 1
                self.queue.put(("log", f"Écrit: {out_json.name}"))

            if n_written == 0:
                self.queue.put(("log", "Aucune sortie sélectionnée (rien écrit)."))

            self.queue.put(("done", "Terminé."))

        except Exception as e:
            tb = traceback.format_exc()
            self.queue.put(("error", f"{e}\n\n{tb}"))


def main() -> None:
    app = WhisperGUI()
    app.mainloop()


if __name__ == "__main__":
    main()