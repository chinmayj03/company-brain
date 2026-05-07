# LLM Provider Setup Guide

Step-by-step instructions for connecting company-brain-ai to each supported LLM provider.
Pick the section for the provider you want, then jump to "Wiring it into the project".

---

## Quick comparison

| Provider    | Cost          | Speed          | Quality                  | Credit card? | Context  |
|-------------|---------------|----------------|--------------------------|--------------|----------|
| **Groq**    | Free          | 500–1,000 tok/s| Excellent (GPT-OSS 120B) | No           | 131K     |
| **OpenRouter** | Free tier  | Medium         | Best free (DeepSeek-R1)  | No           | 64–128K  |
| **Anthropic** | ~$13–14/run | Fast          | Best overall             | Yes          | 200K     |
| **OpenAI**  | ~$2–30/run    | Fast           | Very good                | Yes          | 128K     |
| **Ollama**  | Free          | Slow (local)   | Depends on GPU           | No           | VRAM limited |

**Recommendation for a first test run:** start with **Groq** — free, no credit card, and now has GPT-OSS 120B which rivals paid models.

---

## Provider 1: Groq (FREE — recommended)

Groq runs models on custom LPU silicon at 500–1,000 tokens/second. The current lineup (April 2026) includes GPT-OSS 120B/20B, Llama 4 Scout, and Qwen 3 32B — all free tier.

### Step 1 — Create a Groq account

1. Go to **https://console.groq.com**
2. Click **"Sign Up"** (top right)
3. Sign up with Google or create an email account
4. Verify your email address

### Step 2 — Generate an API key

1. After logging in, go to **https://console.groq.com/keys**
2. Click **"Create API Key"**
3. Give it a name like `company-brain-dev`
4. Click **"Submit"**
5. **Copy the key immediately** — it starts with `gsk_` and is only shown once
6. Store it somewhere safe (password manager)

### Step 3 — Current model lineup

Full model list at **https://console.groq.com/docs/models**. API IDs (what you put in `.env`):

| Console display name   | API model ID                                    | Speed      | Best for                  |
|------------------------|-------------------------------------------------|------------|---------------------------|
| GPT OSS 20B            | `openai/gpt-oss-20b`                            | 1,000 tok/s | High-volume extraction   |
| Llama 4 Scout          | `meta-llama/llama-4-scout-17b-16e-instruct`     | 750 tok/s  | Tool use, vision, balanced |
| GPT OSS 120B           | `openai/gpt-oss-120b`                           | 500 tok/s  | Synthesis & user queries  |
| Qwen 3 32B             | `qwen/qwen3-32b`                                | 400 tok/s  | Reasoning & gap detection |
| Llama 3.3 70B          | `llama-3.3-70b-versatile`                       | 280 tok/s  | Proven fallback           |
| Llama 3.1 8B           | `llama-3.1-8b-instant`                          | 560 tok/s  | Ultra-fast fallback       |

> **Important:** The console shows friendly display names. The `.env` needs the **API model ID** column above — not the display name.

### Step 4 — Configure your .env

```bash
# In company-brain-ai/.env:

LLM_PROVIDER=groq
GROQ_API_KEY=gsk_YOUR_KEY_HERE

# Fastest model for high-volume extraction pass
GROQ_MODEL_FAST=openai/gpt-oss-20b

# Llama 4 Scout for balanced tasks — supports tool use and vision
GROQ_MODEL_BALANCED=meta-llama/llama-4-scout-17b-16e-instruct

# GPT-OSS 120B for synthesis (best quality on Groq)
GROQ_MODEL_SYNTHESIS=openai/gpt-oss-120b

# Qwen 3 32B for reasoning / gap detection (strong logical reasoning)
GROQ_MODEL_REASONING=qwen/qwen3-32b

# GPT-OSS 120B for user-facing queries
GROQ_MODEL_QUERY=openai/gpt-oss-120b

# All Groq models support 131K context
MAX_INPUT_TOKENS=100000

# Reduce concurrency to stay within tok/min rate limits
MAX_ENTITY_EXTRACTION_CONCURRENCY=3
MAX_CONTEXT_SYNTHESIS_CONCURRENCY=2
```

