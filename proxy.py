from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
import httpx
import os
import json
import hashlib

app = FastAPI()

DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
# Users can set their key in Roo Code, or via environment variable
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")

# --- THE BULLETPROOF MEMORY CACHE ---
tool_cache = {}
text_cache = {}

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

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    try:
        body = await request.json()
    except Exception:
        return Response("Invalid JSON body", status_code=400)

    messages = body.get("messages",[])
    
    for msg in messages:
        if msg.get("role") == "assistant":
            # 1. ATTEMPT TO RESTORE REASONING FROM MEMORY CACHE
            t_calls = msg.get("tool_calls",[])
            if isinstance(t_calls, list):
                for tc in t_calls:
                    t_id = tc.get("id")
                    if t_id and t_id in tool_cache:
                        msg["reasoning_content"] = tool_cache[t_id]
                        break
            
            if "reasoning_content" not in msg:
                c_text = msg.get("content")
                if isinstance(c_text, str) and c_text.strip():
                    thash = hashlib.md5(c_text.strip().encode('utf-8')).hexdigest()
                    if thash in text_cache:
                        msg["reasoning_content"] = text_cache[thash]

        # 2. FLATTEN CONTENT ARRAYS
        content = msg.get("content")
        if isinstance(content, list):
            reasoning_text = ""
            regular_text = ""
            for block in content:
                b_type = block.get("type", "text")
                if b_type == "reasoning":
                    reasoning_text += block.get("text", "")
                elif b_type == "text":
                    regular_text += block.get("text", "")
                elif b_type == "tool_result":
                    sub_content = block.get("content",[])
                    if isinstance(sub_content, list):
                        for sub_b in sub_content:
                            regular_text += sub_b.get("text", "")
                    elif isinstance(sub_content, str):
                        regular_text += sub_content
            
            if msg.get("role") == "assistant" and reasoning_text and "reasoning_content" not in msg:
                msg["reasoning_content"] = reasoning_text
                
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
            reasoning_accumulator = ""
            content_accumulator = ""
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
                        if "content" in delta and delta["content"]:
                            content_accumulator += delta["content"]
                            
                        if "tool_calls" in delta and isinstance(delta["tool_calls"], list):
                            for tc in delta["tool_calls"]:
                                t_id = tc.get("id")
                                if t_id and t_id not in tool_call_ids:
                                    tool_call_ids.append(t_id)
                    except Exception:
                        pass
                        
            if reasoning_accumulator:
                made_changes = False
                for t_id in tool_call_ids:
                    tool_cache[t_id] = reasoning_accumulator
                    made_changes = True
                    
                if content_accumulator.strip():
                    thash = hashlib.md5(content_accumulator.strip().encode('utf-8')).hexdigest()
                    text_cache[thash] = reasoning_accumulator
                    made_changes = True
                    
                if made_changes:
                    save_cache()
                    
            await response.aclose()
            
        return StreamingResponse(stream_generator(), media_type="text/event-stream")
    else:
        response = await client.post(DEEPSEEK_API_URL, json=body, headers=headers, timeout=120.0)
        return Response(content=response.content, status_code=response.status_code, media_type=response.headers.get("content-type", "application/json"))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=9000)
