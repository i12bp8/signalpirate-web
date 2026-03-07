import json
import logging
import aiohttp
from typing import AsyncGenerator

logger = logging.getLogger(__name__)

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

async def _stream_openrouter(api_key: str, model: str, messages: list) -> AsyncGenerator[str, None]:
    """Stream a completion from OpenRouter."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://signalpirate.local",
        "X-Title": "SignalPirate SDR",
    }
    
    payload = {
        "model": model,
        "messages": messages,
        "stream": True
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(OPENROUTER_API_URL, headers=headers, json=payload) as response:
                if response.status != 200:
                    error_text = await response.text()
                    yield f"**API Error {response.status}:**\n```json\n{error_text}\n```"
                    return

                async for line in response.content:
                    if not line:
                        continue
                    line = line.decode('utf-8').strip()
                    if line == "data: [DONE]":
                        break
                    if line.startswith("data: "):
                        try:
                            chunk = json.loads(line[6:])
                            if "choices" in chunk and len(chunk["choices"]) > 0:
                                delta = chunk["choices"][0].get("delta", {})
                                if "content" in delta:
                                    yield delta["content"]
                        except json.JSONDecodeError:
                            continue
    except Exception as e:
        logger.error(f"AI Stream Error: {e}")
        yield f"\n\n**Connection Error:** {str(e)}"

async def analyze_signal(config: dict, signal_data: dict) -> AsyncGenerator[str, None]:
    """Generates an AI analysis of a captured signal."""
    api_key = config.get("ai_key")
    model = config.get("ai_model", "anthropic/claude-3.5-haiku")
    
    if not api_key:
        yield "⚠️ **No OpenRouter API Key configured.**\n\nPlease go to the Settings tab and enter your OpenRouter API key to use AI features."
        return

    sys_prompt = (
        "You are an elite RF hacker. Keep your answer EXTREMELY short and punchy. "
        "Do not write long paragraphs. Output a quick summary of what the device is, "
        "and immediately list bullet points on HOW TO EXPLOIT it using a HackRF SDR. "
        "Focus on: Can it be replayed? Is it vulnerable to rolling code cloning? Are there CVEs? "
        "Use short actionable sentences. Use Markdown formatting."
    )
    
    usr_prompt = f"Analyze this captured SDR signal:\n```json\n{json.dumps(signal_data, indent=2)}\n```"
    
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": usr_prompt}
    ]
    
    async for chunk in _stream_openrouter(api_key, model, messages):
        yield chunk

async def craft_payload(config: dict, prompt: str, base_freq: float) -> AsyncGenerator[str, None]:
    """Generates a raw hex payload for TX based on a user prompt."""
    api_key = config.get("ai_key")
    model = config.get("ai_model", "anthropic/claude-3.5-haiku")
    
    if not api_key:
        yield "⚠️ **No OpenRouter API Key configured.** Please add it in Settings."
        return

    sys_prompt = (
        "You are an advanced RF payload crafting engine. Output ONLY the raw hex payload required "
        "for the requested attack or transmission. Do not include any explanation, markdown formatting, "
        "or prefix. ONLY the hex string."
    )
    
    usr_prompt = f"Base Frequency: {base_freq / 1e6} MHz\nRequest: {prompt}\nProvide the raw hex bytes to transmit."
    
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": usr_prompt}
    ]
    
    async for chunk in _stream_openrouter(api_key, model, messages):
        yield chunk

async def chat(config: dict, prompt: str, context: dict | None, history: list = None) -> AsyncGenerator[str, None]:
    """Generates an embedded AI chat completion, optionally injecting signal context and chat history."""
    api_key = config.get("ai_key")
    model = config.get("ai_model", "anthropic/claude-3.5-haiku")
    history = history or []
    
    if not api_key:
        yield "⚠️ **No OpenRouter API Key configured.** Please add it in Settings."
        return

    sys_prompt = (
        "You are an elite AI assistant embedded inside 'SignalPirate', an advanced SDR (Software Defined Radio) hacking platform. "
        "Your job is to help the user reverse-engineer, analyze, and exploit RF sub-GHz signals.\n\n"
        "**CRITICAL FORMATTING INSTRUCTIONS:**\n"
        "- ALWAYS use Rich Markdown to make your output visually stunning.\n"
        "- Use **Markdown Tables** to display technical specifications or comparisons (e.g. `| Property | Value |`).\n"
        "- Use **Blockquotes** (`>`) with emojis for important warnings (e.g. `> ⚠️ **WARNING:** Replay attacks may be illegal.`).\n"
        "- Use **Code Blocks** (`` ` `` or ```` ``` ````) for raw hex, binary data, or tool commands.\n"
        "- Keep paragraphs short and utilize bullet lists extensively for readability."
    )
    
    usr_prompt = prompt
    if context:
        if context.get('type') == 'current_capture':
            usr_prompt += f"\n\n**Context (Currently Selected Signal):**\n```json\n{json.dumps(context.get('data'), indent=2)}\n```"
        elif context.get('type') == 'all_captures':
            usr_prompt += f"\n\n**Context (All Filtered Signals in View):**\n```json\n{json.dumps(context.get('data'), indent=2)}\n```"

    messages = [{"role": "system", "content": sys_prompt}]
    for msg in history:
        # validate role is strictly user or assistant to be safe
        if isinstance(msg, dict) and msg.get("role") in ("user", "assistant"):
            messages.append({"role": msg["role"], "content": msg.get("content", "")})
            
    # Append the final prompt currently being evaluated 
    messages.append({"role": "user", "content": usr_prompt})

    async for chunk in _stream_openrouter(api_key, model, messages):
        yield chunk
