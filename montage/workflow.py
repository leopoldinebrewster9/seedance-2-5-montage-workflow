"""Resumable Seedance 2.5 multi-shot runner and ffmpeg assembler.

The runner owns API requests and a durable manifest. The assembler only reads
downloaded clips, so an encoding failure never creates another generation job.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

BASE_URL = "https://argolink.io/v1"
MODEL = "seedance-2.5"
USER_AGENT = "seedance-2-5-montage-workflow/1.0"
RATIOS = {"21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}
RESOLUTIONS = {"480p", "720p", "1080p"}
STATUSES = {"pending", "done", "failed", "expired"}


class WorkflowError(RuntimeError):
    pass


def _https(value: str, field: str) -> str:
    if not isinstance(value, str) or urllib.parse.urlparse(value).scheme != "https" or not urllib.parse.urlparse(value).netloc:
        raise ValueError(f"{field} must be an https URL")
    return value


def _read_yaml(path: Path) -> Dict[str, Any]:
    try:
        import yaml
    except ImportError as error:
        raise WorkflowError("install PyYAML to read the shot plan") from error
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise WorkflowError(f"cannot read plan {path}: {error}") from error
    if not isinstance(data, dict):
        raise ValueError("the plan root must be a mapping")
    return data


def load_plan(path: Path) -> Dict[str, Any]:
    data = _read_yaml(path)
    if data.get("model", MODEL) != MODEL:
        raise ValueError(f"this workflow only supports {MODEL}")
    resolution = data.get("resolution", "720p")
    ratio = data.get("aspect_ratio", "16:9")
    if resolution not in RESOLUTIONS:
        raise ValueError("resolution must be 480p, 720p, or 1080p")
    if ratio not in RATIOS:
        raise ValueError("text/reference shots require a fixed aspect ratio")
    images = data.get("reference_images", [])
    videos = data.get("reference_videos", [])
    audios = data.get("reference_audios", [])
    for name, values in (("reference_images", images), ("reference_videos", videos), ("reference_audios", audios)):
        if not isinstance(values, list):
            raise ValueError(f"{name} must be a list")
        if len(values) > {"reference_images": 30, "reference_videos": 10, "reference_audios": 10}[name]:
            raise ValueError(f"{name} exceeds the model limit")
        for i, value in enumerate(values):
            _https(value, f"{name}[{i}]")
    shots = data.get("shots")
    if not isinstance(shots, list) or not shots:
        raise ValueError("shots must be a non-empty list")
    seen = set()
    normalized = []
    for index, shot in enumerate(shots, 1):
        if not isinstance(shot, dict):
            raise ValueError(f"shot {index} must be a mapping")
        shot_id = shot.get("id") or f"shot-{index:02d}"
        if not isinstance(shot_id, str) or not shot_id or shot_id in seen or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in shot_id):
            raise ValueError(f"shot {index} has an invalid or duplicate id")
        seen.add(shot_id)
        prompt = shot.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError(f"shot {shot_id} needs a prompt")
        duration = shot.get("duration", 5)
        if isinstance(duration, bool) or not isinstance(duration, int) or not 4 <= duration <= 30:
            raise ValueError(f"shot {shot_id} duration must be an integer from 4 through 30")
        shot_ratio = shot.get("aspect_ratio", ratio)
        if shot_ratio not in RATIOS:
            raise ValueError(f"shot {shot_id} has an unsupported aspect ratio")
        shot_resolution = shot.get("resolution", resolution)
        if shot_resolution not in RESOLUTIONS:
            raise ValueError(f"shot {shot_id} has an unsupported resolution")
        normalized.append({
            "id": shot_id, "prompt": prompt, "duration": duration,
            "aspect_ratio": shot_ratio, "resolution": shot_resolution,
            "generate_audio": bool(shot.get("generate_audio", True)),
        })
    result = dict(data)
    result["model"] = MODEL
    result["resolution"] = resolution
    result["aspect_ratio"] = ratio
    result["reference_images"] = images
    result["reference_videos"] = videos
    result["reference_audios"] = audios
    result["shots"] = normalized
    return result


def request_body(plan: Dict[str, Any], shot: Dict[str, Any]) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "model": MODEL,
        "prompt": shot["prompt"],
        "duration": shot["duration"],
        "resolution": shot["resolution"],
        "aspect_ratio": shot["aspect_ratio"],
        "generate_audio": shot["generate_audio"],
    }
    for name in ("reference_images", "reference_videos", "reference_audios"):
        values = plan.get(name) or []
        if values:
            body[name] = [{"url": value} for value in values]
    return body


class VideoAPI:
    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        opener: Optional[Callable[..., Any]] = None,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 90.0,
        retries: int = 4,
    ) -> None:
        self.api_key = api_key or os.environ.get("VIDEO_API_KEY")
        self.base_url = (base_url or os.environ.get("VIDEO_API_BASE_URL") or BASE_URL).rstrip("/")
        self._open = opener or urllib.request.urlopen
        self._sleep = sleep
        self.timeout = timeout
        self.retries = retries

    def _call(self, method: str, path: str, body: Optional[Dict[str, Any]] = None, *, binary: bool = False) -> Any:
        if not self.api_key:
            raise WorkflowError("VIDEO_API_KEY is required")
        data = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode() if body is not None else None
        headers = {"Authorization": f"Bearer {self.api_key}", "User-Agent": USER_AGENT}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base_url + path, data=data, method=method, headers=headers)
        for attempt in range(self.retries + 1):
            try:
                response = self._open(request, timeout=self.timeout)
                raw = response.read()
                response.close()
                if binary:
                    return raw
                result = json.loads(raw.decode("utf-8"))
                if not isinstance(result, dict):
                    raise WorkflowError(f"{method} {path} returned a non-object")
                return result
            except urllib.error.HTTPError as error:
                raw = error.read(4096).decode("utf-8", errors="replace")
                if error.code in (429, 503) and attempt < self.retries:
                    retry_after = error.headers.get("Retry-After") if error.headers else None
                    try:
                        delay = float(retry_after) if retry_after else min(60.0, 2.0 ** attempt)
                    except ValueError:
                        delay = min(60.0, 2.0 ** attempt)
                    self._sleep(delay)
                    continue
                raise WorkflowError(f"{method} {path} returned HTTP {error.code}: {raw[:300]}") from None
        raise AssertionError("unreachable")

    def submit(self, body: Dict[str, Any]) -> str:
        result = self._call("POST", "/videos/generations", body)
        request_id = result.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            raise WorkflowError("generation response did not contain request_id")
        return request_id

    def status(self, request_id: str) -> Dict[str, Any]:
        result = self._call("GET", "/videos/" + request_id)
        state = result.get("status")
        if state not in STATUSES:
            raise WorkflowError(f"unknown provider status {state!r} for {request_id}")
        return result

    def download(self, request_id: str, destination: Path) -> None:
        if destination.exists():
            return
        raw = self._call("GET", f"/videos/{request_id}/content", binary=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".partial-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError:
            pass
        finally:
            temporary.unlink(missing_ok=True)


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=".manifest-", delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def run_plan(plan: Dict[str, Any], workdir: Path, api: VideoAPI, *, poll_interval: float = 10.0, timeout: float = 1800.0) -> Dict[str, Any]:
    if poll_interval <= 0 or timeout <= 0:
        raise ValueError("poll_interval and timeout must be positive")
    workdir.mkdir(parents=True, exist_ok=True)
    manifest_path = workdir / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = {"model": MODEL, "shots": {}}
    by_id = {shot["id"]: shot for shot in plan["shots"]}
    for shot_id in list(manifest.get("shots", {})):
        if shot_id not in by_id:
            raise WorkflowError(f"manifest contains shot not present in plan: {shot_id}")
    for shot in plan["shots"]:
        record = manifest["shots"].setdefault(shot["id"], {"body": request_body(plan, shot)})
        record["body"] = request_body(plan, shot)
        if record.get("status") == "done" and record.get("clip"):
            continue
        if not record.get("request_id"):
            record["request_id"] = api.submit(record["body"])
            record["status"] = "pending"
            atomic_json(manifest_path, manifest)
        started = time.monotonic()
        while True:
            state = api.status(record["request_id"])
            record["status"] = state["status"]
            record["provider"] = state
            atomic_json(manifest_path, manifest)
            if state["status"] == "done":
                break
            if state["status"] in {"failed", "expired"}:
                raise WorkflowError(f"shot {shot['id']} ended {state['status']}: {state.get('error', '')}")
            if time.monotonic() - started + poll_interval > timeout:
                raise TimeoutError(f"shot {shot['id']} is still pending; resume with the manifest")
            api._sleep(poll_interval)
        clip = workdir / "clips" / f"{shot['id']}.mp4"
        api.download(record["request_id"], clip)
        record["clip"] = str(clip)
        atomic_json(manifest_path, manifest)
    return manifest


def _ffconcat_path(path: Path) -> str:
    # ffconcat accepts single-quoted paths; escape a literal quote for its parser.
    return "file '" + str(path.resolve()).replace("'", "'\\\\''") + "'\n"


def probe_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        check=True, capture_output=True, text=True,
    )
    try:
        value = float(result.stdout.strip())
    except ValueError as error:
        raise WorkflowError(f"ffprobe did not return a duration for {path}") from error
    if value <= 0:
        raise WorkflowError(f"clip {path} has a non-positive duration")
    return value


def assemble(clips: Iterable[Path], output: Path, *, music: Optional[Path] = None, transition: str = "none", transition_duration: float = 0.5) -> None:
    paths = [Path(path) for path in clips]
    if not paths or any(not path.is_file() for path in paths):
        raise WorkflowError("every planned clip must exist before assembly")
    if output.exists():
        raise FileExistsError(str(output))
    if transition not in {"none", "fade"}:
        raise ValueError("transition must be none or fade")
    if transition_duration <= 0:
        raise ValueError("transition_duration must be positive")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_manifest: Optional[Path] = None
    try:
        if transition == "none":
            with tempfile.NamedTemporaryFile("w", suffix=".ffconcat", delete=False, encoding="utf-8") as handle:
                temporary_manifest = Path(handle.name)
                handle.write("ffconcat version 1.0\n")
                for path in paths:
                    handle.write(_ffconcat_path(path))
            command = ["ffmpeg", "-nostdin", "-y", "-f", "concat", "-safe", "0", "-i", str(temporary_manifest)]
            if music:
                command += ["-stream_loop", "-1", "-i", str(music), "-map", "0:v:0", "-map", "1:a:0", "-shortest"]
            command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(output)]
        else:
            durations = [probe_duration(path) for path in paths]
            inputs: List[str] = []
            filters: List[str] = []
            for index, path in enumerate(paths):
                inputs += ["-i", str(path)]
                filters.append(f"[{index}:v]format=yuv420p,setpts=PTS-STARTPTS[v{index}]")
            current = "v0"
            offset = max(0.1, durations[0] - transition_duration)
            for index in range(1, len(paths)):
                next_name = f"x{index}"
                filters.append(f"[{current}][v{index}]xfade=transition=fade:duration={transition_duration}:offset={offset:.3f}[{next_name}]")
                current = next_name
                offset += durations[index] - transition_duration
            command = ["ffmpeg", "-nostdin", "-y"] + inputs
            if music:
                command += ["-stream_loop", "-1", "-i", str(music)]
            command += ["-filter_complex", ";".join(filters), "-map", f"[{current}]"]
            if music:
                command += ["-map", f"{len(paths)}:a:0", "-shortest"]
            command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(output)]
        subprocess.run(command, check=True)
    finally:
        if temporary_manifest:
            temporary_manifest.unlink(missing_ok=True)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run and assemble a Seedance 2.5 multi-shot plan")
    parser.add_argument("plan", type=Path)
    parser.add_argument("--workdir", type=Path, default=Path(".montage"))
    parser.add_argument("--output", type=Path, default=Path("montage.mp4"))
    parser.add_argument("--music", type=Path)
    parser.add_argument("--transition", choices=("none", "fade"), default="none")
    parser.add_argument("--transition-duration", type=float, default=0.5)
    parser.add_argument("--poll-interval", type=float, default=10.0)
    parser.add_argument("--timeout", type=float, default=1800.0)
    parser.add_argument("--dry-run", action="store_true", help="validate and print request bodies without calling the API")
    args = parser.parse_args(argv)
    plan = load_plan(args.plan)
    if args.dry_run:
        print(json.dumps([request_body(plan, shot) for shot in plan["shots"]], indent=2))
        return 0
    manifest = run_plan(plan, args.workdir, VideoAPI(), poll_interval=args.poll_interval, timeout=args.timeout)
    clips = [Path(manifest["shots"][shot["id"]]["clip"]) for shot in plan["shots"]]
    assemble(clips, args.output, music=args.music, transition=args.transition, transition_duration=args.transition_duration)
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
