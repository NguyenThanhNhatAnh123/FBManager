---
name: us-video-review
description: Review videos for US Facebook audiences from creator and viewer perspectives, inspect picture and sound, assess copyright evidence, and write natural American English captions before authorized publishing or scheduling.
---
## Via Page run policy

Với run Page Manager, canonical page-shirt-zoom-8s và run-input quyết định caption URL-only/count/uploader. Audio policy human_approved_exact_pcm_v1 dùng phê duyệt người dùng đã nghe reference và kiểm tra PCM hash độc lập; không được tuyên bố AI vừa nghe. Lượt legacy giữ bằng chứng nghe thật.


# US video review

Review the actual file before writing a caption or publishing. Respond in the user's language; write audience-facing copy in natural American English unless requested otherwise. Apply to files supplied by the user; do not assume every file in a folder is authorized for upload.

## Establish the evidence

- Identify the selected file, destination Page, audience, and intended format. If a single new file is clearly designated, use it without asking the user to select it again.
- Measure duration, coded dimensions, active picture dimensions, frame rate, codecs and bitrate. Distinguish a Full HD container from the resolution of the actual image; detect embedded black bars.
- Inspect timestamped frames including beginning, middle and end. Inspect finer detail at native size for faces, clothing, small text, logos, watermarks, compression and motion artifacts. Sampled stills cannot establish smooth motion throughout.
- Decode the full file to check errors. Measure integrated loudness, true peak, loudness range and silence where useful. Treat roughly -14 to -16 LUFS and <= -1 dBTP as editorial reference points, not guaranteed Meta requirements. Account for genre and avoid normalizing solely to a number.
- Listen to speech, music, noise, distortion, transitions and sync when audio perception is available. If it is unavailable, report that explicitly. Numeric audio measurements cannot prove intelligibility, pleasant sound, track identity or absence of copyright issues. Never invent a transcript or claim to have listened.

## Two review perspectives

**Creator/editor:** Evaluate the first 1–3 seconds, clarity of subject, framing for mobile, pacing, readable English subtitles, audio mix, authenticity, ending/loop, and fit between promise and payoff. Give timestamped, actionable edits with expected benefit. Do not silently crop, replace music or alter the source: create a separate version when editing is authorized, preserve the source, and verify it.

**US viewer:** Use the perspective of the intended audience as an editorial simulation, not a claim to represent all Americans. Explain whether a viewer can understand the scene immediately, what makes them keep watching, emotional payoff, credibility, accessibility without sound, and what may confuse or alienate them. Do not infer people's ethnicity, tribal membership, motives or endorsement from appearance. For Native American topics, avoid pan-tribal stereotypes, invented cultural facts, or claims that a scene depicts a specific community without evidence.

Optional scores are subjective: give separate creator/viewer scores out of 10 with reasons and confidence. Mark unassessed dimensions instead of folding them into a fabricated precise score. Distinguish technical pass from editorial recommendations and rights uncertainty.

## Rights and platform checks

Ask whether the user created the source footage, voice and music or holds licenses covering Facebook, commercial use and relevant territories. “I edited it”, a credit, a watermark removal, or no platform warning does not establish permission. Track each element separately as user-declared original, documented licensed, unknown, or observed claim.

Check visible third-party marks and supplied license evidence. Use current official Meta documentation when stating platform copyright or originality rules. Do not declare “copyright free”, “100% safe”, or promise monetization based on inspection or a successful upload. Copyright permission and Meta originality/monetization eligibility are different questions. Do not assume an in-app music license transfers to another platform.

If upload is authorized, inspect any checks actually surfaced by Meta. Record whether a check completed, is pending, was unavailable, or reported a claim; absence of an alert is not a clean bill of rights. Explain practical uncertainty proportionately, without treating every uncertainty as a mandatory legal approval flow. Never evade a claim through pitch, speed or watermark changes.

## Caption

Offer a recommended concise caption and, when useful, one alternative. Base it on observed content; do not invent dialogue, identities or outcomes. Lead with the scene or payoff, use American spelling and conversational phrasing, and add a genuine question only when it fits. Prefer a few relevant hashtags over generic trending tags or engagement bait. For provocative messages on clothing, discuss the observable moment without escalating accusations against individuals. Include necessary context or AI disclosure only when supported and applicable.

## Publishing and scheduling, only when requested

Review the Page name and file in the live composer. Use the user's existing publishing authorization; do not impose a second blanket approval solely because this skill was invoked. An instruction to review alone does not authorize uploading or posting.

Resolve ambiguous dates and time zones before committing. The US has multiple time zones; never translate “US time” into one by assumption. Record the user's date/time/IANA zone, convert to the UI's displayed zone with DST, and verify the saved result. If the requested time has passed, ask for a replacement instead of choosing another day.

Use the supported browser upload workflow and current page state. Inspect caption, destination, preview and timing before the final action. Avoid repeated submissions: if a commit's result is uncertain, inspect scheduled content for a matching item before retrying. Report a saved schedule only after visible confirmation. Otherwise explain the exact blocker and preserve an appropriate draft/handoff.

## Deliverable

Provide a compact evidence-based review: file facts; creator view; viewer view; image/audio findings with limits; rights evidence/status; prioritized fixes; recommended caption; and actual publishing status with date/time/zone. Save supporting outputs only when useful. Keep “measured”, “observed”, “user stated”, and “not verified” distinct.
