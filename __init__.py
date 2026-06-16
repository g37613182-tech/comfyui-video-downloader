"""ComfyUI Video Downloader — package entry point with V3/V1 auto-compatibility.

- If the running ComfyUI provides the V3 API (`comfy_api.latest`), load the V3 node.
- Otherwise fall back to the V1 (legacy) node so older ComfyUI builds also work.
"""

from .version import __version__

# Try the modern V3 API first.
_V3_OK = False
try:
    from comfy_api.latest import ComfyExtension  # noqa: F401  (probe only)
    from .nodes import comfy_entrypoint, VideoDownloaderExtension  # noqa: F401
    _V3_OK = True
except Exception:
    _V3_OK = False

print(f"[comfyui-video-downloader] v{__version__} loaded "
      f"({'V3 API' if _V3_OK else 'V1 legacy fallback'})")

# Always also expose V1 mappings. ComfyUI loads V1 nodes via these globals;
# when V3 is active, defining them as empty/duplicate is harmless, but to avoid
# double-registration we only populate them when V3 is NOT available.
if not _V3_OK:
    from .nodes_v1 import (
        NODE_CLASS_MAPPINGS,
        NODE_DISPLAY_NAME_MAPPINGS,
    )
    __all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
else:
    __all__ = ["comfy_entrypoint", "VideoDownloaderExtension"]
