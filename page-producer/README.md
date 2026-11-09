# Via Page producer

Canonical producer root: the `page-producer` directory beside `meta-page-manager` in the Via installation. Scripts derive this root from their own location, so moving the complete installation does not depend on a Windows username or current working directory.

The Page Manager worker reads `.agents/skills/page-shirt-zoom-8s/SKILL.md` and a frozen `run-input.json`. Each authorized run creates its assigned 1–14 distinct images and eight-second videos with Blueox product-link captions. New manual-review runs use the bounded rolling coordinator and emit a pending human-review receipt after each render, as described in [.agents/skills/page-shirt-zoom-8s/references/manual-review-v1.md](.agents/skills/page-shirt-zoom-8s/references/manual-review-v1.md). A human must approve each video before publication. Historical runs retain their frozen execution mode and complete-set QA contract. Installing this package does not start generation, upload, scheduling or publication.

Project resources:

| Resource | Physical project path | Purpose |
|---|---|---|
| Page pipeline | `.agents/skills/page-shirt-zoom-8s/` | Fixed count, uploader, FFmpeg renderer and handoff contract |
| Native image generation guidance | `.agents/skills/imagegen/` | Built-in ImageGen only; actual host capability is still required |
| Normal shirt photography | `.agents/skills/realistic-shirt-photos-9x16/` | 56 scene choices; Page run count overrides standalone seven |
| Closeup shirt photography | `.agents/skills/realistic-shirt-photos-closeup-9x16/` | Readable print and close composition |
| Boutique photography | `.agents/skills/ao-cao-cap/` | Unworn shirt in a boutique/showroom |
| Pixel-preserving extraction | `.agents/skills/batch-remove-background/` | Deterministic mask/alpha extraction to 2400×3200 |
| Video review guidance | `.agents/skills/us-video-review/` | Visual/technical review and honest audio evidence |
| Shared style references | `assets/shirt-style-references/` | One physical copy shared by normal/closeup catalogs |
| Handoff validator | `page-manager-bridge/bridge.py` | Offline data/file/QA validation; no generator or publisher |
| Rolling coordinator | `..\meta-page-manager\backend\page_manager\producer_stages.py` | Bounded image → render → QA queues and run-bound receipts |
| Stage profiles | `..\meta-page-manager\backend\page_manager\provider_profile.py` | Only each stage's skills; native ImageGen only for the image stage; MCP disabled for all stage workers |
| FFmpeg and FFprobe | `..\tools\ffmpeg\bin\` | Reviewed 9.0.2 binaries, exact imported SHA-256 |
| Audio input | `..\media\audio\source-library.json` | Only cataloged local files with matching filename/size/hash |
| Optional uploader source snapshot | `..\tools\podsach-uploader-code\` | Code archive; runtime config/auth/state were excluded |

The local `VENDORED-MANIFEST.json` records source paths, hashes, adaptations and available licenses. The package provenance test uses this manifest and the local audio catalog.

Use Python 3.12+ and install `meta-page-manager/requirements-producer.txt` into the server's project environment (including Pillow and NumPy). Legacy camera QA uses that same Python by default; `PM_CAMERA_PYTHON` may select one absolute installed Python executable with NumPy. An invalid explicit override blocks QA. No runtime from a user's Codex cache is required. `PM_CODEX_CLI` may select one absolute native Codex executable; otherwise discovery checks PATH and the current user's native npm installation. `PM_PODSACH_TOOL_ROOT` may select the absolute installed local uploader directory. Codex/ImageGen are host capabilities; their executables, credentials and global configuration are not bundled here. See the deployment instructions for server setup and content migration.

Audio reuse requires a human-approved eight-second reference and exact independently decoded PCM matching. Copying source audio or these tools never creates approval. The Page Manager registry, uploader sessions, Meta credentials and run state are maintained separately by their owning services.

Safe local validator tests: run `python -B -m unittest discover -s tests` from `page-manager-bridge`. They use inert fixtures and do not call the uploader, generator or Meta.
