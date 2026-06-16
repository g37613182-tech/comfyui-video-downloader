# ComfyUI Video Downloader (URL)

**Version: 1.1.0** — see [CHANGELOG.md](CHANGELOG.md).

A ComfyUI custom node that downloads a web video from a URL and outputs a
`VIDEO` object — drop it in, paste a link, get a video.

Works with **TikTok, Bilibili, Instagram, YouTube** and many more sites.

## Features

- **Input a URL, get a `VIDEO` output** — connect it to *Save Video* / *Preview Video* or any node that consumes `VIDEO`.
- Also outputs the **file path** (`STRING`) of the downloaded file.
- Powered by `yt-dlp`, with a **Bilibili anti-bot fallback** (auto cookie priming + WBI-signed playurl API + DASH muxing) for links that return HTTP 412.
- **Auto-transcodes HEVC / H.265 / AV1 → H.264** for broad compatibility (toggleable).
- 0KB / corruption guard: the node fails loudly instead of producing an empty file.

## Compatibility

This node works on **both new and old ComfyUI**:

- **New ComfyUI** (with the V3 API `comfy_api.latest`) → loads the V3 node and outputs a real `VIDEO`.
- **Older ComfyUI** (no `comfy_api.latest`) → automatically falls back to a V1 (legacy)
  node. It still outputs a `VIDEO` when the runtime exposes a `VideoFromFile`
  implementation; if none is available, it outputs the saved file path as a `STRING`.

No configuration is needed — the correct version is selected automatically at load time.

## Installation

1. Copy this folder into your ComfyUI custom nodes directory:

   ```
   ComfyUI/custom_nodes/comfyui-video-downloader/
   ```

2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

   This installs `yt-dlp` and `curl_cffi`. **`curl_cffi` enables browser
   impersonation**, which is required to bypass anti-bot challenges on TikTok /
   Instagram (otherwise you may hit "Remote end closed connection" or HTTP 412).

   You also need **ffmpeg** available on PATH (required for muxing/transcoding).

3. Restart ComfyUI.

## Usage

1. Add the node: right-click → `video/download` → **Video Downloader (URL)**.
2. Paste the video page URL into the `url` field.
3. (Optional) Toggle `transcode_to_h264` off if you want to keep the original codec.
4. Connect the `VIDEO` output to a **Save Video** / **Preview Video** node.

Downloaded files are stored under `ComfyUI/output/video_downloads/`.

## Outputs

| Output | Type | Description |
|---|---|---|
| `VIDEO` | VIDEO | The downloaded video as a ComfyUI VideoFromFile object. |
| `file_path` | STRING | Absolute path of the saved `.mp4`. |

## Notes

- Some platforms gate content behind login or anti-bot challenges; if a download
  fails, the node reports the error in the UI (execution is blocked, not silently passed).
- For private / login-required content you may need to extend `downloader.py`
  to pass cookies to `yt-dlp` (`--cookies`).

## License

MIT
