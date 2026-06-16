"""ComfyUI Video Downloader — custom node package entry point."""

from .nodes import comfy_entrypoint, VideoDownloaderExtension

__all__ = ["comfy_entrypoint", "VideoDownloaderExtension"]
