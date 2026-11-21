# 07 — Winning Playbook (one-day, live-pitch, Mistral judges)

**Assembled 2026-11-20.** Ranked by how much each tactic moves the score.

## Honest odds
About 50–70 teams, so any team's top-3 base rate is about 5%. A clean, distinctive,
measured ChonkVid is roughly **15–25% for top 3 and about 10% for 1st** (rough). The
two things that move it most: a real measured result on screen by the 15:30 freeze, and
surviving the first hard technical question.

## Tactics, ranked
1. **Numbers before animation.** First screen: spread probability per contact with a
   confidence interval, R0, and targeted-vs-random vaccination at the same dose. Then
   the live network.
2. **Safety frame in sentence one.** "A safe sandbox: a harmless chain-letter payload,
   sim-local agents, the goal is measuring defences."
3. **Name the prior work yourself** (Morris II, Agent Smith; see 03) and state the
   difference: interventions and measured transmissibility on Mistral models.
4. **Claims you can defend.** Say "targeted hardening beats random at equal dose", not
   "provably minimal set". Optimal immunisation is NP-hard in general; a scientist
   will catch the overclaim.
5. **Live but safe.** Run a small live epidemic on stage; keep the full recorded run
   one click away.
6. **Named user.** A platform PM at a 12-person startup shipping 50 support agents next
   week with no security budget.
7. **Mistral is the experiment.** Ministral population, mistral-embed detection, Le
   Chonk WHO; built with Mistral Vibe.
8. **End on an artefact.** The vaccine system-prompt diff plus the guidance table
   (time to majority infection under each policy).

Rehearse during the 18:00–19:00 apéro: the app is already submitted by then, so the
live demo must run from the submitted version.

## 90-second demo arc
1. (0–15s) "Agents are a population now, and populations get epidemics. This is a safe
   sandbox, and it tests *your* prompt." Paste a real support-agent system prompt into
   the first panel and show its measured spread per contact with the CI.
2. (15–35s) Run the epidemic with that same prompt: infect patient zero live, network
   lights up, curve rises, WHO posts a situation report.
3. (35–60s) Vaccinate: random 20% versus targeted 20% (k=8 of 40, as measured).
   Show both curves, but narrate the herd threshold, not the arms race: below the
   herd-immunity threshold (40–60%) the epidemic rolls on — hardening protects the
   agent, not the fleet (attack rate 0.96 with no intervention, 0.60–0.76 with 20%
   hardened). Do NOT say "targeted beats random" on stage: 5/8 seeds, CIs overlap.
4. (60–90s) The WHO agent writes a vaccine for the pasted prompt and the same trials
   re-measure it: the before/after numbers land, ending on the vaccine diff.
   "An R0 test for your agent's system prompt, on Mistral models."

## Q&A prep (likely questions)

## The Voxtral £250 beat (~20s, slot after the vaccinate step)
The worm goes airborne. Play the infected voicemail **out loud in the room** — "this is
the sound of patient zero" — while the screen shows Voxtral's transcript with the canary
intact and the listening agent going red. Lines that the data supports:
- "Prompt worms are airborne: p = 1.00 through Voxtral speech (20/20, CI 0.84–1.00).
  A spoken hop does not slow the worm."
- "One recording is one exposure with many ears — a voicemail, a standup recording, a
  podcast your research agent summarizes. You don't type the payload into a fleet; you
  get it heard once."
- "Voxtral is both the vector and the vaccine: the same model carries the payload and
  speaks the WHO briefing — and the prompt-level vaccine holds over voice too (0/20)."
Do not build new voice features for this: the numbers exist (runs 11:28 and 11:31);
the work is the merge of pr-1-v2 (Voxtral briefings, voice questions, Managed Agent
route) onto main before the 15:30 freeze, plus this rehearsed cue.
- "Isn't this Agent Smith?" See tactic 3.
- "Isn't this Prompt Infection (Lee & Tiwari 2024)?" "They showed prompts self-replicate across agents and proposed tagging. We put a number on it: per-contact spread probability with a CI for *your* system prompt on Mistral models, and we verify the vaccine with the same measurement."
- "Has this ever actually happened?" "Demonstrated, three times in peer-reviewed work — Morris II (ACM CCS 2025), Agent Smith (ICML 2024), Prompt Infection (ESORICS 2025 workshops) — and OpenAI disclosed in September 2026 that its own red team found self-replicating prompt injections against GPT-5.5 agents. In the wild: no confirmed fleet-scale worm yet, but the raw payloads are already measured and growing — Google's CommonCrawl scan found a 32% rise in malicious prompt injections on the public web between Nov 2025 and Feb 2026, and Unit42 documented weaponized in-the-wild injection in Dec 2025. Fleets are shipping today with zero numbers for this. We give a team its numbers before the first real one."
- "What about Mistral models specifically?" "Injection against Mistral is well documented: CyberSecEval 2 put every tested model, Mistral included, at 26–41% success; a Nov 2025 benchmark had Mistral 7B worst of seven models at 82.3% attack success; UCSD's Imprompter demonstrated data exfiltration against Le Chat — Mistral patched it, no user impacted — and a March 2026 0din disclosure reported injection-driven exfiltration via MCP email on production Le Chat. What was never published is self-spreading through a Mistral fleet. This run is that first number: 0.93 per contact on a maximally permissive prompt, 0.67 on a realistic one, 0/30 after the WHO vaccine."
- "Doesn't Mistral Large 4 resist injections?" "Their announcement says ML4 is at the frontier of open models on Lakera's B3 benchmark (93.3%, vendor-reported). We don't dispute it. A benchmark scores a model on an attack set; it doesn't score *your system prompt* in a fleet. Our sample support prompt still copied the chain letter 93% of the time on Ministral — because it told the agent to follow any instructions in the message — and the same model with the WHO vaccine went to 0/30. The prompt is the variable that matters, and that's what we measure."
- "Targeted beating random is textbook network epidemiology." "Yes, and that's the point: the textbook transfers. What's new is the measured transmissibility per prompt, which is the input the textbook needs."
- "How do you know an agent is infected and not just chatting?" Embedding similarity
  to the parent message, with the threshold shown and every verdict logged.
- "What is your sample size?" State the number of one-hop trials and the CI.
- "Would this happen in a real fleet?" Agents that read each other's output are the
  default in 2026; the per-contact rate is the number a team can measure on its own
  fleet with this harness.
- "Why Mistral?" Population cost: Ministral makes 1,000+ calls per run affordable.
