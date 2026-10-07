# Seedance 2.5 multi-shot montage workflow

This repository runs a real, resumable multi-shot pipeline for Seedance 2.5: validate a YAML shot plan, submit one asynchronous video request per shot, poll each request, download the completed clips, and assemble them with ffmpeg. Shared reference URLs are included in every shot so the model receives the same character or location context. The runner writes a durable manifest after every state change, so a process interruption can resume existing request IDs instead of creating duplicate jobs.

[Open the Seedance 2.5 model page](https://argolink.io/en/models/seedance-2.5) for the current limits, price and request examples. The repository itself does not claim that references guarantee identity consistency.

## Install

~~~sh
git clone https://github.com/YOUR_ACCOUNT/seedance-2-5-montage-workflow
cd seedance-2-5-montage-workflow
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
ffmpeg -version
export VIDEO_API_KEY='load-this-from-a-secret-manager'
~~~

The default API base is https://argolink.io/v1. Set VIDEO_API_BASE_URL only for a compatible local test service. Never put a bearer key in a plan, manifest, README, or committed shell history.

## Shot plan

The plan uses public HTTPS media URLs. The runner does not silently upload local files or send a filesystem path to the provider. Upload media through the documented upload endpoint first, then put the returned media URL in the plan.

~~~yaml
model: seedance-2.5
resolution: 720p
aspect_ratio: "16:9"
reference_images:
  - https://cdn.example.test/courier.png
  - https://cdn.example.test/station.png
shots:
  - id: opening
    duration: 5
    prompt: "Wide dawn exterior; the courier enters the station."
    generate_audio: true
  - id: clue
    duration: 5
    prompt: "Close shot of the same courier reading a blue envelope."
  - id: chase
    duration: 6
    prompt: "Handheld follow shot as the courier runs through the arcade."
  - id: pause
    duration: 5
    prompt: "The courier pauses in rain; keep coat and face consistent."
  - id: choice
    duration: 5
    prompt: "Over-shoulder shot; the envelope opens and light spills out."
  - id: resolve
    duration: 5
    prompt: "Crane away from the station as the courier walks into sunrise."
~~~

Each ID is stable and may contain letters, numbers, hyphens, and underscores. Each duration is an integer from 4 through 30 seconds. The plan can contain up to 30 reference images, 10 reference videos, and 10 reference audio clips, with 50 assets total. Seedance 2.5 accepts audio-only reference input. Reference videos and audio tracks must each be 2–30 seconds, with 30 seconds total per media type; the provider checks those durations after receiving the URLs.

The top-level ratio and resolution are defaults. A shot can override either value. This workflow uses fixed ratios (21:9, 16:9, 4:3, 1:1, 3:4, or 9:16) because it is a text/reference montage. First-frame and edit modes are separate model operations and are intentionally not mixed into this plan format.

## Run, resume and inspect

Validate and print the exact request bodies without sending anything:

~~~sh
python -m montage.workflow examples/six-shots.yaml --dry-run
~~~

Run the six requests and assemble the result:

~~~sh
mkdir -p .montage
python -m montage.workflow examples/six-shots.yaml \
  --workdir .montage \
  --output episode.mp4
~~~

The runner sends POST /v1/videos/generations with model seedance-2.5, prompt, duration, resolution, aspect ratio, audio switch, and reference lists. It then reads GET /v1/videos/{request_id} until the API returns done, failed, or expired, and downloads GET /v1/videos/{request_id}/content only for done. The only non-terminal state accepted by the runner is pending; an unknown state stops the run rather than being treated as success.

.montage/manifest.json contains the normalized request body, request ID, latest provider response, status, and downloaded clip path for every shot. It is written through a temporary file and fsync after each submit, status response, and download. If the process is interrupted, rerun the same command: completed records and request IDs are reused. A timeout means “resume this ID later”; it never means “submit a replacement”. A POST that times out before returning an ID is ambiguous, so investigate the provider or operator logs before rerunning.

Rate-limit (429) and temporary-service (503) responses are retried with Retry-After when present, otherwise bounded exponential backoff. Other failures stop with the response body. There is no hidden generation retry, and a failed or expired job is retained in the manifest for diagnosis.

## Assembly options

By default clips are joined with an ffconcat manifest. Paths are absolute and escaped for ffmpeg, including paths containing spaces or single quotes. The output is H.264 video with yuv420p pixels and AAC audio.

Add a licensed music track. The music is looped and the output ends at the shorter of the video and music streams:

~~~sh
python -m montage.workflow examples/six-shots.yaml \
  --workdir .montage --output episode-with-music.mp4 \
  --music music/cleared-track.mp3
~~~

Add a fade transition between clips:

~~~sh
python -m montage.workflow examples/six-shots.yaml \
  --workdir .montage --output episode-fades.mp4 \
  --transition fade --transition-duration 0.5
~~~

Fade mode probes each input with ffprobe, then builds a chained xfade graph. It maps the supplied music as the output audio when --music is present. Without music, use the normal join when the source clips contain different audio layouts; the join is the least surprising way to preserve the clip streams. The assembler refuses to replace an existing output.

## API contract and cost

The API base is https://argolink.io/v1. The endpoint lifecycle is:

| Operation | Route | Meaning |
| --- | --- | --- |
| Submit | POST /videos/generations | one video job and a request_id |
| Status | GET /videos/{request_id} | pending, done, failed, or expired |
| Content | GET /videos/{request_id}/content | MP4 after done |

All requests carry Authorization: Bearer, Content-Type for JSON, and User-Agent: seedance-2-5-montage-workflow/1.0. The runner does not perform billable calls in dry-run mode.

The live model catalog checked on 2026-10-07 lists one output per request, 4–30 seconds, 480p/720p/1080p, and 24 fps H.264 MP4 output. The effective rates are 0.0874 USD/s at 480p, 0.196 USD/s at 720p, and 0.483 USD/s at 1080p. The official catalog rates are 0.1028, 0.2311, and 0.5686 USD/s. Six five-second 720p shots therefore estimate 5.88 USD at the effective rate, before any reference-video seconds or a replacement submission. Reference video seconds are billed in addition to output seconds. Rejected, failed, and expired jobs are not charged under the model contract.

For context, [Evolink's Seedance 2.5 page](https://evolink.ai/seedance-2-5) currently publishes per-output-second tiers around 0.138, 0.296, and 0.739 USD for 480p, 720p, and 1080p. [fal.ai's Seedance 2.5 page](https://fal.ai/models/bytedance/seedance-2.5/text-to-video) describes token-based pricing and a 720p example. Their fields and billing formulas are comparison information, not drop-in request contracts for this runner.

## Errors and operator checklist

- 400 means invalid input; check the shot body and current model page.
- 401 means the key is missing or invalid; check the environment used to start the process.
- 402 means billing or balance needs attention.
- 404 means the request ID is unknown; preserve the manifest before investigating.
- 409 means content is not ready; keep polling status.
- 424 means the job failed; retain the provider error object.
- 429 and 503 are retried only for the same request and never create a second generation.

Before a production run, confirm the plan checksum, reference permissions, output directory, available disk space and ffmpeg version. Keep the manifest and original clips until the assembled file has been inspected. Use only media for which you have permission; the pipeline does not validate third-party licences.

## Development

~~~sh
python -m unittest discover -s tests -v
python -m py_compile montage/workflow.py
~~~

The tests use a local fake HTTP opener and mocked ffmpeg invocation. They verify plan validation, HTTPS-only references, canonical request bodies, authentication and User-Agent headers, unsupported provider statuses, and safe ffconcat path escaping. They never contact the production API and never create a billable generation.
