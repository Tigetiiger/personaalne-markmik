"""What a media file is (size, date, place, camera) and its conversion to the one kept format per
kind (docs/MEEDIA-PLAN.md, call 7): pictures from HEIC to JPEG, every video to H.264 + AAC .mp4 of
at most 1080p. Files only; nothing here touches the database.

Videos need ffmpeg and ffprobe on PATH (the Docker image installs them)."""

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

import pillow_heif
from PIL import ExifTags, Image

from ..util import TALLINN

pillow_heif.register_heif_opener()

MAX_SHORT_SIDE = 1080
DURATION_SLACK = 0.5  # seconds a converted video may differ from its original
HDR_TRANSFERS = {"arib-std-b67", "smpte2084"}  # HLG (iPhone) and PQ
KEPT_PIXEL_FORMATS = {"yuv420p", "yuvj420p"}
# HDR to SDR, or an iPhone's HLG video turns grey and washed out in H.264 at 8 bits.
TONEMAP = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
           "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv")


class Unreadable(Exception):
    """The file is not a picture or video we can read or convert. The message is Estonian."""


def meta(path, kind):
    """{width, height, duration, taken_at, gps, camera} plus, for videos, the codec facts
    needs_conversion() uses (keys starting with '_', never written to a companion)."""
    return image_meta(path) if kind == "image" else video_meta(path)


def image_meta(path):
    try:
        with Image.open(path) as img:
            width, height = img.size
            exif = img.getexif()
    except Exception as exc:  # noqa: BLE001 - Pillow fails in many ways on a broken file
        raise Unreadable("Pilti ei saa avada.") from exc
    if exif.get(ExifTags.Base.Orientation) in (5, 6, 7, 8):  # turned a quarter: shown the other way
        width, height = height, width
    sub = exif.get_ifd(ExifTags.IFD.Exif)
    stamp = sub.get(ExifTags.Base.DateTimeOriginal) or exif.get(ExifTags.Base.DateTime)
    offset = sub.get(ExifTags.Base.OffsetTimeOriginal)
    make, model = (str(exif.get(t) or "").strip("\x00 ") for t in (ExifTags.Base.Make, ExifTags.Base.Model))
    return {
        "width": width, "height": height, "duration": None,
        "taken_at": _exif_time(stamp, offset),
        "gps": _exif_gps(exif.get_ifd(ExifTags.IFD.GPSInfo)),
        "camera": _camera(make, model),
    }


def _exif_time(stamp, offset):
    """'2026:09:30 14:02:11' (+ '+03:00') -> '2026-09-30T14:02:11+03:00'. Without an offset the
    picture is taken to be from Tallinn."""
    try:
        when = datetime.strptime(str(stamp).strip("\x00 "), "%Y:%m:%d %H:%M:%S")
    except (TypeError, ValueError):
        return None
    try:
        return datetime.fromisoformat(when.isoformat() + str(offset).strip("\x00 ")).isoformat()
    except ValueError:
        return when.replace(tzinfo=TALLINN).isoformat()


def _exif_gps(gps):
    def degrees(values, ref):
        d, m, s = (float(v) for v in values)
        return round((d + m / 60 + s / 3600) * (-1 if ref in ("S", "W") else 1), 6)
    try:
        return {"lat": degrees(gps[2], gps[1]), "lon": degrees(gps[4], gps[3])}
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None


def _camera(make, model):
    if model.lower().startswith(make.lower()):
        return model or None
    return f"{make} {model}".strip() or None


def ffprobe(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True, check=True, timeout=60).stdout
        return json.loads(out)
    except (subprocess.SubprocessError, ValueError) as exc:
        raise Unreadable("Videot ei saa avada.") from exc


def video_meta(path):
    info = ffprobe(path)
    streams = info.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None or not video.get("width"):
        raise Unreadable("Failis pole videot.")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    width, height = int(video["width"]), int(video["height"])
    rotation = video.get("tags", {}).get("rotate") or next(
        (d.get("rotation") for d in video.get("side_data_list", []) if "rotation" in d), 0)
    if abs(int(float(rotation))) % 180 == 90:
        width, height = height, width
    tags = {k.lower(): v for k, v in (info.get("format", {}).get("tags") or {}).items()}
    try:
        duration = round(float(info["format"]["duration"]), 3)
    except (KeyError, ValueError):
        raise Unreadable("Video pikkust ei saa lugeda.") from None
    return {
        "width": width, "height": height, "duration": duration,
        "taken_at": _video_time(tags.get("com.apple.quicktime.creationdate") or tags.get("creation_time")),
        "gps": _iso6709(tags.get("com.apple.quicktime.location.iso6709") or tags.get("location")),
        "camera": _camera(tags.get("com.apple.quicktime.make", ""), tags.get("com.apple.quicktime.model", "")),
        "_vcodec": video.get("codec_name"),
        "_pix_fmt": video.get("pix_fmt"),
        "_hdr": video.get("color_transfer") in HDR_TRANSFERS,
        "_acodec": audio.get("codec_name") if audio else None,
    }


