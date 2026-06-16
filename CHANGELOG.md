# Changelog

## v1.1.0
- **Version is now shown in the node UI** (display name + description) and printed
  to the ComfyUI console on load, so you can confirm which version is installed.
- Added `curl_cffi` dependency for browser impersonation (fixes TikTok / Instagram
  "Remote end closed connection" / HTTP 412 anti-bot errors).
- yt-dlp downloads now retry with Chrome/Safari impersonation (when `curl_cffi` is
  present), then a plain attempt; added `--retries` / `--socket-timeout`.
- Added a **TikTok native fallback** (web detail API → no-watermark URL) used when
  yt-dlp fails.
- **V1/V3 auto-compatibility**: older ComfyUI builds without `comfy_api.latest`
  now load a legacy V1 node automatically.

## v1.0.0
- Initial release: input a URL, output a `VIDEO` object + file path.
- yt-dlp primary downloader with a Bilibili anti-bot fallback (cookie priming +
  WBI-signed playurl API + DASH muxing).
- Auto-transcode HEVC/H.265/AV1 → H.264.
- 0KB / corruption guard.
