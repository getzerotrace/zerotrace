# Moving the AI tie-break to AWS

Today each laptop runs Qwen2.5-Coder 3B in Docker (Ollama). Once a company AWS account is
available, one shared inference endpoint removes Docker and the ~2 GB model download from every
laptop. **The switch is configuration only.** The client already speaks both Ollama and the
OpenAI-compatible API.

## What leaves the laptop

Nothing sensitive. For each MEDIUM finding the request contains:

- a feature block: identifier name, language, file class, value *shape* (length, charset,
  entropy, skeleton like `aaAA99-aa`, dictionary-word ratio), known public prefix only
  (for example `sk_test_`), and whether an env lookup is nearby;
- a code window where every string literal, high-entropy token, unquoted config value and
  detected secret is masked (`"<STR len=24>"`, `<TOKEN len=40>`, `<CANDIDATE>`).

The client refuses to send a prompt that still contains a detected value, and property tests
enforce it (`tests/test_llm.py::test_no_value_ever_reaches_a_prompt`). HIGH/CRITICAL findings are
never sent at all.

## Client configuration (org policy)

```yaml
locked: [model.endpoint, model.allow_remote]
model:
  runtime: openai                 # vLLM / LiteLLM / gateway speaking /v1/chat/completions
  name: Qwen/Qwen2.5-Coder-3B-Instruct
  endpoint: https://zerotrace-inference.internal.example   # must be https for non-localhost
  allow_remote: true              # explicit opt-in; without it remote endpoints are refused
  auth_env: ZEROTRACE_MODEL_TOKEN # bearer token from env / credential helper, never in yml
  timeout_seconds: 8              # network budget; on timeout the finding WARNs (fail closed)
```

`zerotrace doctor` shows the endpoint, whether it is remote, round-trip latency and the model
identity.

## Reference architecture

```
laptop ──(corp VPN / PrivateLink, TLS)──► internal ALB (HTTPS, auth) ──► EC2 GPU (g5/g6.xlarge)
                                                                         vLLM or Ollama
                                                                         Qwen2.5-Coder-3B-Instruct
```

- **Compute:** one `g5.xlarge` or `g6.xlarge` (a single 24 GB GPU) serves a 3B model with a lot
  of headroom. Put two instances behind an Auto Scaling group if availability matters.
- **Serving:** vLLM (`--guided-decoding` for JSON-schema output, OpenAI-compatible) or Ollama
  (`runtime: ollama`). Pin the model revision and record its digest.
- **Network:** private subnets only. Reach it through the corporate VPN or PrivateLink via an
  *internal* ALB with ACM TLS. No public IP.
- **Auth:** a short-lived bearer token (ALB OIDC or an API gateway), distributed to laptops
  via MDM or a credential helper into `ZEROTRACE_MODEL_TOKEN`.
- **Logging:** CloudWatch metrics for latency and error rate. **Disable request-body logging**
  on the ALB/gateway and in vLLM, because even redacted payloads have no business in logs.
- **Alternatives:** SageMaker real-time endpoints (vLLM/TGI container) behind a small gateway,
  or Bedrock Custom Model Import. Check Qwen2.5 support in your region.

## Rollout checklist

1. Stand up the endpoint and run `zerotrace eval --model <name>` against it. Record
   unsafe-allow rate, noise removed, and p50/p95 latency next to the laptop baseline.
2. Push the org policy with `model.endpoint` locked, and keep local Ollama as the documented
   offline fallback.
3. Update `docs/THREAT_MODEL.md` sign-off: the egress claim becomes "only redacted shape metadata,
   over TLS, to a company-owned AWS account".
