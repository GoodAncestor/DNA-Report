# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 GoodAncestor
"""Upload handling for the web front door: unwrap archives, cap size, and turn
every failure into something a person can act on.

Why this module exists
----------------------
The front door used to answer "I could not use your file" with HTTP 200 and a
JSON body. The browser dropped that into a one-line status field, so a failed
upload looked identical to nothing happening at all. Uploads must fail *loudly*:
a real status code, a stable machine-readable `error.code`, and a sentence that
says what to do next.

Two upload realities this module handles:

  1. **Consumer exports arrive as archives.** 23andMe, AncestryDNA, MyHeritage
     and FTDNA all hand the user a ZIP. Asking people to unzip first is a bad
     answer, so a ZIP is unwrapped here and the genotype member inside it is what
     gets analysed. (The browser also unwraps before uploading — see landing.py —
     which is what actually keeps a ZIP off the wire. This is the server-side
     backstop for API callers and for anything the browser path misses.)

  2. **Uploads must be bounded.** A pre-read size cap keeps a hostile or
     mistaken upload from filling the front door's disk.

Error bodies are a fixed shape so the page can render them well:

    {"error": {"code", "title", "message", "hint", "accepted"?}, "detail": str}

`detail` is retained because FastAPI's own HTTPException bodies use it and some
callers already read it; `error` is what the UI renders.
"""
from __future__ import annotations

import os
import re
import zipfile

# Cap for the inline front door. Real consumer genotype exports are 15-25 MB
# uncompressed; whole-genome VCFs and arrays are far larger and belong on the
# large-file (R2) path, not here.
#
# The ceiling is set by the EDGE, not by us: Cloudflare refuses a request body
# over its per-plan limit before the app is reached at all, so any value above
# that limit is unenforceable — the app simply never sees those requests. The
# zone is on Business, whose limit is 200 MB, so this sits just under it with
# room for multipart overhead. The previous 256 MB was above the edge cap and
# therefore dead: no request that large could ever have arrived to be refused.
#
# The landing page derives its own routing threshold from this number (see
# landing.py) so the client and the server cannot disagree about what is too big.
# Raising it past the edge limit is not dangerous — an oversized upload now falls
# back to the parts flow rather than dead-ending — but it is still fiction.
MAX_UPLOAD_BYTES = int(os.environ.get("DNAREPORT_MAX_UPLOAD_BYTES", 180 * 1024 * 1024))

# Shown to the user whenever we could not read a file. Kept in one place so the
# error page, the API body and the landing-page copy cannot drift apart.
ACCEPTED_FORMATS = [
    "23andMe raw data (.txt, or the .zip you downloaded)",
    "AncestryDNA / MyHeritage / FamilyTreeDNA / LivingDNA export (.txt or .csv, zipped is fine)",
    "VCF genome (.vcf or .vcf.gz)",
    "Methylation beta-value table (.csv)",
    "modkit bedMethyl methylation calls (.bed, .bedmethyl)",
    "Illumina IDAT array file (.idat) — large-file upload",
    "ONT modBAM (.bam, .modbam) — large-file upload",
]

# Archive members that are never the data: checksums, readmes, macOS resource
# forks. Skipped so a ZIP containing "__MACOSX/._genome.txt" picks the real file.
_ARCHIVE_NOISE = ("__macosx/", ".ds_store", "readme", "license", "checksum", ".md5")


class UploadError(Exception):
    """An upload we could not use, carrying everything the UI needs to explain it.

    Raised instead of HTTPException so the web layer decides the response shape
    (JSON for the API, a rendered page for a browser) in exactly one place.
    """

    def __init__(self, code: str, title: str, message: str, *, hint: str = "",
                 status: int = 400, accepted: bool = False):
        super().__init__(message)
        self.code = code
        self.title = title
        self.message = message
        self.hint = hint
        self.status = status
        self.accepted = accepted

    def body(self) -> dict:
        err = {"code": self.code, "title": self.title, "message": self.message}
        if self.hint:
            err["hint"] = self.hint
        if self.accepted:
            err["accepted"] = ACCEPTED_FORMATS
        # `detail` mirrors FastAPI's own error shape for callers that read it.
        return {"error": err, "detail": self.message}


async def stream_to_disk(upload, dest: str) -> int:
    """Copy an UploadFile to `dest` in chunks, aborting past MAX_UPLOAD_BYTES.

    Chunked rather than `await upload.read()` so an oversized upload is refused
    part-way through instead of being fully materialised in memory first — the
    cap is only a real cap if nothing before it has to hold the whole file.
    Returns the number of bytes written.
    """
    written = 0
    with open(dest, "wb") as fh:
        while chunk := await upload.read(1024 * 1024):
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                fh.close()
                try:
                    os.unlink(dest)
                except OSError:
                    pass
                check_size(written)          # raises the 413
            fh.write(chunk)
    check_size(written)                      # catches the empty-file case
    return written


