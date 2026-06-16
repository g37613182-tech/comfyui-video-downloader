"""
Core video download logic for the ComfyUI Video Downloader node.

Strategy (battle-tested):
  1. Try yt-dlp directly (works for TikTok, Instagram, YouTube, generic, etc.)
  2. If the site is Bilibili and yt-dlp fails with HTTP 412 (anti-bot),
     fall back to: prime cookies from homepage -> WBI-signed playurl API ->
     download DASH video+audio streams -> mux with ffmpeg.
  3. If the resulting video is HEVC (bytevc1 / hevc / h265), transcode to H.264
     so it is broadly compatible with downstream consumers.

This module has no ComfyUI dependency so it can be unit-tested standalone.
"""

import os
import re
import json
import time
import hashlib
import shutil
import tempfile
import subprocess
import urllib.parse
import urllib.request
import http.cookiejar
import ssl

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _which(name):
    return shutil.which(name)


def _run(cmd, timeout=600):
    """Run a command, return (returncode, stdout, stderr)."""
    proc = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout.decode("utf-8", "ignore"), proc.stderr.decode("utf-8", "ignore")


def _ffmpeg_bin():
    return _which("ffmpeg") or "ffmpeg"


def _ffprobe_video_codec(path):
    """Return the video codec name (lowercase) or '' if unknown."""
    ff = _which("ffprobe")
    if not ff:
        # Fall back to ffmpeg banner parsing.
        rc, out, err = _run([_ffmpeg_bin(), "-i", path], timeout=60)
        m = re.search(r"Video:\s*([a-z0-9]+)", err, re.I)
        return (m.group(1).lower() if m else "")
    rc, out, err = _run([
        ff, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=codec_name",
        "-of", "default=nw=1:nk=1", path,
    ], timeout=60)
    return out.strip().lower()


# --------------------------------------------------------------------------- #
# Bilibili fallback (cookie prime + WBI-signed playurl API)
# --------------------------------------------------------------------------- #
_MIXIN_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]


def _bili_opener(cookie_path):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    cj = http.cookiejar.MozillaCookieJar(cookie_path)
    if os.path.exists(cookie_path):
        try:
            cj.load(ignore_discard=True, ignore_expires=True)
        except Exception:
            pass
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cj),
        urllib.request.HTTPSHandler(context=ctx),
    )
    return opener, cj


def _bili_get(opener, url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": "https://www.bilibili.com/",
    })
    return opener.open(req, timeout=30).read()


def _bili_mixin_key(orig):
    return "".join(orig[i] for i in _MIXIN_TAB)[:32]


def _bili_enc_wbi(params, img_key, sub_key):
    mixin = _bili_mixin_key(img_key + sub_key)
    params = dict(params)
    params["wts"] = round(time.time())
    params = dict(sorted(params.items()))
    params = {k: "".join(c for c in str(v) if c not in "!'()*") for k, v in params.items()}
    query = urllib.parse.urlencode(params)
    params["w_rid"] = hashlib.md5((query + mixin).encode()).hexdigest()
    return urllib.parse.urlencode(params)


def _extract_bvid(url):
    m = re.search(r"(BV[0-9A-Za-z]{10})", url)
    return m.group(1) if m else None


