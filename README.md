# 🧠 Roo DeepSeek Proxy

> A lightweight FastAPI proxy that bridges Roo Code's OpenAI-compatible format to DeepSeek's native API — preserving reasoning content across multi-turn tool-calling conversations.

---

## 🚨 The Problem

Roo Code communicates with LLM providers using the **OpenAI-compatible chat completions** format. When DeepSeek v4 models (including DeepSeek-R1 and DeepSeek v4 Pro/Flash) return a response, they include a special `reasoning_content` parameter in each delta of the streaming response. This reasoning is **critical** for DeepSeek's tool-calling workflow — the model requires it back in subsequent turns when it uses tool results.

However, the OpenAI standard format **does not include** `reasoning_content`. When Roo Code sends the next request (with the assistant message that has tool calls but no reasoning), DeepSeek's API rejects it with a:

> **400 Bad Request** — "assistant message with tool_calls must also have reasoning_content"

This makes DeepSeek v4 models **unusable with Roo Code out of the box**.

---

## 🔧 How This Proxy Fixes It

This proxy sits between Roo Code and the DeepSeek API, performing three key transformations:

| Feature | What It Does |
|---------|-------------|
| **Memory Cache (Tool IDs)** | During streaming, the proxy captures reasoning content and stores it keyed by `tool_call_id`. On subsequent requests, it automatically re-injects `reasoning_content` into the matching assistant message with tool calls. |
| **Memory Cache (Content Hash)** | As a fallback, reasoning is also stored by MD5 hash of the message content. If a tool ID match is not found, the content-based lookup still recovers the reasoning. |
| **Content Array Flattening** | Roo Code sometimes sends `content` as an array of blocks (reasoning, text, tool_result types). The proxy flattens these into the simple string format that DeepSeek expects, while preserving reasoning for assistant messages. |

All cached reasoning is persisted to `.roo_deepseek_cache.json` so it survives proxy restarts.

---

## 📋 Prerequisites

- **Python 3.10+**
- **pip** (Python package manager)
- A **DeepSeek API key** — get one at [platform.deepseek.com](https://platform.deepseek.com)

---

## 📦 Installation

```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/roo-deepseek-proxy.git
cd roo-deepseek-proxy

# Install dependencies
pip install -r requirements.txt
```

---

## 🚀 Usage

```bash
python proxy.py
```

The proxy starts on `http://127.0.0.1:9000`. You should see:

```
INFO:     Started server process
INFO:     Uvicorn running on http://127.0.0.1:9000
```

### Environment Variable (Optional)

If you prefer not to enter your API key in Roo Code, you can set it as an environment variable:

```bash
# Windows (Command Prompt)
set DEEPSEEK_API_KEY=sk-your-key-here

# Windows (PowerShell)
$env:DEEPSEEK_API_KEY="sk-your-key-here"

# macOS / Linux
export DEEPSEEK_API_KEY="sk-your-key-here"
```

Then run `python proxy.py` — the proxy will use this key for all requests.

---

## 🦘 Roo Code Setup

Once the proxy is running, configure Roo Code as follows:

1. Open Roo Code settings
2. Set **API Provider** to: `OpenAI Compatible`
3. Set **Base URL** to: `http://127.0.0.1:9000/v1`
4. Set **Model ID** to one of:
   - `deepseek-v4-flash` — fast, cost-effective
   - `deepseek-v4-pro` — most capable
   - `deepseek-reasoner` — DeepSeek-R1 reasoning model
5. Enter your **real DeepSeek API key** in the API key field (the proxy securely forwards it to DeepSeek's servers)

That's it! Roo Code will now use DeepSeek v4 models with full reasoning and tool-calling support.

---

## 🗂️ Cache File

The proxy maintains a `.roo_deepseek_cache.json` file in the project directory. This file stores reasoning content between restarts. It is automatically excluded from Git (listed in `.gitignore`). You can safely delete it at any time — the proxy will simply recreate it on the next request.

---

## 📄 Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/v1/models` | Returns available DeepSeek model IDs |
| `POST` | `/v1/chat/completions` | Proxies chat completion requests to DeepSeek |

---

## 📝 License

MIT
