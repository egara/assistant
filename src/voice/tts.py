"""Text-to-Speech (TTS) Worker Thread and Audio playback helpers for Assistant."""

import logging
import os
import subprocess
from typing import Any, Dict, Optional

from langdetect import detect
from langdetect.lang_detect_exception import LangDetectException
from PyQt6.QtCore import QThread, pyqtSignal

logger = logging.getLogger("assistant.voice.tts")


class TTSWorkerThread(QThread):
    """Worker thread generating and streaming Text-To-Speech audio via Edge-TTS or Piper."""

    status_signal = pyqtSignal(str, str)
    finished_signal = pyqtSignal()

    def __init__(self, text: str, voice_config: Optional[Dict[str, Any]] = None, temp_dir: str = "/tmp/assistant"):
        super().__init__()
        self.text: str = text
        self.voice_config: Dict[str, Any] = voice_config or {}
        self.temp_dir: str = temp_dir
        self._is_stopped: bool = False
        self.current_process: Optional[subprocess.Popen] = None

    def run(self) -> None:
        engine_type = self.voice_config.get("tts_engine", "online")
        if engine_type in ("none", "disabled", "off", None):
            self.finished_signal.emit()
            return

        audio_path = os.path.join(self.temp_dir, "tts.wav")
        try:
            detected_lang = detect(self.text)
        except (LangDetectException, Exception):
            detected_lang = "en"

        if self._is_stopped:
            return

        if engine_type == "online":
            self.status_signal.emit("󰓃 Speaking...", "#59c2ff")
            voice = self.voice_config.get("tts_online_voices", {}).get(
                detected_lang, "en-US-GuyNeural"
            )
            self.current_process = subprocess.Popen(
                [
                    "edge-tts",
                    "--voice",
                    voice,
                    "--rate",
                    "+5%",
                    "--text",
                    self.text,
                    "--write-media",
                    audio_path,
                ],
                stderr=subprocess.DEVNULL,
            )
            if self.current_process:
                self.current_process.wait()
        else:
            local_models = self.voice_config.get("tts_local_models", {})
            model = os.path.expanduser(
                local_models.get(
                    detected_lang,
                    list(local_models.values())[0] if local_models else "",
                )
            )

            if os.path.exists(model):
                self.status_signal.emit("󰓃 Speaking...", "#59c2ff")
                self.current_process = subprocess.Popen(
                    ["piper", "--model", model, "--output_file", audio_path],
                    stdin=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
                self.current_process.communicate(input=self.text.encode("utf-8"))
            else:
                self.status_signal.emit("󰀦 Missing Voice Model", "#f07178")
                logger.error(
                    f"Voice model {model} not found. Please verify your Nix Flake or install.sh downloaded it."
                )

        if self._is_stopped:
            return

        if os.path.exists(audio_path):
            self.current_process = subprocess.Popen(
                ["pw-play", audio_path], stderr=subprocess.DEVNULL
            )
            self.current_process.wait()

        if not self._is_stopped:
            self.finished_signal.emit()

    def stop(self) -> None:
        """Interrupt any running TTS conversion or playback."""
        self._is_stopped = True
        if self.current_process:
            try:
                self.current_process.terminate()
            except OSError:
                pass


def stop_all_media_playback() -> None:
    """Kill active media player and TTS processes cleanly."""
    subprocess.run(["pkill", "-x", "pw-play"], stderr=subprocess.DEVNULL)
    subprocess.run(["pkill", "-x", "piper"], stderr=subprocess.DEVNULL)
    subprocess.run(["pkill", "-x", "edge-tts"], stderr=subprocess.DEVNULL)
