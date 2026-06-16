"""ComfyUI V1 (legacy) compatible node: download a video from a URL.

Used automatically when the running ComfyUI does not provide the V3 API
(`comfy_api.latest`). Outputs a VIDEO object when the runtime supports one,
otherwise falls back to outputting the saved file path as a STRING.
"""

import os
import time
import hashlib

import folder_paths  # provided by ComfyUI runtime

from .downloader import download_video


def _make_output_path(url: str) -> str:
    out_dir = folder_paths.get_output_directory()
    sub = os.path.join(out_dir, "video_downloads")
    os.makedirs(sub, exist_ok=True)
    digest = hashlib.md5(url.encode("utf-8")).hexdigest()[:10]
    ts = int(time.time())
    return os.path.join(sub, f"vdl_{digest}_{ts}.mp4")


def _resolve_video_factory():
    """Find a VideoFromFile implementation available in this ComfyUI version.

    Returns (return_type_str, factory_callable_or_None).
    - If a VIDEO implementation is found  -> ("VIDEO", factory(path)->video_obj)
    - Otherwise fall back to STRING path  -> ("STRING", None)
    """
    # Try every known location of a VideoFromFile implementation across
    # different ComfyUI versions (newest to oldest), without requiring V3.
    candidates = [
        ("comfy_api.input_impl.video_types", "VideoFromFile"),
        ("comfy_api.input_impl", "VideoFromFile"),
        ("comfy_api.input.video_types", "VideoFromFile"),
        ("comfy_api.input", "VideoFromFile"),
    ]
    for mod_name, attr in candidates:
        try:
            mod = __import__(mod_name, fromlist=[attr])
            VideoFromFile = getattr(mod, attr)
            return "VIDEO", (lambda p, _f=VideoFromFile: _f(p))
        except Exception:
            continue
    # V3 InputImpl as a last resort (in case it exists but V3 node failed for other reasons).
    try:
        from comfy_api.latest import InputImpl  # type: ignore
        return "VIDEO", (lambda p: InputImpl.VideoFromFile(p))
    except Exception:
        pass
    # No VIDEO type available in this build -> output the file path instead.
    return "STRING", None


_RETURN_TYPE, _VIDEO_FACTORY = _resolve_video_factory()


class VideoDownloaderByURL:
    """V1-style ComfyUI node: input a URL, output a VIDEO (or file path)."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "url": ("STRING", {
                    "multiline": False,
                    "default": "",
                    "placeholder": "https://www.tiktok.com/@user/video/123...",
                }),
                "transcode_to_h264": ("BOOLEAN", {"default": True}),
            },
        }

    # First output is VIDEO when supported, else STRING(path); second is always the path.
    RETURN_TYPES = (_RETURN_TYPE, "STRING")
    RETURN_NAMES = ("VIDEO" if _RETURN_TYPE == "VIDEO" else "file_path", "file_path")
    FUNCTION = "run"
    CATEGORY = "video/download"
    OUTPUT_NODE = False
    DESCRIPTION = (
        "Download a video from a URL (TikTok, Bilibili, Instagram, YouTube, ...) "
        "and output a VIDEO. Uses yt-dlp with a Bilibili anti-bot fallback, and "
        "auto-transcodes HEVC to H.264."
    )

    @classmethod
    def IS_CHANGED(cls, url, transcode_to_h264):
        # Always re-run (network content may change / temp files cleaned).
        return time.time()

    @classmethod
    def VALIDATE_INPUTS(cls, url, transcode_to_h264):
        if not url or not url.strip():
            return "URL must not be empty."
        if not url.strip().lower().startswith(("http://", "https://")):
            return "URL must start with http:// or https://"
        return True

    def run(self, url, transcode_to_h264):
        logs = []

        def _log(msg):
            logs.append(str(msg))
            print(f"[VideoDownloader] {msg}")

        dest = _make_output_path(url.strip())
        path = download_video(
            url.strip(), dest,
            transcode_h264=transcode_to_h264,
            log=_log,
        )

        if _VIDEO_FACTORY is not None:
            try:
                video = _VIDEO_FACTORY(path)
                return (video, path)
            except Exception as e:
                _log(f"VIDEO wrap failed, returning path string: {e}")
                return (path, path)
        # No VIDEO type -> return path twice (first slot is STRING).
        return (path, path)


NODE_CLASS_MAPPINGS = {
    "VideoDownloaderByURL": VideoDownloaderByURL,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "VideoDownloaderByURL": "Video Downloader (URL)",
}
