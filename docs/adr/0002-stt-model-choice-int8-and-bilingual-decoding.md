# ADR 0002 — Speech-to-text: faster-whisper `base`, int8 on CPU, one decode per utterance, parallel en/tr decoding with vocabulary prompts

- Status: accepted
- Date: 2026-11-09

## Context

STT is the largest compute stage between "the user stopped talking" and "GIDEON starts answering". GIDEON has to
run on an ordinary laptop CPU with no GPU, understand **English and Turkish** without the user picking a language,
and recognise short commands reliably enough for a regex intent router. Constraints and facts measured early:

- Whisper always encodes a 30 s window, whatever the clip length. A 1.5 s command costs about the same encoder time as
  a 10 s one.
- Whisper's language-ID head failed on short Turkish clips. In our fixtures it labelled "Saat kaç?" Romanian (p=0.14;
  en 0.030 vs tr 0.024) and "Notlarımı oku" Portuguese (en 0.059 vs tr 0.006, so even the en/tr argmax is wrong).
  Language detection also costs about one extra encoder pass (≈ 430–630 ms with `base` here), which is as much as
  the decode itself.
- `base` produced "zamanlayıcı kor" for "kur" and "durumun edir" for "durumu nedir". The router has to tolerate
  that, or the decoder has to be nudged.

## Options considered

**Engine / precision**

1. openai-whisper (PyTorch, fp32 on CPU): the reference, but several times slower than CTranslate2 on CPU and a
   heavy dependency.
2. whisper.cpp: fast, but bindings and packaging per OS. Python support is weaker than faster-whisper's.
3. **faster-whisper (CTranslate2) with int8 weights on CPU** (chosen). int8 quantisation is CTranslate2's
   recommended CPU mode. It has `num_workers` for true parallel decodes (CTranslate2 releases the GIL), built-in
   Silero VAD trimming and `initial_prompt`.

**Model size.** Measured with `python -m gideon bench-stt` (the command span of each of the 9 fixtures plus 300 ms
padding, 5 rounds, n = 45 per row; same machine as [benchmarks](../benchmarks.md)):

| model | languages | prompt | threads | median ms | p95 ms | intent correct | language correct |
|---|---|---|---|---:|---:|---:|---:|
| tiny | en+tr parallel | yes | 4×2 | 618.9 | 1289.2 | 40/45 | 45/45 |
| **base** | **en+tr parallel** | **yes** | **4×2** | **931.8** | **1988.0** | **45/45** | **45/45** |
| base | en+tr parallel | **no** | 4×2 | 1398.5 | 2115.7 | 40/45 | 45/45 |
| base | en+tr parallel | yes | **8×2** | 1233.6 | 3935.4 | 45/45 | 45/45 |
| base | fixed (oracle language) | yes | 8×1 | 547.0 | 689.4 | 45/45 | 45/45 |
| small | en+tr parallel | yes | 4×2 | 1848.2 | 5846.8 | 45/45 | 45/45 |

**Language strategy**

1. Let Whisper detect the language. One API call, but wrong on short Turkish clips and an extra encoder pass.
2. Detect first, then decode with the detected language. Same accuracy problem, two passes always.
3. Fixed language from configuration. Fastest (one decode, 547 ms median) and accurate, but monolingual.
4. **Decode in every supported language in parallel and keep the decoding with the highest mean token
   log-probability** (chosen as the default; option 3 stays available with `GIDEON_STT_LANGUAGE`). The model scores
   its own transcript: "Notlarımı oku." scored −0.16 as Turkish and "Not very much." scored −1.06 as English.

**Streaming partials vs. one decode.** Re-decoding a growing buffer every few hundred ms gives live captions but
multiplies the work, because of the fixed 30 s window, and competes with the final decode for the same cores.
Commands are 1–3 s long, so GIDEON decodes once after the endpoint.

## Decision

- faster-whisper **`base`, int8, CPU**, beam size 1, `condition_on_previous_text=False`, `without_timestamps=True`,
  `vad_filter=True` (Silero trims leading and trailing silence and stops hallucinations on empty input). The model is
  loaded and warmed up at start-up: the first inference is several times slower and must not land on a user.
- **Parallel bilingual decoding** with `num_workers=2`, and **4 threads per decode** on a 16-thread CPU (physical
  cores ÷ decodes). 8×2 was slower *and* had a 2× worse p95 (oversubscription), so the default is
  `logical_cores / 2 / languages`, capped at 8.
- **Vocabulary prompt per language**, built from the installed skills' `IntentSpec.examples`
  (`SkillRouter.vocabulary`). It raised intent accuracy from 40/45 to 45/45 and *reduced* latency (1398 → 932 ms
  median). Unprimed decodes of short Turkish clips ran longer and produced garbled tokens.
- `tiny` is a supported setting (`GIDEON_STT_MODEL=tiny`) for weak machines. It is 34% faster here but missed 5 of 45
  intents. `small` was right on everything, as `base` was, but took twice as long with a 3× worse p95.

## Consequences

- The bilingual default costs about **1.7×** a monolingual decode (932 vs 547 ms median). Parallelism does not make
  the second language free on this CPU: the two decodes compete for memory bandwidth and the shared cores. Users who
  speak only one language should set `GIDEON_STT_LANGUAGE`. That is the single biggest latency lever after the
  endpoint hangover.
- The prompt is a bias. It helps the phrasings the skills know, and it could pull an unrelated utterance toward a
  known command. The fixtures share wording with the examples (7 of 9 are close), which is why the matrix also
  reports the unprimed row. A held-out set of real recordings is listed under limitations.
- Next steps: share one encoder pass between both language decodes (CTranslate2 exposes `encode` and `generate`
  separately), which would bring the bilingual cost close to the monolingual one. Also try a distilled model
  (distil-whisper does not cover Turkish today).
