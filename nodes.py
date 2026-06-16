"""ComfyUI V3 custom node: download a video from a URL and output a VIDEO object."""

import os
import time
import hashlib

import folder_paths  # provided by ComfyUI runtime

from comfy_api.latest import ComfyExtension, io, InputImpl
from typing_extensions import override

from .downloader import download_video


def _make_output_path(url: str) -> str:
    """Deterministic-ish output path inside ComfyUI's output dir."""
    out_dir = folder_paths.get_output_directory()
    sub = os.path.join(out_dir, "video_downloads")
    os.makedirs(sub, exist_ok=True)
    digest = hashlib.md5(url.encode("utf-8")).hexdigest()[:10]
    ts = int(time.time())
    return os.path.join(sub, f"vdl_{digest}_{ts}.mp4")


class VideoDownloaderNode(io.ComfyNode):
    """Download a web video (TikTok / Bilibili / Instagram / YouTube / ...) by URL."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="VideoDownloaderByURL",
            display_name="Video Downloader (URL)",
            category="video/download",
            description=(
                "Download a video from a URL (TikTok, Bilibili, Instagram, "
                "YouTube, and many more) and output a VIDEO. Uses yt-dlp with a "
                "Bilibili anti-bot fallback, and auto-transcodes HEVC to H.264."
            ),
            inputs=[
                io.String.Input(
                    "url",
                    multiline=False,
                    placeholder="https://www.tiktok.com/@user/video/123...",
                    tooltip="The web page URL of the video to download.",
                ),
                io.Boolean.Input(
                    "transcode_to_h264",
                    default=True,
                    label_on="H.264",
                    label_off="Keep original",
                    tooltip="Transcode HEVC/H.265/AV1 to H.264 for compatibility.",
                ),
            ],
            outputs=[
                io.Video.Output(display_name="VIDEO"),
                io.String.Output(display_name="file_path"),
            ],
            # Downloading is a side-effecting fetch; do not cache aggressively.
            not_idempotent=True,
        )

    @classmethod
    def fingerprint_inputs(cls, url, transcode_to_h264):
        # Re-run whenever invoked (network content may change / files cleaned).
        return time.time()

    @classmethod
    def validate_inputs(cls, url, transcode_to_h264):
        if not url or not url.strip():
            return "URL must not be empty."
        if not url.strip().lower().startswith(("http://", "https://")):
            return "URL must start with http:// or https://"
        return True

    @classmethod
    def execute(cls, url, transcode_to_h264):
        logs = []

        def _log(msg):
            logs.append(str(msg))
            print(f"[VideoDownloader] {msg}")

        dest = _make_output_path(url.strip())
        try:
            path = download_video(
                url.strip(), dest,
                transcode_h264=transcode_to_h264,
                log=_log,
            )
        except Exception as e:
            # Surface a clean, blocking error in the UI.
            return io.NodeOutput(
                block_execution=f"Video download failed: {e}\n" + "\n".join(logs[-6:])
            )

        video = InputImpl.VideoFromFile(path)
        return io.NodeOutput(video, path)


class VideoDownloaderExtension(ComfyExtension):
    @override
    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        return [VideoDownloaderNode]


async def comfy_entrypoint() -> VideoDownloaderExtension:
    return VideoDownloaderExtension()