def _video_time(text):
    """Apple's '2026-09-30T14:02:11+0300' or the container's UTC '2026-09-30T11:02:11.000000Z'
    -> ISO with the offset, in Tallinn time when the source gave UTC."""
    if not text:
        return None
    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=TALLINN)
    elif when.utcoffset().total_seconds() == 0:
        when = when.astimezone(TALLINN)
    return when.replace(microsecond=0).isoformat()


def _iso6709(text):
    m = re.match(r"([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)", text or "")
    return {"lat": round(float(m.group(1)), 6), "lon": round(float(m.group(2)), 6)} if m else None


def target_suffix(name, info):
    """The suffix the file must be converted to, or None when it is kept as it is."""
    ext = Path(name).suffix.lower()
    if ext in (".heic", ".heif"):
        return ".jpg"
    if info.get("duration") is None:
        return None
    keep = (ext == ".mp4" and info["_vcodec"] == "h264" and info["_acodec"] in (None, "aac")
            and info["_pix_fmt"] in KEPT_PIXEL_FORMATS and not info["_hdr"]
            and min(info["width"], info["height"]) <= MAX_SHORT_SIDE)
    return None if keep else ".mp4"


def convert(src, dst, info):
    """Write src converted to dst (suffix from target_suffix). Unreadable when it fails."""
    if Path(dst).suffix == ".jpg":
        _convert_picture(src, dst)
    else:
        _convert_video(src, dst, info)


def _convert_picture(src, dst):
    try:
        with Image.open(src) as img:
            exif = img.getexif()
            # pillow-heif has already turned the pixels upright: the tag must not turn them again.
            exif[ExifTags.Base.Orientation] = 1
            img.convert("RGB").save(dst, "JPEG", quality=92, exif=exif.tobytes())
    except Exception as exc:  # noqa: BLE001
        raise Unreadable("Pilti ei saa teisendada.") from exc


def _convert_video(src, dst, info):
    filters = [TONEMAP] if info["_hdr"] else []
    if min(info["width"], info["height"]) > MAX_SHORT_SIDE:
        # The frames are already upright (ffmpeg turns them), so width and height are as shown.
        filters.append(f"scale=-2:{MAX_SHORT_SIDE}" if info["width"] >= info["height"] else f"scale={MAX_SHORT_SIDE}:-2")
    filters.append("format=yuv420p")
    cmd = ["nice", "-n", "10", "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
           "-i", str(src), "-map", "0:v:0", "-map", "0:a:0?", "-map_metadata", "0",
           "-vf", ",".join(filters), "-c:v", "libx264", "-preset", "medium", "-crf", "22",
           "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart+use_metadata_tags", str(dst)]
    try:
        subprocess.run(cmd, capture_output=True, check=True)
    except subprocess.CalledProcessError as exc:
        raise Unreadable("Videot ei saa teisendada.") from exc
    after = video_meta(dst)
    if after["_vcodec"] != "h264" or abs(after["duration"] - info["duration"]) > DURATION_SLACK:
        raise Unreadable("Teisendatud video ei vasta originaalile.")


def frame(path, at, max_edge):
    """One frame of a video at `at` seconds as JPEG bytes, its longer edge at most max_edge."""
    scale = f"scale='if(gt(iw,ih),min({max_edge},iw),-2)':'if(gt(iw,ih),-2,min({max_edge},ih))'"
    try:
        return subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{at:.3f}", "-i", str(path),
             "-frames:v", "1", "-vf", scale, "-f", "image2pipe", "-c:v", "mjpeg", "-q:v", "4", "-"],
            capture_output=True, check=True, timeout=120).stdout or _no_frame()
    except subprocess.SubprocessError as exc:
        raise Unreadable("Videost ei saa kaadrit võtta.") from exc


def _no_frame():
    raise Unreadable("Videost ei saa kaadrit võtta.")
