"""Surveillance tool for Assistant.

Manages periodic webcam surveillance via background worker thread, inspecting frames
with multimodal vision models to detect dynamic conditions (e.g. person, pet, movement)
and trigger actions (save to Downloads, desktop notification, voice alert).
"""

import base64
import glob
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger("assistant.tools.surveillance")

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "start_surveillance",
            "description": (
                "Activate background surveillance mode via webcam. Regularly captures frames "
                "and uses vision analysis to alert when a specific condition is met (e.g. "
                "'aparece una persona', 'un gato', 'alguien se acerca', 'paquete'). "
                "Saves triggered frames to Downloads and notifies the user."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "condition": {
                        "type": "string",
                        "description": "What to watch for (e.g. 'una persona', 'un perro o gato', 'movimiento extraño').",
                    },
                    "interval_seconds": {
                        "type": "integer",
                        "default": 5,
                        "description": "Interval between camera captures in seconds (minimum 3s, default 5s).",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["save_and_notify", "notify_only"],
                        "default": "save_and_notify",
                        "description": "Action when condition is triggered: save snapshot to ~/Downloads and show notification, or notify only.",
                    },
                },
                "required": ["condition"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stop_surveillance",
            "description": "Stop active background webcam surveillance mode.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_surveillance_status",
            "description": "Get current status, condition, and statistics of webcam surveillance.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
]


def _find_binary(cmd_name: str) -> Optional[str]:
    """Find binary executable in PATH or Nix store."""
    found = shutil.which(cmd_name)
    if found:
        return found
    for nix_bin in [
        f"/run/current-system/sw/bin/{cmd_name}",
        f"/etc/profiles/per-user/{os.environ.get('USER', 'root')}/bin/{cmd_name}",
    ]:
        if os.path.exists(nix_bin) and os.access(nix_bin, os.X_OK):
            return nix_bin

    store_matches = glob.glob(f"/nix/store/*{cmd_name}*/bin/{cmd_name}")
    if store_matches:
        for match in sorted(store_matches, reverse=True):
            if os.path.isfile(match) and os.access(match, os.X_OK):
                return match
    return None


def capture_webcam_frame(output_path: str, device: str = "/dev/video0") -> bool:
    """Capture a single frame from the webcam using ffmpeg or fswebcam."""
    ffmpeg_bin = _find_binary("ffmpeg")
    if ffmpeg_bin and os.path.exists(device):
        try:
            cmd = [
                ffmpeg_bin,
                "-y",
                "-f", "v4l2",
                "-i", device,
                "-vframes", "1",
                "-q:v", "3",
                output_path,
            ]
            res = subprocess.run(cmd, capture_output=True, timeout=5)
            if res.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                return True
        except (subprocess.SubprocessError, OSError) as e:
            logger.warning(f"ffmpeg frame capture failed: {e}")

    fswebcam_bin = _find_binary("fswebcam")
    if fswebcam_bin and os.path.exists(device):
        try:
            cmd = [fswebcam_bin, "-d", device, "-r", "640x480", "--no-banner", output_path]
            res = subprocess.run(cmd, capture_output=True, timeout=5)
            if res.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                return True
        except (subprocess.SubprocessError, OSError) as e:
            logger.warning(f"fswebcam capture failed: {e}")

    return False