def check_size(n_bytes: int) -> None:
    """Reject an oversized or empty upload."""
    if n_bytes > MAX_UPLOAD_BYTES:
        mb = MAX_UPLOAD_BYTES // (1024 * 1024)
        raise UploadError(
            "too_large",
            "That file is too large for the instant analysis",
            f"This upload is {n_bytes / (1024 * 1024):.0f} MB; the instant path accepts "
            f"up to {mb} MB.",
            hint="Whole-genome sequencing files and raw arrays go through the "
                 "large-file upload instead, which streams the file in parts.",
            status=413)
    if n_bytes == 0:
        raise UploadError(
            "empty_file",
            "That file is empty",
            "The uploaded file contains no data at all (0 bytes).",
            hint="If you exported this from a testing service, re-download it — a "
                 "0-byte file usually means the download was interrupted.",
            status=400)


def unwrap_archive(path: str, scratch: str) -> tuple[str, str | None]:
    """If `path` is a ZIP, extract its genotype member into `scratch`.

    Returns `(path_to_analyse, note)` where `note` is a human sentence about the
    unwrapping (or None if the file was not an archive). A `.gz` is passed
    through untouched — `detect` and the parsers already read gzip directly.

    The member is chosen as the largest non-noise entry, which is reliably the
    genotype table in every consumer export we have seen. Extraction is done by
    reading the member and writing it ourselves, never `extractall`, so a
    malicious archive cannot write outside `scratch` via traversal entries.
    """
    if not zipfile.is_zipfile(path):
        return path, None

    try:
        with zipfile.ZipFile(path) as zf:
            members = [
                zi for zi in zf.infolist()
                if not zi.is_dir()
                and zi.file_size > 0
                and not any(bit in zi.filename.lower() for bit in _ARCHIVE_NOISE)
            ]
            if not members:
                raise UploadError(
                    "empty_archive",
                    "That ZIP has nothing we can read in it",
                    "The archive opened, but every entry inside it was empty or was a "
                    "readme/checksum rather than genetic data.",
                    hint="Open the ZIP and upload the data file inside it directly — "
                         "for a 23andMe export that is the .txt file.",
                    status=415, accepted=True)

            # Guard against a decompression bomb: refuse before writing anything.
            biggest = max(members, key=lambda zi: zi.file_size)
            check_size(biggest.file_size)

            # Flatten the name so nested paths cannot escape the scratch dir.
            safe = os.path.basename(biggest.filename) or "upload.txt"
            out = os.path.join(scratch, safe)
            with zf.open(biggest) as src, open(out, "wb") as dst:
                while chunk := src.read(1024 * 1024):
                    dst.write(chunk)
    except UploadError:
        raise
    except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
        raise UploadError(
            "unreadable_archive",
            "We could not open that ZIP",
            "The file looks like a ZIP archive but could not be read.",
            hint="If the archive is password-protected or was only partly "
                 "downloaded, unzip it yourself and upload the file inside.",
            status=415) from exc

    return out, f"Unpacked {os.path.basename(biggest.filename)} from the uploaded archive."


def sanitize_note(note: str, scratch: str, display_name: str) -> str:
    """Replace server-side scratch paths in an engine note with the user's own
    filename. Engine notes interpolate the path they were handed, which would
    otherwise expose the front door's temp directory layout in the UI."""
    if not note:
        return note
    out = note
    # Longest match first: the full path to the analysed file becomes the name
    # the user actually chose, then any other reference to the scratch dir is
    # dropped. Doing this in the other order left nothing for the filename
    # substitution to match, so notes came out as "/genome.txt" with a stray
    # leading slash instead of "genome.txt".
    if scratch and display_name:
        out = out.replace(os.path.join(scratch, display_name), display_name)
    if scratch:
        out = out.replace(scratch.rstrip("/") + "/", "").replace(scratch, "")
    # Safety net for a path built from a scratch dir other than this request's.
    # Scoped to OUR prefix rather than to /tmp and /var generally: the broad
    # version also rewrote legitimate paths an engine may cite (a mirror
    # database under /var, say), and a plain "//" collapse corrupted every URL
    # in a note — "https://..." came out as "https:/...".
    out = re.sub(r"\S*/dnr-web-[^/\s]+/(\S+)", r"\1", out)
    return out.strip()