### Troubleshooting Groq

**429 rate limit errors** → Reduce concurrency:
```bash
MAX_ENTITY_EXTRACTION_CONCURRENCY=2
MAX_CONTEXT_SYNTHESIS_CONCURRENCY=1
```

**"Model not found" error** → Check the model ID is exact (no typos). List available models:
```bash
curl https://api.groq.com/openai/v1/models \
  -H "Authorization: Bearer $GROQ_API_KEY" | python3 -m json.tool | grep '"id"'
```

**Hit daily limit** → Switch the slow passes to `llama-3.1-8b-instant` which has a separate quota pool from `llama-3.3-70b-versatile`.

---

## Provider 2: OpenRouter (FREE — 30+ models)

OpenRouter is a unified API gateway that proxies requests to dozens of models, including several that are permanently free. Use it to mix models per task role — e.g. fast Llama for extraction, DeepSeek-R1 for gap detection.

### Step 1 — Create an OpenRouter account

1. Go to **https://openrouter.ai**
2. Click **"Sign In"** → **"Continue with Google"** (or email)
3. Verify your email if using email sign-up

### Step 2 — Generate an API key

1. Go to **https://openrouter.ai/keys**
2. Click **"Create Key"**
3. Give it a name like `company-brain`
4. Leave credit limit blank (free models don't need credits)
5. Click **"Create"**
6. **Copy the key** — starts with `sk-or-`

### Step 3 — Browse free models

Go to **https://openrouter.ai/models?q=free** to see all free models. Filter by "Free" in the top bar.

Key free models (April 2026):

| Model ID                                      | Quality | Context | Notes                        |
|-----------------------------------------------|---------|---------|-------------------------------|
| `meta-llama/llama-3.1-8b-instruct:free`       | Good    | 128K    | Fast extraction               |
| `meta-llama/llama-3.3-70b-instruct:free`      | Great   | 128K    | Best free balanced model      |
| `deepseek/deepseek-r1:free`                   | Excellent | 64K  | Best reasoning, slower        |
| `deepseek/deepseek-r1-distill-llama-70b:free` | Great   | 128K    | R1 distilled, faster          |
| `google/gemma-3-27b-it:free`                  | Good    | 96K     | Google model                  |
| `mistralai/mistral-7b-instruct:free`          | OK      | 32K     | Lightweight                   |

> **Note:** Free models have ~200 req/day per model and 20 req/min. For a 500-file run, use the pipeline's `--batch-size` flag to run in stages across a few days, or add a small credit balance ($5) to remove limits.

### Step 4 — Configure your .env

```bash
# In company-brain-ai/.env:

LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-YOUR_KEY_HERE

# Use lightweight model for extraction (high volume), R1 for reasoning (low volume)
OPENROUTER_MODEL_FAST=meta-llama/llama-3.1-8b-instruct:free
OPENROUTER_MODEL_BALANCED=meta-llama/llama-3.3-70b-instruct:free
OPENROUTER_MODEL_SYNTHESIS=deepseek/deepseek-r1:free
OPENROUTER_MODEL_REASONING=deepseek/deepseek-r1:free
OPENROUTER_MODEL_QUERY=meta-llama/llama-3.3-70b-instruct:free

# Strict concurrency on free tier
MAX_ENTITY_EXTRACTION_CONCURRENCY=2
MAX_CONTEXT_SYNTHESIS_CONCURRENCY=1

# DeepSeek-R1 free has 64K context; set lower to be safe
MAX_INPUT_TOKENS=56000
```

### Troubleshooting OpenRouter

**"No endpoints found" or 404** → Model may not be available in your region or the free quota is exhausted. Check https://openrouter.ai/models for current status.

**429 errors** → You've hit 20 req/min. Lower concurrency to 1 and add a small credit balance ($5) to increase limits significantly.

**Long latency on free models** → Free model requests are lower priority. Add a small credit balance or switch to Groq for speed.

---

## Provider 3: Anthropic (Claude — paid, best quality)

### Step 1 — Create an Anthropic account

1. Go to **https://console.anthropic.com**
2. Click **"Sign Up"**
3. Verify your email

> **Important:** Your Claude Max chat subscription does NOT give you API access. The API is separate billing.

### Step 2 — Add API credits

1. Go to **https://console.anthropic.com/settings/billing**
2. Click **"Add payment method"**
3. Add a credit card
4. Click **"Buy credits"** — minimum purchase is $5

### Step 3 — Generate an API key

1. Go to **https://console.anthropic.com/settings/keys**
2. Click **"Create Key"**
3. Name it `company-brain-dev`
4. Click **"Create Key"**
5. **Copy the key** — starts with `sk-ant-`

### Step 4 — Configure your .env

```bash
# In company-brain-ai/.env:

LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-YOUR_KEY_HERE

# Haiku for fast extraction (~$1/M in, $5/M out), Sonnet for synthesis
ANTHROPIC_MODEL_FAST=claude-haiku-4-5-20251001
ANTHROPIC_MODEL_BALANCED=claude-sonnet-4-6
ANTHROPIC_MODEL_SYNTHESIS=claude-sonnet-4-6
ANTHROPIC_MODEL_REASONING=claude-sonnet-4-6
ANTHROPIC_MODEL_QUERY=claude-sonnet-4-6

# Claude supports 200K context
MAX_INPUT_TOKENS=120000

# Higher concurrency is fine on Anthropic (Tier 1 = 50 req/min)
MAX_ENTITY_EXTRACTION_CONCURRENCY=10
MAX_CONTEXT_SYNTHESIS_CONCURRENCY=5
```

**Cost estimate for 500 files:**
- Entity extraction (Haiku): ~$2–3
- Synthesis passes (Sonnet): ~$10–12
- Total: **~$13–14 per full run**

Enable Batch API to cut this in half:
```bash
ANTHROPIC_USE_BATCH_API=true
```
Batch runs complete within 24 hours and cost 50% less.

---

## Provider 4: OpenAI (GPT — paid)

### Step 1 — Create an OpenAI account

1. Go to **https://platform.openai.com**
2. Click **"Sign Up"**
3. Verify your phone number

> **Important:** Your ChatGPT Pro subscription does NOT give you API access. The API uses separate billing.

### Step 2 — Add API credits

1. Go to **https://platform.openai.com/settings/organization/billing**
2. Click **"Add payment method"**
3. Add credit card
4. Set a usage limit (e.g. $20/month) to avoid surprises

### Step 3 — Generate an API key

1. Go to **https://platform.openai.com/api-keys**
2. Click **"Create new secret key"**
3. Name it `company-brain`
4. **Copy the key** — starts with `sk-`

### Step 4 — Configure your .env

```bash
# In company-brain-ai/.env:

LLM_PROVIDER=openai
OPENAI_API_KEY=sk-YOUR_KEY_HERE

# gpt-4o-mini for extraction (cheap), gpt-4o for synthesis (better quality)
OPENAI_MODEL_FAST=gpt-4o-mini
OPENAI_MODEL_BALANCED=gpt-4o
OPENAI_MODEL_SYNTHESIS=gpt-4o
OPENAI_MODEL_REASONING=gpt-4o
OPENAI_MODEL_QUERY=gpt-4o

# GPT-4o has 128K context
MAX_INPUT_TOKENS=100000
```

**Cost estimate for 500 files:**
- All gpt-4o-mini: **~$1.80**
- Mixed mini/4o: **~$10–15**
- All gpt-4o: **~$30**

---

## Wiring it into the project

Once your `.env` is set, verify the provider loads correctly:

```bash
cd company-brain-ai

# Activate venv
source .venv/bin/activate   # or: poetry shell

# Quick smoke test — no API call made, just verifies config loads
python - <<'EOF'
import os, asyncio
from companybrain.llm.factory import get_provider, reset_provider
from companybrain.llm.base import TaskRole

p = get_provider()
print(f"Provider : {p.provider_name}")
print(f"FAST     : {p.model_for_role(TaskRole.FAST)}")
print(f"BALANCED : {p.model_for_role(TaskRole.BALANCED)}")
print(f"REASONING: {p.model_for_role(TaskRole.REASONING)}")
print("Config loaded successfully ✓")
EOF
```

Expected output (example for Groq):
```
Provider : groq
FAST     : openai/gpt-oss-20b
BALANCED : meta-llama/llama-4-scout-17b-16e-instruct
REASONING: qwen/qwen3-32b
Config loaded successfully ✓
```

### Make a real test call

```bash
python - <<'EOF'
import asyncio
from companybrain.llm.factory import get_provider
from companybrain.llm.base import TaskRole, ChatMessage

async def test():
    p = get_provider()
    resp = await p.chat(
        messages=[ChatMessage(role="user", content="Reply with: OK")],
        role=TaskRole.FAST,
        max_tokens=10,
    )
    print(f"Response : {resp.content!r}")
    print(f"Model    : {resp.model}")
    print(f"Tokens   : {resp.input_tokens} in / {resp.output_tokens} out")

asyncio.run(test())
EOF
```

---

## Switching providers mid-project

You can switch providers at any time — the pipeline is stateless with respect to the LLM. Just update `LLM_PROVIDER` and the relevant API key in `.env`. The stored knowledge graph in Postgres is not affected.

```bash
# Switch from Groq to Anthropic:
# Edit .env:
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...

# Restart any running pipeline workers after changing .env.
```

---

## Rate limit cheat sheet

| Provider   | Concurrency setting         | Why                                         |
|------------|-----------------------------|---------------------------------------------|
| Groq (GPT-OSS 20B)  | ENTITY=5, SYNTHESIS=3  | 1,000 tok/s, generous tok/min limit   |
| Groq (GPT-OSS 120B) | ENTITY=3, SYNTHESIS=2  | 500 tok/s, tighter tok/min limit      |
| Groq (Qwen 3 32B)   | ENTITY=3, SYNTHESIS=2  | 400 tok/s, 6,000 tok/min              |
| OpenRouter free | ENTITY=2, SYNTHESIS=1   | 20 req/min, 200 req/day per model         |
| Anthropic Tier 1 | ENTITY=10, SYNTHESIS=5 | 50 req/min, 40K tok/min                  |
| OpenAI Tier 1 | ENTITY=10, SYNTHESIS=5   | 500 req/min on mini, 60 on gpt-4o         |
| Ollama     | ENTITY=1, SYNTHESIS=1       | Single GPU, no parallelism                  |

If you see `429` errors in the logs, halve the concurrency settings and retry.

## Full Groq model reference (April 2026)

| Console name         | API model ID                                | Speed       | Context | Category          |
|----------------------|---------------------------------------------|-------------|---------|-------------------|
| GPT OSS 120B         | `openai/gpt-oss-120b`                       | 500 tok/s   | 131K    | Text/Reasoning    |
| GPT OSS 20B          | `openai/gpt-oss-20b`                        | 1,000 tok/s | 131K    | Text/Fast         |
| Llama 4 Scout        | `meta-llama/llama-4-scout-17b-16e-instruct` | 750 tok/s   | 131K    | Text/Vision/Tools |
| Llama 3.3 70B        | `llama-3.3-70b-versatile`                   | 280 tok/s   | 131K    | Text (legacy)     |
| Qwen 3 32B           | `qwen/qwen3-32b`                            | 400 tok/s   | 131K    | Reasoning         |
| Whisper Large v3     | `whisper-large-v3`                          | —           | —       | Speech to Text    |
| Whisper Large v3 Turbo | `whisper-large-v3-turbo`                  | —           | —       | Speech to Text    |
| Orpheus English      | `playai-tts`                                | —           | —       | Text to Speech    |
