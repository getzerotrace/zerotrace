# ADR 0002: Deterministic-first, fail-closed, bounded LLM

**Status:** accepted

**Decision:** The LLM is never on the blocking path for HIGH findings and can
never turn a BLOCK into an ALLOW. On any error the system fails closed.

**Rationale:** a local 3B model is not a security boundary; treating it as advisory
keeps correctness bounded by the deterministic layer while still cutting false
positives on ambiguous MEDIUM findings.

**Amendment (v0.2):** the model may also *escalate* a MEDIUM finding to BLOCK
(`REAL_SECRET` ≥ 0.8). Escalation only adds friction, so it is safe in the same trust
model. It is configurable via `model.can_escalate`.