class SurveillanceManager:
    """Singleton manager controlling the background surveillance thread."""

    _instance: Optional["SurveillanceManager"] = None

    def __new__(cls) -> "SurveillanceManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_state()
        return cls._instance

    def _init_state(self) -> None:
        self.is_active = False
        self.condition = ""
        self.interval = 5
        self.action = "save_and_notify"
        self.device = "/dev/video0"
        self.thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.frames_analyzed = 0
        self.triggers_detected = 0
        self.start_time: float = 0.0
        self.model_client: Optional[Any] = None
        self.model_name: Optional[str] = None
        self.on_trigger_callback: Optional[Callable[[str, str], None]] = None

    def configure_vision_client(
        self,
        client: Any,
        model_name: str,
        on_trigger_callback: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        """Register the vision client and notification callbacks."""
        self.model_client = client
        self.model_name = model_name
        self.on_trigger_callback = on_trigger_callback

    def start(
        self,
        condition: str,
        interval_seconds: int = 5,
        action: str = "save_and_notify",
        device: str = "/dev/video0",
    ) -> str:
        """Start background surveillance thread."""
        if self.is_active:
            return (
                f"El modo vigilancia ya está activo buscando: '{self.condition}'. "
                f"Usa stop_surveillance para detenerlo primero si deseas cambiar la condición."
            )

        if not os.path.exists(device):
            return f"Error: No se encontró el dispositivo de cámara {device}."

        ffmpeg_bin = _find_binary("ffmpeg") or _find_binary("fswebcam")
        if not ffmpeg_bin:
            return "Error: No se encontró ffmpeg ni fswebcam para capturar imágenes de la webcam."

        self.condition = condition.strip()
        self.interval = max(3, int(interval_seconds))
        self.action = action
        self.device = device
        self.frames_analyzed = 0
        self.triggers_detected = 0
        self.start_time = time.time()
        self.stop_event.clear()
        self.is_active = True

        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()

        logger.info(f"Surveillance started: condition='{self.condition}', interval={self.interval}s")
        return (
            f"Modo vigilancia activado correctamente. "
            f"Estaré analizando la cámara cada {self.interval} segundos buscando '{self.condition}'. "
            f"En cuanto lo detecte, te avisaré de inmediato."
        )

    def stop(self) -> str:
        """Stop background surveillance thread."""
        if not self.is_active:
            return "El modo vigilancia no estaba activo."

        self.stop_event.set()
        self.is_active = False
        duration_sec = int(time.time() - self.start_time) if self.start_time else 0
        msg = (
            f"Modo vigilancia detenido. Estuvo activo {duration_sec}s, "
            f"analizó {self.frames_analyzed} capturas y disparó {self.triggers_detected} alertas."
        )
        logger.info(msg)
        return msg

    def get_status(self) -> Dict[str, Any]:
        """Return current status dictionary."""
        return {
            "active": self.is_active,
            "condition": self.condition,
            "interval_seconds": self.interval,
            "action": self.action,
            "frames_analyzed": self.frames_analyzed,
            "triggers_detected": self.triggers_detected,
            "uptime_seconds": int(time.time() - self.start_time) if self.is_active else 0,
        }

    def _run_loop(self) -> None:
        """Background thread loop capturing frames and querying the vision LLM."""
        tmp_dir = "/tmp/assistant/surveillance"
        os.makedirs(tmp_dir, exist_ok=True)

        downloads_dir = os.path.expanduser("~/Downloads")

        while not self.stop_event.is_set():
            frame_path = os.path.join(tmp_dir, f"frame_{int(time.time())}.jpg")
            success = capture_webcam_frame(frame_path, device=self.device)

            if success and os.path.exists(frame_path):
                self.frames_analyzed += 1
                try:
                    is_triggered, description = self._analyze_frame(frame_path)
                    if is_triggered:
                        self.triggers_detected += 1
                        saved_path = ""
                        if "save" in self.action:
                            filename = f"vigilancia_{int(time.time())}.jpg"
                            saved_path = os.path.join(downloads_dir, filename)
                            try:
                                shutil.copy2(frame_path, saved_path)
                            except OSError as e:
                                logger.error(f"Failed to copy frame to Downloads: {e}")
                                saved_path = frame_path

                        self._notify_trigger(description, saved_path)
                        # After triggering, stop surveillance automatically or wait
                        self.is_active = False
                        self.stop_event.set()
                        try:
                            os.remove(frame_path)
                        except OSError:
                            pass
                        break
                except Exception as e:
                    logger.error(f"Surveillance vision analysis error: {e}")
                finally:
                    if os.path.exists(frame_path):
                        try:
                            os.remove(frame_path)
                        except OSError:
                            pass

            # Wait for next interval or until stopped
            if self.stop_event.wait(self.interval):
                break

    def _analyze_frame(self, frame_path: str) -> Tuple[bool, str]:
        """Send frame to vision model to check if surveillance condition is met."""
        if not self.model_client or not self.model_name:
            logger.warning("No vision model client registered for surveillance.")
            return False, ""

        with open(frame_path, "rb") as f:
            b64_data = base64.b64encode(f.read()).decode("utf-8")

        prompt = (
            f"You are an automated real-time security vision monitor.\n"
            f"Condition to detect: \"{self.condition}\"\n\n"
            f"Inspect the provided webcam snapshot carefully. Determine if the condition is currently met in the image.\n"
            f"Respond ONLY with a valid JSON object in this exact schema, without any conversational preamble or markdown code blocks:\n"
            f"{{\"triggered\": true or false, \"description\": \"brief Spanish description of what is seen regarding the condition\"}}"
        )

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64_data}"},
                    },
                ],
            }
        ]

        resp = self.model_client.chat.completions.create(
            model=self.model_name,
            messages=messages,
            temperature=0.0,
            stream=False,
        )

        content = resp.choices[0].message.content or ""
        logger.info(f"[Surveillance LLM Response]: {content.strip()}")

        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            content = match.group(0)

        data = json.loads(content)
        triggered = bool(data.get("triggered", False))
        description = str(data.get("description", ""))
        return triggered, description

    def _notify_trigger(self, description: str, saved_image_path: str = "") -> None:
        """Trigger desktop notification and forward event to Assistant GUI."""
        logger.info(f"Surveillance triggered! {description} (Saved: {saved_image_path})")

        notify_bin = _find_binary("notify-send")
        if notify_bin:
            title = "🚨 Vigilancia: ¡Alerta detectada!"
            body = description
            if saved_image_path:
                body += f"\n📁 Guardado en: {os.path.basename(saved_image_path)}"
            try:
                cmd = [notify_bin, "-u", "critical", "-t", "8000", title, body]
                if saved_image_path and os.path.exists(saved_image_path):
                    cmd.extend(["-i", saved_image_path])
                subprocess.run(cmd, timeout=3)
            except (subprocess.SubprocessError, OSError) as e:
                logger.warning(f"Failed to dispatch notify-send: {e}")

        if self.on_trigger_callback:
            try:
                self.on_trigger_callback(description, saved_image_path)
            except Exception as e:
                logger.warning(f"Failed to invoke on_trigger_callback: {e}")


# Global singleton accessors
surveillance_manager = SurveillanceManager()


def start_surveillance(
    condition: str,
    interval_seconds: int = 5,
    action: str = "save_and_notify",
) -> str:
    """Tool entrypoint: Start background webcam surveillance."""
    return surveillance_manager.start(
        condition=condition,
        interval_seconds=interval_seconds,
        action=action,
    )


def stop_surveillance() -> str:
    """Tool entrypoint: Stop active background webcam surveillance."""
    return surveillance_manager.stop()


def get_surveillance_status() -> str:
    """Tool entrypoint: Query status of webcam surveillance."""
    st = surveillance_manager.get_status()
    if not st["active"]:
        return "El modo vigilancia está actualmente desactivado."
    return (
        f"Modo vigilancia ACTIVO.\n"
        f"- Condición: '{st['condition']}'\n"
        f"- Intervalo: {st['interval_seconds']}s\n"
        f"- Acción: {st['action']}\n"
        f"- Tiempo activo: {st['uptime_seconds']}s\n"
        f"- Capturas analizadas: {st['frames_analyzed']}"
    )