def _download_bilibili(url, workdir, log):
    """Fallback path for Bilibili. Returns the merged mp4 path or raises."""
    bvid = _extract_bvid(url)
    if not bvid:
        raise RuntimeError("Could not extract BV id from Bilibili URL.")

    cookie_path = os.path.join(workdir, "bili_cookies.txt")
    opener, cj = _bili_opener(cookie_path)

    # 1. Prime cookies (buvid3 / b_nut) from homepage + finger spi.
    log("Bilibili: priming anti-bot cookies ...")
    _bili_get(opener, "https://www.bilibili.com/")
    try:
        _bili_get(opener, "https://api.bilibili.com/x/frontend/finger/spi")
    except Exception:
        pass
    cj.save(ignore_discard=True, ignore_expires=True)

    # 2. Resolve cid via view API.
    view = json.loads(_bili_get(
        opener, f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"))
    if view.get("code") != 0:
        raise RuntimeError(f"Bilibili view API error: {view.get('message')}")
    data = view["data"]
    cid = data["cid"]
    title = data.get("title", bvid)
    log(f"Bilibili: '{title}' (cid={cid})")

    # 3. WBI keys from nav.
    nav = json.loads(_bili_get(opener, "https://api.bilibili.com/x/web-interface/nav"))
    wbi = nav["data"]["wbi_img"]
    img_key = wbi["img_url"].rsplit("/", 1)[1].split(".")[0]
    sub_key = wbi["sub_url"].rsplit("/", 1)[1].split(".")[0]

    # 4. playurl (DASH).
    q = _bili_enc_wbi(
        {"bvid": bvid, "cid": cid, "qn": 80, "fnval": 4048, "fourk": 1},
        img_key, sub_key)
    play = json.loads(_bili_get(
        opener, "https://api.bilibili.com/x/player/wbi/playurl?" + q))
    if play.get("code") != 0:
        raise RuntimeError(f"Bilibili playurl error: {play.get('message')}")
    pdata = play["data"]

    if pdata.get("dash"):
        dash = pdata["dash"]
        video = sorted(dash["video"], key=lambda x: x.get("bandwidth", 0), reverse=True)[0]
        audio = sorted(dash["audio"], key=lambda x: x.get("bandwidth", 0), reverse=True)[0]
        v_path = os.path.join(workdir, "bili_v.m4s")
        a_path = os.path.join(workdir, "bili_a.m4s")
        log("Bilibili: downloading DASH video + audio streams ...")
        _bili_download_stream(opener, video["baseUrl"], v_path)
        _bili_download_stream(opener, audio["baseUrl"], a_path)
        out = os.path.join(workdir, "bili_merged.mp4")
        log("Bilibili: muxing with ffmpeg ...")
        rc, _, err = _run([_ffmpeg_bin(), "-y", "-i", v_path, "-i", a_path,
                           "-c", "copy", out], timeout=600)
        if rc != 0 or not os.path.exists(out):
            raise RuntimeError(f"ffmpeg mux failed: {err[-500:]}")
        return out
    else:
        durl = pdata["durl"][0]["url"]
        out = os.path.join(workdir, "bili_merged.mp4")
        _bili_download_stream(opener, durl, out)
        return out


def _bili_download_stream(opener, url, dest):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": "https://www.bilibili.com/",
    })
    with opener.open(req, timeout=120) as resp, open(dest, "wb") as f:
        shutil.copyfileobj(resp, f, length=1 << 20)
    if os.path.getsize(dest) < 1024:
        raise RuntimeError(f"Stream download too small: {dest}")


# --------------------------------------------------------------------------- #
# yt-dlp primary path
# --------------------------------------------------------------------------- #
def _has_curl_cffi():
    try:
        import curl_cffi  # noqa: F401
        return True
    except Exception:
        return False


def _download_ytdlp(url, workdir, log):
    """Primary path via yt-dlp, with retries and impersonation when available.

    Returns downloaded file path or raises RuntimeError.
    """
    ytdlp = _which("yt-dlp")
    if not ytdlp:
        raise RuntimeError("yt-dlp is not installed (pip install yt-dlp).")
    out_tmpl = os.path.join(workdir, "dl_%(id)s.%(ext)s")

    base = [
        ytdlp, "--no-check-certificates", "--no-playlist", "--no-warnings",
        "-f", "bv*+ba/b", "--merge-output-format", "mp4",
        "--user-agent", UA,
        "--retries", "5", "--fragment-retries", "5",
        "--retry-sleep", "3",
        "--socket-timeout", "30",
        "--add-header", "Accept-Language:en-US,en;q=0.9",
        "-o", out_tmpl,
    ]

    # Build a list of attempts (most capable first).
    attempts = []
    if _has_curl_cffi():
        # Impersonate a real Chrome to survive TikTok/IG anti-bot handshakes.
        attempts.append(base + ["--impersonate", "chrome", url])
        attempts.append(base + ["--impersonate", "safari", url])
    attempts.append(base + [url])  # plain attempt (works without curl_cffi for many sites)

    last_err = ""
    for i, cmd in enumerate(attempts, 1):
        imp = "chrome/safari impersonate" if "--impersonate" in cmd else "plain"
        log(f"yt-dlp: downloading (attempt {i}/{len(attempts)}, {imp}) ...")
        rc, out, err = _run(cmd, timeout=900)
        files = [os.path.join(workdir, f) for f in os.listdir(workdir)
                 if f.startswith("dl_")]
        if rc == 0 and files:
            return max(files, key=os.path.getsize)
        last_err = err[-600:]
        log(f"yt-dlp attempt {i} failed (rc={rc}).")

    hint = ""
    if not _has_curl_cffi():
        hint = ("\nHINT: install 'curl_cffi' to enable browser impersonation, "
                "which fixes most TikTok/Instagram 'connection closed' / 412 errors:\n"
                "  pip install curl_cffi")
    raise RuntimeError(f"yt-dlp failed: {last_err}{hint}")


