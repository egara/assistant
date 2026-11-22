"""Multi-Round Agentic Generator Worker Thread for Assistant.

Manages conversational inference, reasoning extraction (DeepSeek/GLM),
safe bash command approval gating, loop prevention, and multi-round tool chaining.
"""

import ast
import json
import logging
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import openai
from PyQt6.QtCore import QThread, pyqtSignal

from tools.dispatch import dispatch_tool
from tools.os import execute_command

logger = logging.getLogger("assistant.llm.generator")

# Default maximum number of chained tool-execution rounds per user request.
# Overridable per model via the "max_tool_rounds" key in each model spec
# (config.json -> llm.models.<key>.max_tool_rounds), so lightweight models can
# keep a tighter budget while strong agentic models get more headroom.
DEFAULT_MAX_TOOL_ROUNDS: int = 6

SAFE_COMMANDS: List[str] = [
    "uwsm",
    "ls",
    "cat",
    "echo",
    "date",
    "whoami",
    "uptime",
    "hyprctl",
    "wmctrl",
    "playerctl",
    "wpctl",
    "pactl",
    "xdotool",
    "sleep",
    "free",
    "df",
    "du",
    "top",
    "htop",
    "ps",
    "uname",
    "grep",
    "find",
    "neofetch",
    "fastfetch",
    "yazi",
    "kitty",
    "firefox",
    "google-chrome",
    "google-chrome-stable",
    "nautilus",
    "zed",
    "zeditor",
]


def extract_raw_tool_calls(
    full_content: str,
) -> Tuple[List[Dict[str, Any]], str]:
    """Parse raw pseudo-XML, fenced JSON, or raw JSON tool invocations emitted as text.

    Returns a tuple ``(tool_calls_list, clean_display_text)``.
    """
    tool_calls: List[Dict[str, Any]] = []
    clean_display = full_content

    # Pattern 1: Pseudo-XML format: <|tool_call>call:func_name(...)<tool_call|>
    if "<|tool_call>" in full_content:
        raw_call_match = re.search(
            r"<\|tool_call>.*?call:([a-zA-Z0-9_]+)(?:\((.*?)\)|\{(.*?)\})?.*?<tool_call\|>",
            full_content,
            re.DOTALL,
        )
        if raw_call_match:
            parsed_name = raw_call_match.group(1).strip()
            parsed_arg_str = (
                raw_call_match.group(2) or raw_call_match.group(3) or ""
            ).strip()
            parsed_args: Dict[str, Any] = {}
            if parsed_arg_str:
                if parsed_arg_str.startswith("{") and parsed_arg_str.endswith("}"):
                    try:
                        parsed_args = json.loads(parsed_arg_str)
                    except Exception:
                        pass
                if not parsed_args:
                    try:
                        parsed_args = json.loads("{" + parsed_arg_str + "}")
                    except Exception:
                        try:
                            expr = ast.parse(f"func({parsed_arg_str})")
                            for kw in expr.body[0].value.keywords:
                                parsed_args[kw.arg] = ast.literal_eval(kw.value)
                        except Exception as pe:
                            logger.error(
                                f"[Tool Parser] Error parsing raw args '{parsed_arg_str}': {pe}"
                            )

            tool_calls.append({
                "id": f"call_raw_{int(time.time())}",
                "type": "function",
                "function": {
                    "name": parsed_name,
                    "arguments": json.dumps(parsed_args),
                },
            })
            clean_display = re.sub(
                r"<\|tool_call>.*?<tool_call\|>", "", full_content, flags=re.DOTALL
            ).strip()
            return tool_calls, clean_display

    # Pattern 2: Fenced or raw JSON tool call block
    json_candidate = None
    sub_target = None
    fenced_match = re.search(
        r"```(?:json)?\s*(\{\s*[\"'](?:name|tool|function)[\"'].*?\})\s*```",
        full_content,
        re.DOTALL,
    )
    if fenced_match:
        json_candidate = fenced_match.group(1)
        sub_target = fenced_match.group(0)
    else:
        stripped = full_content.strip()
        has_key = any(
            f'"{k}"' in stripped or f"'{k}'" in stripped
            for k in ("name", "tool", "function")
        )
        if stripped.startswith("{") and stripped.endswith("}") and has_key:
            json_candidate = stripped
            sub_target = stripped

    if json_candidate:
        try:
            data = json.loads(json_candidate)
            name = data.get("name") or data.get("tool") or data.get("function")
            if name and isinstance(name, str):
                args = (
                    data.get("arguments")
                    or data.get("parameters")
                    or data.get("args")
                )
                if args is None:
                    args = {
                        k: v
                        for k, v in data.items()
                        if k not in ("name", "tool", "function")
                    }
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}
                elif not isinstance(args, dict):
                    args = {}
                tool_calls.append({
                    "id": f"call_raw_{int(time.time())}",
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(args),
                    },
                })
                clean_display = full_content.replace(sub_target, "").strip()
                return tool_calls, clean_display
        except Exception as pe:
            logger.debug(f"[Tool Parser] Non-tool JSON block ignored: {pe}")

    return tool_calls, clean_display


