# ADR-0009: Live open-model inventory, eval-based selection, one gateway

Status: Accepted (owner request, 2026-10-08). Spec references: §4 (provider-neutral model gateway), §12
(budgets, no unreviewed model substitution), §21 (cost per completed unit). Builds on ADR-0007.

## Goal

Always know the full, current set of open models we could use, pick the cheapest one that meets the bar for
each task, and run nothing ourselves that we do not have to.

## Inventory (the Model Scout keeps it current)

| Source | What it lists | How we reach it | Checked 2026-10-08 |
|---|---|---|---|
| Workers AI catalog | Open models hosted inside Cloudflare (text, embeddings, vision, speech) | Cloudflare API model search | 81 models (e.g. DeepSeek V4 Pro/Flash, gpt-oss-120b, Qwen 3.8, Kimi K2.7) |
| NVIDIA API catalog | NVIDIA-hosted open models, including Nemotron and NIM-packaged models | `https://integrate.api.nvidia.com/v1/models` | 80 models (e.g. Nemotron 3 Ultra 550B, Kimi K3, GLM-5.3, DeepSeek V4.1 Flash) |
| Hugging Face Inference Providers | Open models served by many providers, with per-provider pricing and context | `https://router.huggingface.co/v1/models` | 137 models across 14 providers (Groq, Cerebras, Together, Fireworks, DeepInfra, …) |
| Hugging Face Hub | Every published trained model (new and trending, licence, size) | Hub API | Discovery only: a model must also be deployable through one of the routes above, or self-hosted |
| Ollama library | Models for local development and self-hosted deployments | Ollama registry | Existing scout source |
| Anthropic Models API | Frontier models for Tier-3 reasoning | Existing scout source | |

Each inventory row records:

- model id and family
- licence, with a flag for licences that restrict commercial use
- parameter count and active parameters
- context window and modalities
- every hosting route, with price per million input and output tokens
- the provider's data terms (zero retention available? training on inputs?)
- first-seen date and last-seen date

When a model disappears from a provider, it is marked retired and its routes are removed.

## Selection

- The Model Scout benchmarks candidates on **our own** evaluation sets for each role: classify, extract,
  answer with citations, draft, reason. It uses held-out cases, scored deterministically where possible.
- Score is accuracy *and* **cost per correct result** *and* p95 latency. For each role and data class, the
  champion is the cheapest route that meets the role's accuracy bar.
- Promotion stays reversible and recorded (existing Foundry flow). In production, a promotion that changes
  which provider receives taxpayer data needs platform-reviewer approval. Model weights are data; a provider
  is a subprocessor.

## Routing and cost

- Every call goes through **Cloudflare AI Gateway**:
  - Workers AI natively
  - NVIDIA and Hugging Face as OpenAI-compatible **custom providers**
  - Anthropic natively

  We get one endpoint, one log, caching, retries and fallbacks, per-tenant and per-role **spend limits**,
  and zero-data-retention controls.
- The ADR-0007 data-class policy filters routes *before* selection. Tax return information may only go to
  in-platform Workers AI, or to routes whose zero retention is verified and covered by consent.
- Cost levers:
  - small models for classification and extraction
  - response caching for repeated rules questions
  - batch endpoints for overnight work
  - escalating to a larger model only when a deterministic check fails
  - **no GPUs to operate**
- Self-hosting (NVIDIA NIM or vLLM on dedicated GPUs) becomes a deployment option only for a tenant whose
  contract requires it and pays for it.

## Consequences

- New open models become available to us as soon as any provider serves them. The scout surfaces them, the
  evals decide, and nothing is switched without a measured win.
- A provider outage falls back along the role's ranked route list. If no allowed route remains, the task
  pauses visibly (spec Q34); it never escalates silently to a disallowed provider.
