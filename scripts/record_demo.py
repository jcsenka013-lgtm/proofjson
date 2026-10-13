#!/usr/bin/env python3
"""Rebuild ``docs/demo.gif`` from the generated sample invoice.

The terminal frames are the real ``--model mock`` command output. The review
frames are screenshots of the local review page. No API key is used.

From the repo root, with ffmpeg and Google Chrome installed:

    uv run --with playwright --with pillow python scripts/record_demo.py

The script writes ``out.json`` in the repo root while it runs and deletes
that file, plus ``out.reviewed.json``, when it finishes. An existing
``out.json`` is restored.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
GIF_PATH = ROOT / "docs" / "demo.gif"
WIDTH = 1280
HEIGHT = 720
FPS = 10
PORT = 8765
NEW_NOTE = "Paid in the demo."
EXTRACT_CMD = (
    "uv run proofjson extract examples/invoice/invoice.pdf "
    "--schema examples/invoice/schema.json --model mock -o out.json"
)
JSON_CMD = (
    "uv run python -c 'import json; d=json.load(open(\"out.json\")); "
    'print(json.dumps({"_evidence": {"total": d["_evidence"]["total"]}}, indent=2))\''
)
REVIEW_CMD = "uv run proofjson review out.json --no-browser"
FONT_CANDIDATES = (
    Path("/usr/share/fonts/truetype/jetbrains-mono/JetBrainsMono-Regular.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"),
)
EXPECTED_SUMMARY = (
    "Wrote out.json\n"
    "20 fields, 20 verified, 0 unverified\n"
    "below confidence: notes\n"
    "needs review"
)
HIGHLIGHT = "Notes: Thank you for your demo order."

BG = (26, 24, 20)
FG = (244, 240, 230)
GREEN = (143, 206, 176)
AMBER = (255, 224, 138)
MUTED = (138, 132, 120)
KEY = (240, 198, 116)
STRING = (181, 214, 165)

JSON_TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|-?\d+(?:\.\d+)?|\btrue\b|\bfalse\b|\bnull\b|[{}\[\]:,]')
Segment = tuple[str, tuple[int, int, int]]
Frame = tuple[Image.Image, int]


def find_uv() -> str:
    found = shutil.which("uv")
    if found:
        return found
    candidate = Path.home() / ".local" / "bin" / "uv"
    if candidate.is_file():
        return str(candidate)
    raise SystemExit("uv is not on PATH. Install it from https://docs.astral.sh/uv/.")


def command_env(uv: str) -> dict[str, str]:
    env = os.environ.copy()
    uv_dir = str(Path(uv).parent)
    parts = env.get("PATH", "").split(os.pathsep)
    if uv_dir not in parts:
        env["PATH"] = uv_dir + os.pathsep + env.get("PATH", "")
    env["PYTHONUNBUFFERED"] = "1"
    return env


def run_cmd(cmd: str, env: dict[str, str]) -> str:
    completed = subprocess.run(
        ["bash", "-c", cmd],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise SystemExit(
            f"command failed ({completed.returncode}): {cmd}\n{completed.stdout}\n{completed.stderr}"
        )
    return completed.stdout.rstrip("\n")


def port_is_free(port: int) -> bool:
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


class Terminal:
    def __init__(self) -> None:
        font_path = next((path for path in FONT_CANDIDATES if path.is_file()), None)
        if font_path is None:
            raise SystemExit("Install a monospace font, such as JetBrains Mono or DejaVu Sans Mono.")
        self.font = ImageFont.truetype(str(font_path), 16)
        self.cell = float(self.font.getlength("M"))
        self.line_height = 22
        self.pad_x = 28
        self.pad_y = 22
        self.cols = int((WIDTH - self.pad_x * 2) // self.cell)
        self.rows = int((HEIGHT - self.pad_y * 2) // self.line_height)
        if self.cols < 80 or self.rows < 24:
            raise SystemExit(f"Terminal grid is too small: {self.cols}x{self.rows}")

    def render(self, done: list[tuple[str, str, str]], current: str | None) -> Image.Image:
        logical: list[list[Segment]] = []
        for command, output, kind in done:
            logical.append([("$ ", GREEN), (command, FG)])
            logical.extend(self._output(output, kind))
            logical.append([])
        if current is None:
            logical.append([("$ ", GREEN)])
        else:
            logical.append([("$ ", GREEN), (current, FG)])
        rows = self._wrap(logical)
        visible = rows[-self.rows :]
        image = Image.new("RGB", (WIDTH, HEIGHT), BG)
        draw = ImageDraw.Draw(image)
        for index, row in enumerate(visible):
            x = float(self.pad_x)
            y = self.pad_y + index * self.line_height
            for text, color in row:
                draw.text((x, y), text, font=self.font, fill=color, anchor="lt")
                x += self.cell * len(text)
        cursor_row = visible[-1] if visible else []
        cursor_col = sum(len(text) for text, _color in cursor_row)
        if cursor_col >= self.cols:
            cursor_col = 0
        draw.rectangle(
            (
                self.pad_x + cursor_col * self.cell,
                self.pad_y + (len(visible) - 1) * self.line_height + 3,
                self.pad_x + (cursor_col + 1) * self.cell - 1,
                self.pad_y + len(visible) * self.line_height - 3,
            ),
            fill=AMBER,
        )
        return image

    def _output(self, output: str, kind: str) -> list[list[Segment]]:
        lines = output.splitlines() or [""]
        rendered: list[list[Segment]] = []
        for line in lines:
            if kind == "json":
                rendered.append(self._json_line(line))
            elif kind == "summary" and (line.startswith("below confidence:") or line == "needs review"):
                rendered.append([(line, AMBER)])
            else:
                rendered.append([(line, FG)])
        return rendered

    def _json_line(self, line: str) -> list[Segment]:
        if not line:
            return []
        parts: list[Segment] = []
        pos = 0
        for match in JSON_TOKEN.finditer(line):
            if match.start() > pos:
                parts.append((line[pos : match.start()], FG))
            token = match.group()
            if token.startswith('"'):
                follows = line[match.end() :].lstrip()
                color = KEY if follows.startswith(":") else STRING
            elif token in {"true", "false"}:
                color = GREEN
            elif token in {"{", "}", "[", "]", ":", ","}:
                color = MUTED
            else:
                color = FG
            parts.append((token, color))
            pos = match.end()
        if pos < len(line):
            parts.append((line[pos:], FG))
        return parts or [(line, FG)]

    def _wrap(self, lines: list[list[Segment]]) -> list[list[Segment]]:
        rows: list[list[Segment]] = []
        for line in lines:
            rows.extend(self._wrap_line(line))
        return rows or [[]]

    def _wrap_line(self, segments: list[Segment]) -> list[list[Segment]]:
        if not any(text for text, _color in segments):
            return [[]]
        rows: list[list[Segment]] = [[]]
        column = 0
        buffer = ""
        buffer_color = FG

        def flush() -> None:
            nonlocal buffer
            if buffer:
                rows[-1].append((buffer, buffer_color))
                buffer = ""

        for text, color in segments:
            for char in text:
                if column >= self.cols:
                    flush()
                    rows.append([])
                    column = 0
                if buffer and color != buffer_color:
                    flush()
                buffer_color = color
                buffer += char
                column += 1
        flush()
        return rows


def add_hold(frames: list[Frame], image: Image.Image, seconds: float) -> None:
    frames.append((image, round(seconds * FPS)))


def add_typing(
    frames: list[Frame],
    terminal: Terminal,
    done: list[tuple[str, str, str]],
    command: str,
    seconds: float,
) -> None:
    steps = round(seconds * FPS)
    for index in range(1, steps + 1):
        shown = command[: max(1, round(len(command) * index / steps))]
        frames.append((terminal.render(done, shown), 1))


def terminal_frames(terminal: Terminal, summary: str, evidence: str, review: str) -> list[Frame]:
    frames: list[Frame] = []
    done: list[tuple[str, str, str]] = []
    add_typing(frames, terminal, done, EXTRACT_CMD, 1.8)
    add_hold(frames, terminal.render(done, EXTRACT_CMD), 0.3)
    done.append((EXTRACT_CMD, summary, "summary"))
    add_hold(frames, terminal.render(done, None), 1.9)

    add_typing(frames, terminal, done, JSON_CMD, 1.6)
    add_hold(frames, terminal.render(done, JSON_CMD), 0.3)
    done.append((JSON_CMD, evidence, "json"))
    add_hold(frames, terminal.render(done, None), 2.1)

    add_typing(frames, terminal, done, REVIEW_CMD, 0.8)
    add_hold(frames, terminal.render(done, REVIEW_CMD), 0.3)
    done.append((REVIEW_CMD, review, "review"))
    add_hold(frames, terminal.render(done, None), 0.9)
    return frames


def grab(page: Page) -> Image.Image:
    image = Image.open(io.BytesIO(page.screenshot(type="png"))).convert("RGB")
    if image.size != (WIDTH, HEIGHT):
        image = image.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    return image


def click_notes(page: Page) -> None:
    page.locator('.card[data-path="notes"]').scroll_into_view_if_needed()
    page.locator('.card[data-path="notes"] .path').click()
    page.wait_for_selector("#hit")
    page.locator("#hit").evaluate("element => element.scrollIntoView({block: 'center', inline: 'nearest'})")
    page.wait_for_timeout(80)


def approve_remaining(page: Page) -> list[Image.Image]:
    page.get_by_role("button", name="Open", exact=True).click()
    page.wait_for_selector('[data-action="approve"]')
    remaining = page.locator('[data-action="approve"]').count()
    if remaining < 1:
        raise SystemExit("The review page has no fields left to approve.")
    shots: list[Image.Image] = [grab(page)]
    marks = {remaining // 3, (2 * remaining) // 3}
    for index in range(remaining):
        page.evaluate(
            """() => {
              const button = document.querySelector('[data-action="approve"]');
              if (!button) throw new Error("no approve button");
              button.click();
            }"""
        )
        left = remaining - index
        page.wait_for_function(
            "(count) => document.querySelectorAll('[data-action=approve]').length < count",
            arg=left,
            timeout=8000,
        )
        if index in marks:
            shots.append(grab(page))
    page.wait_for_selector("text=Nothing open")
    shots.append(grab(page))
    return shots


def browser_frames(page: Page) -> list[Frame]:
    page.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
    page.wait_for_function("() => document.getElementById('subtitle').textContent.includes('0 of 20')")
    frames: list[Frame] = [(grab(page), round(0.8 * FPS))]

    page.get_by_role("button", name="Needs a look").click()
    click_notes(page)
    hit = page.locator("#hit").inner_text()
    if hit != HIGHLIGHT:
        raise SystemExit(f"Expected the highlighted quote {HIGHLIGHT!r}, got {hit!r}")
    card = page.locator('.card[data-path="notes"]').inner_text()
    if "75%" not in card or "Thank you for your demo order." not in card:
        raise SystemExit(f"The notes card does not show the low-confidence field:\n{card}")
    frames.append((grab(page), round(3.2 * FPS)))

    field = page.locator('input[data-input="notes"]')
    field.click()
    field.fill("")
    slots = round(1.6 * FPS)
    for index in range(slots):
        typed = NEW_NOTE[: round(len(NEW_NOTE) * index / (slots - 1))]
        field.fill(typed)
        frames.append((grab(page), 1))
    page.locator('.card[data-path="notes"] [data-action="edit"]').click()
    page.wait_for_function("() => document.getElementById('subtitle').textContent.includes('1 of 20')")
    page.get_by_role("button", name="All", exact=True).click()
    click_notes(page)
    edited = page.locator('.card[data-path="notes"]').inner_text()
    edited_value = page.locator('input[data-input="notes"]').input_value()
    if "edited" not in edited or edited_value != NEW_NOTE:
        raise SystemExit(f"The saved edit is not visible ({edited_value!r}):\n{edited}")
    if page.locator("#hit").inner_text() != HIGHLIGHT:
        raise SystemExit("Saving the edit dropped the original notes quote.")
    frames.append((grab(page), round(1.0 * FPS)))

    shots = approve_remaining(page)
    progress = shots[:-1] or shots
    progress_frames = round(1.4 * FPS)
    for index, shot in enumerate(progress):
        share = progress_frames // len(progress)
        extra = 1 if index < progress_frames % len(progress) else 0
        frames.append((shot, max(1, share + extra)))
    frames.append((shots[-1], round(0.6 * FPS)))

    page.locator("#save").click()
    page.wait_for_selector("text=Every field has a decision")
    status = page.locator("#status").inner_text()
    if "out.reviewed.json" not in status:
        raise SystemExit(f"Save status did not name out.reviewed.json: {status}")
    frames.append((grab(page), round(1.4 * FPS)))
    return frames


def record_browser() -> list[Frame]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel="chrome",
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--force-device-scale-factor=1",
                "--font-render-hinting=none",
            ],
        )
        try:
            page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=1)
            page.set_default_timeout(8000)
            try:
                return browser_frames(page)
            except Exception:
                page.screenshot(path="/tmp/proofjson-demo-failure.png")
                raise
        finally:
            browser.close()


def review_banner(stdout_lines: list[str], stderr_lines: list[str]) -> str:
    lines = [line.rstrip() for line in stdout_lines if line.strip()]
    for line in stderr_lines:
        if "Uvicorn running on" in line:
            lines.append(line.rstrip())
            break
    if not any(line.startswith("Review out.json at") for line in lines):
        raise SystemExit(f"Review server did not announce its URL.\n{lines}\n{stderr_lines}")
    return "\n".join(lines)


def start_review(uv: str, env: dict[str, str]) -> tuple[subprocess.Popen[str], list[str], list[str]]:
    if not port_is_free(PORT):
        raise SystemExit(f"Port {PORT} is already in use.")
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    process = subprocess.Popen(
        [uv, "run", "proofjson", "review", "out.json", "--no-browser", "--port", str(PORT)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )

    def drain(stream: Any, bucket: list[str]) -> None:
        if stream is None:
            return
        for line in stream:
            bucket.append(line)

    threading.Thread(target=drain, args=(process.stdout, stdout_lines), daemon=True).start()
    threading.Thread(target=drain, args=(process.stderr, stderr_lines), daemon=True).start()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SystemExit(
                f"review server exited {process.returncode}\n{''.join(stdout_lines)}\n{''.join(stderr_lines)}"
            )
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=0.5) as response:
                if response.status == 200:
                    return process, stdout_lines, stderr_lines
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(0.1)
    raise SystemExit(f"review server did not start\n{''.join(stderr_lines)}")


def stop_process(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=3)


def encode_gif(frames: list[Frame], dest: Path) -> None:
    total = sum(count for _image, count in frames)
    duration = total / FPS
    if not 15 <= duration <= 25:
        raise SystemExit(f"Demo duration {duration:.1f}s is outside 15–25 seconds.")
    work = Path("/tmp/proofjson-demo-frames")
    if work.exists():
        shutil.rmtree(work)
    work.mkdir()
    number = 1
    for image, count in frames:
        for _ in range(count):
            image.save(work / f"frame_{number:04d}.png", compress_level=1)
            number += 1
    palette = work / "palette.png"
    encode = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-framerate",
        str(FPS),
        "-start_number",
        "1",
        "-i",
        str(work / "frame_%04d.png"),
    ]
    subprocess.run([*encode, "-vf", "palettegen=max_colors=96:stats_mode=full", str(palette)], check=True)
    subprocess.run(
        [
            *encode,
            "-i",
            str(palette),
            "-lavfi",
            "paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle",
            "-loop",
            "0",
            str(dest),
        ],
        check=True,
    )
    shutil.rmtree(work)
    size = dest.stat().st_size
    if size >= 5 * 1024 * 1024:
        raise SystemExit(f"{dest} is {size} bytes, above the 5 MB limit.")
    print(f"Wrote {dest} ({duration:.1f}s, {size / 1024 / 1024:.2f} MB)")


def assert_reviewed(path: Path) -> None:
    saved = json.loads(path.read_text(encoding="utf-8"))
    if saved.get("notes") != NEW_NOTE:
        raise SystemExit(f"Reviewed notes were {saved.get('notes')!r}")
    evidence = saved.get("_evidence", {}).get("notes", {})
    if evidence.get("quote") != HIGHLIGHT:
        raise SystemExit("Reviewed file lost the original notes quote.")
    total = saved.get("_evidence", {}).get("total", {})
    if total.get("quote") != "Total due: 128.40" or total.get("page") != 2 or total.get("verified") is not True:
        raise SystemExit(f"Reviewed total evidence changed: {total}")
    if saved.get("_meta", {}).get("review_complete") is not True:
        raise SystemExit("Review did not finish every field.")


def main() -> None:
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg is required to encode the GIF.")
    uv = find_uv()
    env = command_env(uv)
    out_path = ROOT / "out.json"
    reviewed_path = ROOT / "out.reviewed.json"
    backup = out_path.read_bytes() if out_path.exists() else None
    process: subprocess.Popen[str] | None = None
    try:
        summary = run_cmd(EXTRACT_CMD, env)
        if summary != EXPECTED_SUMMARY:
            raise SystemExit(f"Unexpected extract summary:\n{summary}")
        evidence = run_cmd(JSON_CMD, env)
        for needle in ('"_evidence"', '"total"', '"quote": "Total due: 128.40"', '"page": 2', '"verified": true'):
            if needle not in evidence:
                raise SystemExit(f"Evidence output is missing {needle}:\n{evidence}")
        process, stdout_lines, stderr_lines = start_review(uv, env)
        # The server prints its banner before it accepts connections. Give the
        # drain thread a moment so the GIF shows the real lines.
        time.sleep(0.2)
        banner = review_banner(list(stdout_lines), list(stderr_lines))
        terminal = Terminal()
        frames = terminal_frames(terminal, summary, evidence, banner)
        frames.extend(record_browser())
        assert_reviewed(reviewed_path)
        encode_gif(frames, GIF_PATH)
    finally:
        stop_process(process)
        if backup is None:
            out_path.unlink(missing_ok=True)
        else:
            out_path.write_bytes(backup)
        reviewed_path.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"ffmpeg failed: {exc}") from exc
