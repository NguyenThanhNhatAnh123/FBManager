---
name: imagegen
description: Generate or edit bitmap product images with the native ImageGen tool for this project; preserve supplied artwork, quote and transparency. This project variant requires the built-in tool and does not package an API/CLI fallback.
---

# Native ImageGen for the Via producer

Adapted from the installed native Image Generation skill. Read [prompting guidance](references/prompting.md) and [sample prompts](references/sample-prompts.md) when composing a production request. The source license is [Apache 2.0](LICENSE.txt).

Use the built-in `image_gen` tool for generated photos and edits. A copied skill or a feature flag does not prove that the child runtime exposes the tool. If the actual tool is unavailable, preserve progress and report `needs_review`; do not replace it with placeholders, browser automation or a credential-based API/CLI path.

For a local input, inspect the original with `view_image` before editing. Give each input an explicit role: the artwork controls the exact design, lettering, ink colors, proportions and seams; style assets control only scene, props, fabric and lighting. Keep the original artwork as a reference in every generation call. A previously generated photo must not become the sole design source.

Create one separate image per assigned variant, with its own scene/concept, rather than a collage. `variant_count` from the canonical Page run wins over a subskill's seven-image default. For transparent outputs, request and preserve real alpha. For extraction that must retain source pixels, use the project batch-remove-background skill and its deterministic masks rather than regenerating lettering.

Inspect each result at useful detail: exact quote, diacritics, font, illustration, print placement, fabric color and full print visibility. Iterate a targeted correction if needed and retain unresolved QA honestly. Do not claim that a rename, trivial crop or duplicate file is an independent creative.

The native tool normally saves under the Codex generated-images directory. Copy each selected final artifact into the authorized run directory, preserve alpha, compute its SHA-256 and use the copied file in the manifest. Do not overwrite a previous uncertain artifact; use a revision filename. Report actual saved paths and the tool used.

The producer must follow the canonical page-shirt-zoom-8s run-input, fixed shop, caption, count, uploader, audio and QA contracts. This image skill does not authorize campaign upload, Meta calls, scheduling or publishing.
