# Seedance 2.5 multi-shot montage workflow

**Seedance 2.5 is available through [ArgoLink](https://argolink.io/gh-seedance-2-5-montage-workflow).** This repository turns a reviewed YAML shot list into independent asynchronous jobs, keeps a shared reference-image set in each request, downloads clips, and assembles them with ffmpeg. It is a transparent script, not an application or a promise of automatic character consistency. Last checked: 2026-10-07.

## Why this shape

A montage has two failure domains. The API runner owns authentication, request IDs, rate limits, and resumable polling. The local assembler owns ordering, file paths, codecs, and audio. Keeping them separate means a failed sixth clip does not force the first five to render again, and an ffmpeg error does not cause another paid POST. This repository validates the plan and provides the assembly primitive; your runner persists one JSON record per shot.

## Plan format and lifecycle

The example contains six five-second shots, a common character image, and a location image. Every shot has a stable ID and a prompt that says what must remain continuous. Submit with model seedance-2.5, resolution 720p, and adaptive ratio when frame or reference inputs require it. Store the returned request_id next to the ID. Poll GET /videos/{request_id} until pending becomes done, failed, or expired; only then download content. Unknown or ambiguous POST outcomes need investigation before a retry.

## Assembly

Install Python 3.9+, PyYAML, and ffmpeg. Run the command: python -m montage.workflow examples/six-shots.yaml --clips clips/01.mp4 clips/02.mp4 --output episode.mp4. The utility writes an ffconcat manifest with JSON-escaped absolute paths, uses safe 0, and encodes a broadly playable H.264 file. It does not claim to implement transitions: add and test a filter graph separately when a creative brief needs one. Music should be mixed only after checking its licence and loudness.

## Cost estimate

The checked ArgoLink effective 720p rate is $0.196 per output second. Six times five seconds therefore estimates $5.88 before reference-video seconds or retries. The estimate is arithmetic, not a receipt. Failed, rejected, and expired jobs are not charged under the service contract. A reference video can add billable input duration, so do not use the output-only estimate for an edit or extension. The live model page is authoritative.

## Validation and limits

Run python -m unittest discover -s tests. A real provider call and ffmpeg run are intentionally not performed in tests. Seedance 2.5 accepts 4–30 seconds and 480p/720p/1080p; prompt, image, video, and audio limits belong to the current model page. Recheck them before copying this plan to another model. Local files are not silently uploaded: create a media URL through the documented upload flow first.

## FAQ

**Does this generate the videos?** Your job runner performs submission and polling; this repository validates and assembles. **Why send the same references?** It gives the model a continuity signal, but identity is not guaranteed. **Can I run all shots concurrently?** Only with an explicit semaphore respecting account limits. **Where are results?** Your runner should put each MP4 at the path used by the clips option; the assembler never invents missing files.

## Operator checklist

Before the first POST, record the plan checksum and the exact model-page revision you used. Give each shot a durable ID such as opening or clue rather than relying on an array index; this lets you resume a partial episode after a process restart. Persist the request body and returned ID together, but keep the API key in the environment or a secret manager. When a status response is pending, wait at least the configured interval. On 429 or 503, honor Retry-After and do not create a second job. On failed or expired, preserve the response for diagnosis. A local deadline means “resume this ID later”, not “submit a replacement”.

For an edit or extension, calculate input-video seconds separately from output seconds and use the provider's current billing rule. For a normal text or image shot, the six-shot estimate above is a useful planning number only. Audio files and image references can have independent size and count limits; the current page wins over this example. Keep original clips until the assembled file is inspected because ffmpeg can report success while a creative requirement is still wrong.

The assembler intentionally has no hidden network access, no key loading, and no automatic deletion. That makes it suitable for a CI job after the API runner has written a manifest. If ffmpeg is unavailable, the plan and downloaded clips remain usable in another editor. If PyYAML is unavailable, installation fails with a clear message instead of silently accepting malformed YAML.
