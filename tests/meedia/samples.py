"""Real small media files for the Meedia tests (videos with ffmpeg, made once per session)."""

import shutil
import subprocess
from pathlib import Path

import pillow_heif
from PIL import ExifTags, Image

SAMPLES = {}
DIR = []  # set by conftest: one folder for the whole session


def jpeg(path, color=(200, 80, 40), size=(64, 48), exif=None):
    img = Image.new("RGB", size, color)
    e = Image.Exif()
    for tag, value in (exif or {}).items():
        if tag in (ExifTags.Base.DateTimeOriginal, ExifTags.Base.OffsetTimeOriginal):
            e.get_ifd(ExifTags.IFD.Exif)[tag] = value
        elif tag == "gps":
            e.get_ifd(ExifTags.IFD.GPSInfo).update(value)
        else:
            e[tag] = value
    img.save(path, "JPEG", exif=e.tobytes())
    return path


def heic(path, size=(64, 48)):
    pillow_heif.from_pillow(Image.new("RGB", size, (10, 120, 200))).save(path, quality=50)
    return path


def video(path, *, codec="libx264", size="320x240", seconds=2, hdr=False, extra=()):
    """A short test video; made once per kind and copied."""
    key = (Path(path).suffix, codec, size, seconds, hdr, tuple(extra))
    if key not in SAMPLES:
        cache = DIR[0] / f"sample-{len(SAMPLES)}{Path(path).suffix}"
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
               "-f", "lavfi", "-i", f"testsrc=size={size}:rate=10:duration={seconds}",
               "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
               "-c:v", codec, "-pix_fmt", "yuv420p10le" if hdr else "yuv420p"]
        if hdr:
            cmd += ["-color_trc", "arib-std-b67", "-color_primaries", "bt2020", "-colorspace", "bt2020nc"]
        if codec == "libx265":
            # x265 writes the colour facts into the stream only when told through its own params.
            params = "log-level=error" + (":colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc" if hdr else "")
            cmd += ["-tag:v", "hvc1", "-x265-params", params]
        cmd += ["-c:a", "aac", "-shortest", *extra, str(cache)]
        subprocess.run(cmd, check=True)
        SAMPLES[key] = cache
    shutil.copyfile(SAMPLES[key], path)
    return path
