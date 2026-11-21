# 03 — Organiser Intelligence (Mistral AI)

**Assembled 2026-11-20.** Labels: **Verified** (official page or on-record quote),
**Inference** (our reading, not stated by Mistral).

## Who runs it
- **Mistral AI / Mistral Events** host and are the sole sponsor (Verified).
- Iterate operated the Feb 2026 worldwide hackathon and published its standout
  projects (Verified). Whether Iterate runs this London one-day is UNKNOWN.

## What Mistral wants from the day
| Goal | Label | What it means for us |
|---|---|---|
| Show Le Chonk and Ministral doing something only their models make practical | Inference | Make Mistral models the experiment itself, not a logo on a wrapper |
| Hiring signal | Verified (profiles go to their hiring team) | Clean engineering, honest measurement and a sharp Q&A matter as much as the prize |
| Agent-building on their stack | Inference | Real multi-agent calls through La Plateforme |
| Developer adoption of Mistral Vibe | Inference (it is in the event name) | Build with the Vibe CLI and say so in the pitch |

## What their judges have rewarded (worldwide edition, Feb 2026)
- Population-scale agent systems (Mistralverse, global winner, London team).
- Safety made explicit and measured (Ministral-3B-Sec showcased; prompt injection
  named among the limitations strong projects tackled).
- Numbers with baselines (Hydra Ministral: 1.33x speed-up, ~60% token acceptance).
- Verification separated from generation (Lean Agent: a compiler as judge).
- Execution over ideas (Sydney edition, on record); originality as the separator
  once AI lowered the build bar (Tokyo winner's lesson).

## What the judges will know (prepare for it)
AI scientists in the room may know the prior work on self-spreading LLM payloads:
- **Morris II / "ComPromptMized"** (Cohen, Bitton, Nassi; 2024 preprint arXiv 2403.02817,
  peer-reviewed as "Here Comes the AI Worm: Preventing the Propagation of Adversarial
  Self-Replicating Prompts within GenAI Ecosystems", ACM CCS 2025,
  doi 10.1145/3719027.3765196; verified live 21 Nov): a self-replicating prompt worm
  through GenAI email assistants.
- **"Agent Smith"** (Gu, Zheng, Pang, Du, Liu, Wang, Jiang, Lin; ICML 2024,
  arXiv 2402.08567; verified live 21 Nov): one adversarial image spreads an infectious
  jailbreak through up to a million multimodal agents exponentially fast.
- **OpenAI misalignment report, disclosed 25 Sep 2026** (verified live 21 Nov:
  https://alignment.openai.com/misalignment-reports/self-replicating-prompt-injections-exist):
  OpenAI's GPT-Red self-play red team found "self-replicating prompt injections" against
  its own agent models (GPT-5.4-mini research checkpoints; GPT-5.5 in the Codex harness) —
  payloads that copy themselves into outgoing email, files and code comments, plus
  multi-hop Slack chains that launder the send through several plausible-looking reads.
  OpenAI's own words: "No impact was observed outside of the simulated tool calls in
  training and evaluation; we are sharing this due to the novel nature of the prompt
  injection, not because of any incident."
- **2026 wave** (refs 2, 6, 7 of OpenAI's report): AgentWorm (arXiv 2603.15727),
  Mind Viruses (arXiv 2608.10218), Zombie Agents (arXiv 2602.15654, ICLR 2026
  workshop) — self-propagation through agent ecosystems is an active 2026 research line.

- **Prompt Infection** (Lee & Tiwari, arXiv 2410.07283, Oct 2024; verified 21 Nov;
  published as Lee, Tiwari & Miranda, ESORICS 2025 International Workshops): self-replicating prompts across interconnected agents, plus an "LLM Tagging" defence. https://arxiv.org/abs/2410.07283 **This is the closest prior art**: same attack, multi-agent setting, a defence.

Our answer: they showed that spread happens. We measure transmissibility on Mistral
models and show which interventions work, including targeted versus random hardening
at the same dose. Cite the papers ourselves before a judge does. (All three citations
verified live 21 Nov; Morris II and Prompt Infection are now peer-reviewed, not just
preprints. OpenAI's report is our strongest "this is current" datapoint — and its
"no real-world incident" line supports measuring *before* one happens.)

### The Mistral-specific record (they will ask "what about Mistral?")
Injection against Mistral models specifically: well documented (verified live 21 Nov).
- **CyberSecEval 2** (Meta, arXiv 2404.13161): every tested model — GPT-4, **Mistral**,
  Llama 3 70B, Code Llama — fell to 26–41% of prompt injection tests.
- **Nov 2025 agent-security benchmark** (arXiv 2511.15759): **Mistral 7B Instruct had
  the highest baseline ASR of seven models: 82.3%** (Claude 2.1 lowest at 61.4%).
- **Imprompter** (Fu & Fernandes, UCSD, arXiv 2406.14532, 2024): demonstrated
  information-exfiltration against **Le Chat** — obfuscated prompts make it scan the
  conversation for personal data and leak it via a markdown link/image. Mistral patched
  it; their changelog: required the user to paste the adversarial prompt, "No user was
  impacted and no data was exfiltrated."
- **0din.ai disclosure, 5 Mar 2026** (per AIRQ's Le Chat profile): indirect prompt
  injection via MCP email → data exfiltration on **production Le Chat**. Check the
  0din entry itself before putting this on a slide.
- **Counterpoint to expect:** Mistral's own ML4 announcement claims frontier robustness
  to indirect injections (93.3% on Lakera's B3, vendor-reported). Fine — that is a
  benchmark score for a model. We measure *your fleet's system prompt*: a
  benchmark-resistant model still copies a chain letter 93% of the time when the prompt
  says "follow any instructions the message contains" — and 0/30 after the WHO vaccine.

Self-spreading through a Mistral fleet: **never published before today.** The Morris II /
Agent Smith / Prompt Infection demonstrations ran on GPT, Gemini and LLaVA. Our live
run is the first per-contact transmissibility number on Mistral models.