def extract_reasoning(delta: Any) -> Optional[str]:
    """Extract reasoning_content from a streaming delta, when present."""
    extra = getattr(delta, "model_extra", None)
    if not extra:
        return None
    reasoning = extra.get("reasoning_content")
    if reasoning is None:
        reasoning = extra.get("reasoning")
    return reasoning if isinstance(reasoning, str) else None


class AIGeneratorThread(QThread):
    """Background worker executing the streaming agentic loop."""

    status_signal = pyqtSignal(str, str)
    active_model_signal = pyqtSignal(str)
    new_bubble_signal = pyqtSignal(str, str)
    append_text_signal = pyqtSignal(str)
    sys_message_signal = pyqtSignal(str, str)
    require_approval_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(str)
    history_save_signal = pyqtSignal()

    def __init__(
        self,
        messages: List[Dict[str, Any]],
        model_config: Dict[str, Any],
        mcp_manager: Optional[Any] = None,
        command_intents: Optional[Dict[str, list]] = None,
        resolve_tools_fn: Optional[Callable[..., Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]]] = None,
        hide_window_callback: Optional[Callable[[], None]] = None,
        show_window_callback: Optional[Callable[[], None]] = None,
        reasoning_effort: Optional[str] = None,
    ):
        super().__init__()
        self.messages = messages
        self.model_config = model_config
        self.mcp_manager = mcp_manager
        self.command_intents = command_intents or {}
        self.resolve_tools_fn = resolve_tools_fn
        self.hide_window_callback = hide_window_callback
        self.show_window_callback = show_window_callback
        self.reasoning_effort = reasoning_effort
        self.approval_event = threading.Event()
        self.approval_result = False
        self._is_stopped = False

    def stop(self) -> None:
        """Signal the generator thread to stop execution."""
        self._is_stopped = True
        self.approval_event.set()

    def execute_command_safe(self, command: str) -> str:
        """Enforce safe execution of bash commands, gating risky commands with user approval."""
        base_cmd = command.strip().split()[0] if command.strip() else ""
        if base_cmd in SAFE_COMMANDS:
            return execute_command(command)

        self.approval_event.clear()
        self.require_approval_signal.emit(command)
        self.approval_event.wait()
        if self.approval_result and not self._is_stopped:
            return execute_command(command)
        else:
            return "Command execution denied by the user."

    def run(self) -> None:
        try:
            temp_messages = list(self.messages)

            # Check if vision content is present
            has_image = False
            for msg in temp_messages:
                if isinstance(msg.get("content"), list):
                    for part in msg["content"]:
                        if isinstance(part, dict) and part.get("type") == "image_url":
                            has_image = True
                            break

            active_client = self.model_config.get("client")
            active_model = self.model_config.get("name")
            model_key = self.model_config.get("key", active_model)

            try:
                max_tool_rounds = int(
                    self.model_config.get("max_tool_rounds", DEFAULT_MAX_TOOL_ROUNDS)
                )
            except (TypeError, ValueError):
                max_tool_rounds = DEFAULT_MAX_TOOL_ROUNDS
            max_tool_rounds = max(1, min(max_tool_rounds, 12))

            api_kwargs: Dict[str, Any] = {
                "model": active_model,
                "messages": temp_messages,
                "temperature": 0.0,
                "stream": True,
            }

            if not has_image and self.resolve_tools_fn:
                last_user_query = ""
                recent_context = ""
                for i in range(len(temp_messages) - 1, -1, -1):
                    msg = temp_messages[i]
                    if msg.get("role") == "user" and not last_user_query:
                        c = msg.get("content")
                        if isinstance(c, str):
                            last_user_query = c
                    elif msg.get("role") == "assistant" and not recent_context:
                        c = msg.get("content")
                        if isinstance(c, str):
                            recent_context = c
                    if last_user_query and recent_context:
                        break

                routed_tools, _ = self.resolve_tools_fn(
                    last_user_query,
                    recent_context=recent_context,
                    base_url=self.model_config.get("base_url", ""),
                )
                if routed_tools:
                    api_kwargs["tools"] = routed_tools

            self.status_signal.emit("󰟷 Thinking...", "#59c2ff")
            self.active_model_signal.emit(f"{model_key} ({active_model})")
            logger.info(
                f"[LLM] Request to '{model_key}' ({active_model}) | "
                f"tools={len(api_kwargs.get('tools', []))}"
            )

            executed_signatures_history: Set[Tuple[Tuple[str, str], ...]] = set()

            for round_num in range(max_tool_rounds + 1):
                request_kwargs = {
                    "model": active_model,
                    "messages": temp_messages,
                    "temperature": 0.0,
                    "stream": True,
                }
                if self.reasoning_effort and self.model_config.get("supports_thinking", False):
                    request_kwargs["reasoning_effort"] = self.reasoning_effort

                if api_kwargs.get("tools") and round_num < max_tool_rounds:
                    request_kwargs["tools"] = api_kwargs["tools"]
                elif api_kwargs.get("tools"):
                    logger.info(
                        f"[LLM] Round {round_num + 1}: tool round cap reached -> forcing text-only answer"
                    )

                try:
                    response = active_client.chat.completions.create(**request_kwargs)
                except openai.BadRequestError as bre:
                    # Robust shield: if the API complains that this model does not support thinking
                    # or rejected reasoning_effort, retry immediately without reasoning_effort and mark model
                    err_text = str(bre).lower()
                    if ("thinking" in err_text or "reasoning" in err_text) and "reasoning_effort" in request_kwargs:
                        logger.warning(
                            f"[LLM] Endpoint rejected reasoning_effort for '{active_model}': {bre}. "
                            f"Retrying without reasoning_effort parameter..."
                        )
                        self.model_config["supports_thinking"] = False
                        request_kwargs.pop("reasoning_effort", None)
                        response = active_client.chat.completions.create(**request_kwargs)
                    else:
                        raise bre
                except openai.APIConnectionError as ce:
                    logger.warning(
                        f"[LLM] Primary endpoint error ({ce}). "
                        f"Attempting fallback if available..."
                    )
                    raise ce

                full_content = ""
                full_reasoning = ""
                tool_calls: List[Dict[str, Any]] = []
                created_bubble = False
                in_reasoning_block = False

                # Buffer initial characters to suppress raw tool invocations (JSON/pseudo-XML) from appearing in the GUI
                buffered_prefix = ""
                is_buffering = True
                candidate_prefixes = ("<|tool_call", "```", "{\n", "{\"", "{ '", '{"', "{")

                for chunk in response:
                    if self._is_stopped:
                        break

                    delta = chunk.choices[0].delta if chunk.choices else None
                    if not delta:
                        continue

                    # Reasoning models support (DeepSeek / GLM-style)
                    reasoning_piece = extract_reasoning(delta)
                    if reasoning_piece:
                        full_reasoning += reasoning_piece
                        if not in_reasoning_block:
                            in_reasoning_block = True
                        preview = full_reasoning.strip().replace("\n", " ")
                        if len(preview) > 60:
                            preview = "…" + preview[-57:]
                        self.status_signal.emit(f"󰟷 Thinking: {preview}", "#59c2ff")

                    if delta.content:
                        if in_reasoning_block:
                            in_reasoning_block = False
                            self.status_signal.emit("󰟷 Thinking...", "#59c2ff")
                        full_content += delta.content

                        if is_buffering:
                            buffered_prefix += delta.content
                            stripped_buf = buffered_prefix.lstrip()
                            if not stripped_buf:
                                continue

                            matches_prefix = any(
                                stripped_buf.startswith(p) or p.startswith(stripped_buf)
                                for p in candidate_prefixes
                            )
                            if matches_prefix:
                                if len(stripped_buf) > 300:
                                    is_buffering = False
                                    if not created_bubble:
                                        self.new_bubble_signal.emit("ai", "")
                                        created_bubble = True
                                    self.append_text_signal.emit(buffered_prefix)
                                    buffered_prefix = ""
                            else:
                                is_buffering = False
                                if not created_bubble:
                                    self.new_bubble_signal.emit("ai", "")
                                    created_bubble = True
                                self.append_text_signal.emit(buffered_prefix)
                                buffered_prefix = ""
                        else:
                            self.append_text_signal.emit(delta.content)

                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            while len(tool_calls) <= idx:
                                tool_calls.append({
                                    "id": "",
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""},
                                })
                            if tc.id:
                                tool_calls[idx]["id"] += tc.id
                            if tc.function:
                                if tc.function.name:
                                    tool_calls[idx]["function"]["name"] += tc.function.name
                                if tc.function.arguments:
                                    tool_calls[idx]["function"]["arguments"] += tc.function.arguments

                if self._is_stopped:
                    self.finished_signal.emit("")
                    return

                if full_reasoning:
                    logger.info(
                        f"[LLM] Round {round_num + 1}: reasoning finished "
                        f"({len(full_reasoning)} chars): {full_reasoning[:300]!r}"
                    )

                # Fallback parser for pseudo-XML and raw/fenced JSON tool calls
                if not tool_calls:
                    parsed_raw_tools, clean_display = extract_raw_tool_calls(full_content)
                    if parsed_raw_tools:
                        tool_calls.extend(parsed_raw_tools)
                        full_content = clean_display

                # Flush any leftover buffered text if no tool calls were detected
                if not tool_calls and buffered_prefix:
                    if not created_bubble:
                        self.new_bubble_signal.emit("ai", "")
                        created_bubble = True
                    self.append_text_signal.emit(buffered_prefix)
                    buffered_prefix = ""

                if not tool_calls:
                    if full_content.strip():
                        self.messages.append({"role": "assistant", "content": full_content})
                        logger.info(f"[LLM] Round {round_num + 1}: answer ({len(full_content)} chars)")
                    else:
                        logger.warning(f"[LLM] Round {round_num + 1}: model returned no content and no tool calls")
                        # Visible fallback: never leave the user without any response.
                        if round_num >= max_tool_rounds:
                            fallback_text = (
                                "He agotado el límite de operaciones para esta petición sin "
                                "completar la acción. Pídemelo de nuevo si quieres que lo reintente."
                            )
                        else:
                            fallback_text = (
                                "No he recibido una respuesta válida del modelo. "
                                "¿Puedes repetir la petición?"
                            )
                        self.new_bubble_signal.emit("ai", "")
                        self.append_text_signal.emit(fallback_text)
                        self.messages.append({"role": "assistant", "content": fallback_text})
                        self.history_save_signal.emit()
                        self.finished_signal.emit(fallback_text)
                        return
                    break

                # Loop prevention
                round_signatures = []
                for tc in tool_calls:
                    fn_name = tc.get("function", {}).get("name", "")
                    fn_args = tc.get("function", {}).get("arguments", "")
                    round_signatures.append((fn_name, fn_args))
                current_round_sig = tuple(round_signatures)

                if current_round_sig in executed_signatures_history:
                    logger.warning(
                        f"[LLM] Round {round_num + 1}: detected repetitive tool call loop "
                        f"{current_round_sig} -> stopping loop"
                    )
                    if full_content.strip():
                        self.messages.append({"role": "assistant", "content": full_content})
                    else:
                        loop_msg = "He obtenido los datos necesarios pero se ha interrumpido la consulta. ¿Quieres que lo vuelva a intentar?"
                        self.new_bubble_signal.emit("ai", "")
                        self.append_text_signal.emit(loop_msg)
                        self.messages.append({"role": "assistant", "content": loop_msg})
                    break
                executed_signatures_history.add(current_round_sig)

                # Tool execution round
                clean_tool_calls = []
                for i, tc in enumerate(tool_calls):
                    call_id = tc.get("id") or f"call_{round_num}_{i}_{int(time.time())}"
                    tc["id"] = call_id
                    clean_tool_calls.append({
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": tc["function"]["name"],
                            "arguments": tc["function"]["arguments"],
                        },
                    })

                assistant_msg = {
                    "role": "assistant",
                    "content": full_content if full_content.strip() else None,
                    "tool_calls": clean_tool_calls,
                }
                temp_messages.append(assistant_msg)

                tool_results_list = []
                all_tools_silent = True

                for tool_call in tool_calls:
                    if self._is_stopped:
                        break

                    tool_name = tool_call["function"]["name"]
                    try:
                        args = json.loads(tool_call["function"]["arguments"])
                    except Exception:
                        args = {}

                    logger.info(
                        f"[LLM] Round {round_num + 1}: executing '{tool_name}' "
                        f"with {json.dumps(args, ensure_ascii=False)[:200]}"
                    )
                    self.sys_message_signal.emit("sys", f"󰑮 Running {tool_name}...")

                    results, tool_silent = dispatch_tool(
                        tool_name,
                        args,
                        execute_command_safe=self.execute_command_safe,
                        mcp_manager=self.mcp_manager,
                        command_intents=self.command_intents,
                        hide_window_callback=self.hide_window_callback,
                        show_window_callback=self.show_window_callback,
                    )
                    if not tool_silent:
                        all_tools_silent = False

                    # Check if results contain visual image payload (e.g. from take_screenshot)
                    if isinstance(results, dict) and "image_b64" in results:
                        text_content = results.get("text", "Screenshot captured.")
                        b64_data = results["image_b64"]
                        tool_results_list.append({
                            "call_id": tool_call["id"],
                            "name": tool_name,
                            "content": text_content,
                            "image_b64": b64_data,
                        })
                    else:
                        tool_results_list.append({
                            "call_id": tool_call["id"],
                            "name": tool_name,
                            "content": str(results),
                        })

                if self._is_stopped:
                    self.finished_signal.emit("")
                    return

                if all_tools_silent:
                    logger.info(
                        f"[LLM] Round {round_num + 1}: all tools silent -> finishing with silent 'Hecho.'"
                    )
                    self.messages.append({
                        "role": "assistant",
                        "content": "Hecho.",
                        "is_silent": True,
                    })
                    self.history_save_signal.emit()
                    self.finished_signal.emit("")
                    return

                for res_item in tool_results_list:
                    temp_messages.append({
                        "role": "tool",
                        "tool_call_id": res_item["call_id"],
                        "name": res_item["name"],
                        "content": res_item["content"],
                    })
                    # If this tool call produced an image (e.g. screenshot), inject multimodal user turn
                    # so vision-capable models can inspect the pixels in the next round.
                    if res_item.get("image_b64"):
                        temp_messages.append({
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "Screenshot captured. Analyze the visual contents of this screenshot to fulfill the request.",
                                },
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/png;base64,{res_item['image_b64']}"
                                    },
                                },
                            ],
                        })

            self.history_save_signal.emit()
            self.finished_signal.emit(full_content)

        except openai.APIConnectionError:
            logger.error("[LLM] Connection to the LLM server failed")
            self.sys_message_signal.emit(
                "sys", "󰀦 Error: The Local LLM Server is offline."
            )
            self.finished_signal.emit("The local model server is offline.")
        except Exception as e:
            logger.error(f"[LLM] Unhandled generator error: {e}", exc_info=True)
            self.sys_message_signal.emit("sys", f"[API ERROR]: {str(e)}")
            self.finished_signal.emit("")