# --------------------------------------------------------------------------- #
# TikTok native fallback (web detail API -> no-watermark URL)
# --------------------------------------------------------------------------- #
def _extract_tiktok_id(url):
    m = re.search(r"/video/(\d+)", url)
    if m:
        return m.group(1)
    m = re.search(r"(\d{18,19})", url)
    return m.group(1) if m else None


def _download_tiktok(url, workdir, log):
    """Fallback for TikTok: resolve play address via web detail API, then fetch.

    Uses curl_cffi (real browser TLS fingerprint) when available, because TikTok
    edge servers drop plain urllib/yt-dlp connections (RemoteDisconnected).
    """
    vid = _extract_tiktok_id(url)
    if not vid:
        raise RuntimeError("Could not extract TikTok video id from URL.")

    headers = {
        "User-Agent": UA,
        "Referer": "https://www.tiktok.com/",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "application/json, text/plain, */*",
    }

    apis = [
        ("https://www.tiktok.com/api/item/detail/?itemId=%s"
         "&aid=1988&app_language=en&region=US&device_platform=web_pc" % vid),
        ("https://api16-normal-c-useast1a.tiktokv.com/aweme/v1/feed/"
         "?aweme_id=%s&aid=1180&version_code=300904" % vid),
    ]

    # ---- Preferred path: curl_cffi with Chrome impersonation ----
    try:
        from curl_cffi import requests as creq  # type: ignore
    except Exception:
        creq = None

    def _parse_play(data):
        # web detail API shape
        item = (data.get("itemInfo") or {}).get("itemStruct") or {}
        video = item.get("video") or {}
        play = (video.get("playAddr") or video.get("downloadAddr"))
        if play:
            return play
        bi = video.get("bitrateInfo") or []
        if bi:
            ul = (bi[0].get("PlayAddr") or {}).get("UrlList") or []
            if ul:
                return ul[0]
        # mobile aweme API shape
        aw = (data.get("aweme_list") or [{}])[0]
        v = aw.get("video") or {}
        pa = (v.get("play_addr") or {}).get("url_list") or []
        if pa:
            return pa[0]
        dl = (v.get("download_addr") or {}).get("url_list") or []
        if dl:
            return dl[0]
        return None

    last_err = None
    if creq is not None:
        for api in apis:
            for attempt in range(2):
                try:
                    log(f"TikTok: querying detail API via curl_cffi (chrome) ...")
                    r = creq.get(api, headers=headers, impersonate="chrome",
                                 timeout=30, verify=False)
                    if r.status_code != 200 or not r.content:
                        last_err = f"HTTP {r.status_code}"
                        continue
                    play = _parse_play(r.json())
                    if not play:
                        last_err = "no playable URL in API response"
                        continue
                    out = os.path.join(workdir, f"tiktok_{vid}.mp4")
                    log("TikTok: downloading video stream via curl_cffi ...")
                    dl_headers = dict(headers)
                    dl_headers["Referer"] = "https://www.tiktok.com/"
                    with creq.Session(impersonate="chrome") as s:
                        resp = s.get(play, headers=dl_headers, timeout=120,
                                     stream=True, verify=False)
                        with open(out, "wb") as f:
                            for chunk in resp.iter_content(chunk_size=1 << 20):
                                if chunk:
                                    f.write(chunk)
                    if os.path.getsize(out) >= 1024:
                        return out
                    last_err = "stream too small"
                except Exception as e:
                    last_err = str(e)
                    log(f"TikTok curl_cffi attempt failed: {e}")
        log(f"TikTok curl_cffi path exhausted: {last_err}")

    # ---- Last resort: plain urllib (usually blocked, but try anyway) ----
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cj),
        urllib.request.HTTPSHandler(context=ctx),
    )

    def _get(u):
        req = urllib.request.Request(u, headers=headers)
        return opener.open(req, timeout=30).read()

    try:
        log("TikTok: retrying detail API via plain urllib ...")
        data = json.loads(_get(apis[0]))
        play = _parse_play(data)
        if not play:
            raise RuntimeError("TikTok API did not return a playable URL.")
        out = os.path.join(workdir, f"tiktok_{vid}.mp4")
        req = urllib.request.Request(play, headers=headers)
        with opener.open(req, timeout=120) as resp, open(out, "wb") as f:
            shutil.copyfileobj(resp, f, length=1 << 20)
        if os.path.getsize(out) < 1024:
            raise RuntimeError("TikTok stream download too small.")
        return out
    except Exception as e:
        hint = ""
        if creq is None:
            hint = ("\nHINT: TikTok blocks plain connections. Install curl_cffi to "
                    "enable browser TLS fingerprinting:\n  pip install curl_cffi")
        raise RuntimeError(f"TikTok fallback failed: {e}{hint}")


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def download_video(url, dest_path, transcode_h264=True, log=None):
    """
    Download a video from `url` to `dest_path` (a final .mp4 path).

    Returns dest_path on success. Raises RuntimeError on failure.
    `log` is an optional callable(str).
    """
    if log is None:
        log = lambda m: None
    url = (url or "").strip()
    if not url:
        raise RuntimeError("Empty URL.")

    workdir = tempfile.mkdtemp(prefix="cvd_")
    try:
        raw = None
        is_bili = "bilibili.com" in url or _extract_bvid(url) is not None
        is_tiktok = "tiktok.com" in url

        # Try yt-dlp first (it handles most sites well).
        try:
            raw = _download_ytdlp(url, workdir, log)
        except Exception as e:
            log(f"yt-dlp path failed: {e}")
            if is_bili:
                log("Falling back to Bilibili native downloader ...")
                raw = _download_bilibili(url, workdir, log)
            elif is_tiktok:
                log("Falling back to TikTok native downloader ...")
                raw = _download_tiktok(url, workdir, log)
            else:
                raise

        if not raw or not os.path.exists(raw) or os.path.getsize(raw) < 1024:
            raise RuntimeError("Downloaded file is missing or too small (0KB guard).")

        # Transcode HEVC -> H.264 for compatibility.
        final = raw
        if transcode_h264:
            codec = _ffprobe_video_codec(raw)
            log(f"Detected video codec: {codec or 'unknown'}")
            if codec in ("hevc", "h265", "bytevc1", "av1") or codec == "":
                log("Transcoding to H.264 for compatibility ...")
                tc = os.path.join(workdir, "transcoded.mp4")
                rc, _, err = _run([
                    _ffmpeg_bin(), "-y", "-i", raw,
                    "-c:v", "libx264", "-crf", "23", "-preset", "veryfast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                    "-movflags", "+faststart", tc,
                ], timeout=900)
                if rc == 0 and os.path.exists(tc) and os.path.getsize(tc) > 1024:
                    final = tc
                else:
                    log(f"Transcode failed, keeping original: {err[-300:]}")

        # Move to destination.
        os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
        shutil.copyfile(final, dest_path)
        size = os.path.getsize(dest_path)
        if size < 1024:
            raise RuntimeError(f"Final file too small ({size} bytes).")
        log(f"Saved: {dest_path} ({size / 1024 / 1024:.2f} MB)")
        return dest_path
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("usage: downloader.py <url> <dest.mp4>")
        sys.exit(1)
    download_video(sys.argv[1], sys.argv[2], log=print)
