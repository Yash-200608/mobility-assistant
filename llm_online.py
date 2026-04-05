
import socket
from typing import Optional

from groq import Groq

from config import Config
from core import global_state, llm_circuit, logger

SYSTEM_PROMPT = """You are the spoken voice companion for a mobility assistance application (obstacle hints, cane/walker modes, gait feedback).
Short coaching and mode changes are already handled offline by the device; you only answer when the user wants longer or detailed help.
The user may be older or have limited mobility. Answers are read aloud by text-to-speech: use at most 3 short sentences, simple words, calm tone.
You are not a medical professional: do not diagnose conditions or prescribe treatment. If the user reports injury, chest pain, severe bleeding, or any emergency, tell them to contact local emergency services immediately.
CONTEXT from the app is approximate and may be wrong; do not claim you can see the room."""

_CONNECTIVITY_HOSTS = (("1.1.1.1", 53), ("8.8.8.8", 53))

def is_internet_available(timeout: float = 2.5) -> bool:
    for host, port in _CONNECTIVITY_HOSTS:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False

def mobility_context_snapshot() -> str:
    mode = global_state.get_mode()
    alerts_on = global_state.get_alerts_enabled()
    dets = global_state.get_detections()
    counts: dict[str, int] = {}
    for d in dets:
        label = d.get("class", "?")
        counts[label] = counts.get(label, 0) + 1
    det_str = ", ".join(f"{k}×{v}" for k, v in sorted(counts.items())) or "none listed"
    hw = "fused hardware OK" if global_state.use_hardware_for_safety() else "camera/mic or no live fused sensors"
    base = f"mode={mode}; spoken_alerts={'on' if alerts_on else 'off'}; vision_labels={det_str}; {hw}"
    rehab = global_state.get_rehab_llm_context()
    if rehab:
        return f"{base}; {rehab}"
    return base

def ask_mobility_llm(user_message: str, client: Groq) -> Optional[str]:
    if not Config.enable_online_llm():
        return None
    if not llm_circuit.can_execute():
        logger.warning("LLM circuit open; skipping online LLM call.")
        return None
    if not is_internet_available():
        logger.info("Offline: skipping online LLM.")
        return None

    ctx = mobility_context_snapshot()
    try:
        comp = client.chat.completions.create(
            model=Config.GROQ_LLM_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"CONTEXT: {ctx}\n\nUSER SAID: {user_message}"},
            ],
            max_tokens=Config.GROQ_LLM_MAX_TOKENS,
            temperature=0.35,
        )
        text = (comp.choices[0].message.content or "").strip()
        if text:
            llm_circuit.record_success()
            return text
    except Exception as e:
        logger.error(f"Online LLM error: {type(e).__name__}: {e}")
        llm_circuit.record_failure()
    return None
