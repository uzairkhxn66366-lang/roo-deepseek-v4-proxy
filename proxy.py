from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
import httpx
import os
import json
import hashlib

app = FastAPI()

DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")

# --- 1. PERSISTENT CACHE ---
tool_cache = {}
text_cache = {}

# --- 2. THE PENDING STATE (DeepSeek's Logic) ---
_pending_state = {
    "reasoning": "",
    "tool_call_ids":[]
}

def save_cache():
    try:
        with open(".roo_deepseek_cache.json", "w", encoding="utf-8") as f:
            json.dump({"tools": tool_cache, "texts": text_cache}, f)
    except Exception:
        pass

def load_cache():
    global tool_cache, text_cache
    try:
        if os.path.exists(".roo_deepseek_cache.json"):
            with open(".roo_deepseek_cache.json", "r", encoding="utf-8") as f:
                data = json.load(f)
                tool_cache = data.get("tools", {})
                text_cache = data.get("texts", {})
    except Exception:
        pass

load_cache()

@app.get("/v1/models")
async def get_models():
    return {
        "object": "list",
        "data":[
            {"id": "deepseek-v4-flash", "object": "model", "created": 1700000000, "owned_by": "deepseek"},
            {"id": "deepseek-v4-pro", "object": "model", "created": 1700000000, "owned_by": "deepseek"},
            {"id": "deepseek-reasoner", "object": "model", "created": 1700000000, "owned_by": "deepseek"}
        ]
    }

def extract_regular_text(content_block):
    """Helper to safely extract string text from Roo Code's arrays"""
    if isinstance(content_block, str):
        return content_block
    if isinstance(content_block, list):
        regular_text = ""
        for block in content_block:
            b_type = block.get("type", "text")
            if b_type in ["text", "reasoning"]:
                regular_text += block.get("text", "")
            elif b_type == "tool_result":
                sub = block.get("content",[])
                if isinstance(sub, list):
                    for sub_b in sub:
                        regular_text += sub_b.get("text", "")
                elif isinstance(sub, str):
                    regular_text += sub
        return regular_text
    return ""

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    global _pending_state
    
    try:
        body = await request.json()
    except Exception:
        return Response("Invalid JSON body", status_code=400)

    messages = body.get("messages",[])
    
    # Identify the index of the very LAST assistant message in the history
    assistant_indices =[i for i, m in enumerate(messages) if m.get("role") == "assistant"]
    last_assistant_idx = assistant_indices[-1] if assistant_indices else -1

    for i, msg in enumerate(messages):
        if msg.get("role") == "assistant":
            
            c_text = extract_regular_text(msg.get("content"))
            c_text_stripped = c_text.strip() if c_text else ""
            thash = hashlib.md5(c_text_stripped.encode('utf-8')).hexdigest() if c_text_stripped else None

            # --- DEEPSEEK'S PENDING LOGIC INJECTION ---
            # If this is the last assistant message, and we just captured reasoning from the previous stream...
            if i == last_assistant_idx and _pending_state["reasoning"]:
                msg["reasoning_content"] = _pending_state["reasoning"]
                
                # Lock this newly formatted text (even if interrupted) into persistent cache
                if thash:
                    text_cache[thash] = _pending_state["reasoning"]
                for tid in _pending_state["tool_call_ids"]:
                    tool_cache[tid] = _pending_state["reasoning"]
                save_cache()
                
                # Clear the pending state so it doesn't double-fire
                _pending_state = {"reasoning": "", "tool_call_ids":[]}
            
            # --- STANDARD CACHE RESTORATION (For older messages in the history) ---
            if "reasoning_content" not in msg or not msg["reasoning_content"]:
                t_calls = msg.get("tool_calls",[])
                if isinstance(t_calls, list):
                    for tc in t_calls:
                        t_id = tc.get("id")
                        if t_id and t_id in tool_cache:
                            msg["reasoning_content"] = tool_cache[t_id]
                            break
                            
            if ("reasoning_content" not in msg or not msg["reasoning_content"]) and thash:
                if thash in text_cache:
                    msg["reasoning_content"] = text_cache[thash]

            # --- THE SAFETY FALLBACK ---
            if "reasoning_content" not in msg or not msg["reasoning_content"]:
                msg["reasoning_content"] = "Analyzed previous context and executed tools."

        # Flatten all content for strict DeepSeek API rules
        if isinstance(msg.get("content"), list):
            regular_text = extract_regular_text(msg.get("content"))
            msg["content"] = regular_text if regular_text else None

    headers = {"Content-Type": "application/json"}
    auth_header = request.headers.get("authorization")
    if auth_header and "dummy" not in auth_header.lower():
        headers["Authorization"] = auth_header
    elif DEEPSEEK_API_KEY:
        headers["Authorization"] = f"Bearer {DEEPSEEK_API_KEY}"

    client = httpx.AsyncClient()
    is_stream = body.get("stream", False)

    if is_stream:
        req = client.build_request("POST", DEEPSEEK_API_URL, json=body, headers=headers, timeout=120.0)
        response = await client.send(req, stream=True)
        
        if response.status_code != 200:
            await response.aread()
            err_content = response.content
            await response.aclose()
            return Response(content=err_content, status_code=response.status_code, media_type="application/json")
            
        async def stream_generator():
            global _pending_state
            reasoning_accumulator = ""
            tool_call_ids =[]
            
            async for line in response.aiter_lines():
                yield line + "\n"
                clean_line = line.strip()
                if clean_line.startswith("data: ") and clean_line != "data: [DONE]":
                    try:
                        data = json.loads(clean_line[6:])
                        delta = data["choices"][0]["delta"]
                        
                        if "reasoning_content" in delta and delta["reasoning_content"]:
                            reasoning_accumulator += delta["reasoning_content"]
                            
                        if "tool_calls" in delta and isinstance(delta["tool_calls"], list):
                            for tc in delta["tool_calls"]:
                                t_id = tc.get("id")
                                if t_id and t_id not in tool_call_ids:
                                    tool_call_ids.append(t_id)
                    except Exception:
                        pass
                        
            # --- POPULATE THE PENDING STATE ON STREAM END ---
            if reasoning_accumulator:
                _pending_state["reasoning"] = reasoning_accumulator
                _pending_state["tool_call_ids"] = tool_call_ids
                    
            await response.aclose()
            
        return StreamingResponse(stream_generator(), media_type="text/event-stream")
    else:
        response = await client.post(DEEPSEEK_API_URL, json=body, headers=headers, timeout=120.0)
        return Response(content=response.content, status_code=response.status_code, media_type=response.headers.get("content-type", "application/json"))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=9000)
