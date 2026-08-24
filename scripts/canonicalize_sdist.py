#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Create a privacy-safe, reproducible ``.tar.gz`` source distribution.

Raw sdist archives may inherit the build account's uid, gid, user/group names,
and file mtimes. This tool validates the input, strips those host fingerprints,
and verifies the resulting archive before making it visible at the output path.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import os
import stat
import struct
import sys
import tarfile
import tempfile
import unicodedata
import zlib
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, Tuple


MAX_MEMBERS = 10_000
MAX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_TAR_STREAM_BYTES = MAX_TOTAL_BYTES + MAX_MEMBERS * 2048 + 4 * 1024 * 1024
MAX_GZIP_BYTES = MAX_TAR_STREAM_BYTES + 16 * 1024 * 1024
GZIP_INPUT_CHUNK = 64 * 1024
GZIP_OUTPUT_CHUNK = 1024 * 1024
OUTPUT_MODE = 0o644
WINDOWS_DEVICE_NAMES = {
    "CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
WINDOWS_FORBIDDEN_CHARACTERS = frozenset('<>:"|?*')
WINDOWS_REPARSE_POINT = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


class ArchiveError(ValueError):
    """Raised when an archive cannot be canonicalized safely."""


class SafeArgumentParser(argparse.ArgumentParser):
    """Keep rejected paths and argument values out of CI logs."""

    def error(self, message: str) -> None:
        del message
        self.print_usage(sys.stderr)
        self.exit(2, "canonical sdist: FAIL (invalid arguments)\n")


def _safe_name(name: str) -> bool:
    try:
        name.encode("utf-8", errors="strict")
    except UnicodeError:
        return False
    if (
        not name
        or "\\" in name
        or "\x00" in name
        or any(ord(character) < 32 or ord(character) == 127 for character in name)
    ):
        return False
    path = PurePosixPath(name)
    if path.is_absolute() or path.as_posix() != name:
        return False
    for index, part in enumerate(path.parts):
        if part in ("", ".", "..") or part.endswith((" ", ".")):
            return False
        if any(character in WINDOWS_FORBIDDEN_CHARACTERS for character in part):
            return False
        if index == 0 and part.startswith("~"):
            return False
        portable_part = unicodedata.normalize("NFKC", part)
        stem = portable_part.split(".", 1)[0].rstrip(" .").upper()
        if stem in WINDOWS_DEVICE_NAMES:
            return False
    return True


def _absolute_without_links(path: Path) -> Path:
    absolute = Path(os.path.abspath(path))
    for candidate in reversed((absolute, *absolute.parents)):
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ArchiveError("filesystem boundary could not be inspected") from exc
        if _is_link_like(metadata):
            raise ArchiveError("filesystem boundary contains a symbolic link")
    return absolute


def _is_link_like(metadata: os.stat_result) -> bool:
    if stat.S_ISLNK(metadata.st_mode):
        return True
    attributes = int(getattr(metadata, "st_file_attributes", 0) or 0)
    return bool(attributes & WINDOWS_REPARSE_POINT)


def _metadata_identity(metadata: os.stat_result) -> Tuple[int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        stat.S_IFMT(metadata.st_mode),
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _object_identity(metadata: os.stat_result) -> Tuple[int, int, int]:
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        stat.S_IFMT(metadata.st_mode),
    )


def _readonly_flags() -> int:
    flags = os.O_RDONLY
    flags |= int(getattr(os, "O_BINARY", 0))
    flags |= int(getattr(os, "O_CLOEXEC", 0))
    flags |= int(getattr(os, "O_NOFOLLOW", 0))
    return flags


def _open_readonly(path: Path) -> int:
    try:
        return os.open(path, _readonly_flags())
    except OSError as exc:
        raise ArchiveError("input archive could not be opened safely") from exc


def _ensure_path_matches_descriptor(
    path: Path,
    descriptor: int,
    expected_path_identity: Tuple[int, int, int, int, int, int],
    message: str,
) -> None:
    """Recheck a path and its descriptor without comparing unlike timestamps.

    Windows path and handle stat calls expose different st_ctime meanings. The
    initial object-ID check binds the path to the primary descriptor; this
    second handle check preserves that binding through each path recheck.
    """

    _absolute_without_links(path)
    try:
        path_metadata = path.lstat()
    except OSError as exc:
        raise ArchiveError(message) from exc
    if (
        _is_link_like(path_metadata)
        or not stat.S_ISREG(path_metadata.st_mode)
        or _metadata_identity(path_metadata) != expected_path_identity
    ):
        raise ArchiveError(message)
    try:
        verifier = _open_readonly(path)
    except ArchiveError as exc:
        raise ArchiveError(message) from exc
    try:
        source_metadata = os.fstat(descriptor)
        verifier_metadata = os.fstat(verifier)
        if (
            _is_link_like(source_metadata)
            or _is_link_like(verifier_metadata)
            or not stat.S_ISREG(source_metadata.st_mode)
            or not stat.S_ISREG(verifier_metadata.st_mode)
        ):
            raise ArchiveError(message)
        if os.name == "nt" and (
            not source_metadata.st_dev
            or not source_metadata.st_ino
            or not verifier_metadata.st_dev
            or not verifier_metadata.st_ino
        ):
            raise ArchiveError(message)
        _absolute_without_links(path)
        try:
            rechecked_metadata = path.lstat()
        except OSError as exc:
            raise ArchiveError(message) from exc
        if (
            _is_link_like(rechecked_metadata)
            or not stat.S_ISREG(rechecked_metadata.st_mode)
            or _metadata_identity(rechecked_metadata) != expected_path_identity
        ):
            raise ArchiveError(message)
        try:
            matches = os.path.sameopenfile(descriptor, verifier)
        except (OSError, ValueError) as exc:
            raise ArchiveError(message) from exc
        if not matches:
            raise ArchiveError(message)
    finally:
        os.close(verifier)


def _validate_single_gzip(stream) -> None:
    try:
        stream.seek(0)
    except OSError as exc:
        raise ArchiveError("input gzip stream could not be positioned") from exc
    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    total = 0
    while True:
        chunk = stream.read(GZIP_INPUT_CHUNK)
        if not chunk:
            break
        if decompressor.eof:
            raise ArchiveError("input gzip stream contains trailing data")
        pending = chunk
        while True:
            try:
                output = decompressor.decompress(pending, GZIP_OUTPUT_CHUNK)
            except zlib.error as exc:
                raise ArchiveError("input gzip integrity check failed") from exc
            total += len(output)
            if total > MAX_TAR_STREAM_BYTES:
                raise ArchiveError("input gzip expands beyond the accepted size limit")
            if decompressor.unused_data:
                raise ArchiveError("input gzip stream contains trailing data")
            pending = decompressor.unconsumed_tail
            if pending:
                continue
            if len(output) == GZIP_OUTPUT_CHUNK and not decompressor.eof:
                pending = b""
                continue
            break
    if not decompressor.eof:
        raise ArchiveError("input gzip stream is truncated")
    try:
        remaining = decompressor.flush()
    except zlib.error as exc:
        raise ArchiveError("input gzip integrity check failed") from exc
    total += len(remaining)
    if total > MAX_TAR_STREAM_BYTES:
        raise ArchiveError("input gzip expands beyond the accepted size limit")
    stream.seek(0)


def _read_members(source: Path) -> Tuple[list[Tuple[tarfile.TarInfo, bytes]], Dict[str, str]]:
    rows: list[Tuple[tarfile.TarInfo, bytes]] = []
    digests: Dict[str, str] = {}
    names = set()
    portable_names = set()
    portable_types: Dict[str, bool] = {}
    roots = set()
    total = 0
    try:
        before_metadata = source.lstat()
    except OSError as exc:
        raise ArchiveError("input archive metadata could not be inspected") from exc
    if _is_link_like(before_metadata) or not stat.S_ISREG(before_metadata.st_mode):
        raise ArchiveError("input must be one regular file")
    if before_metadata.st_size > MAX_GZIP_BYTES:
        raise ArchiveError("input gzip file is larger than the accepted limit")
    if os.name == "nt" and (not before_metadata.st_dev or not before_metadata.st_ino):
        raise ArchiveError("input archive has no stable file identity")
    before_identity = _metadata_identity(before_metadata)
    descriptor = _open_readonly(source)
    try:
        stream = os.fdopen(descriptor, "rb")
    except OSError:
        os.close(descriptor)
        raise
    with stream:
        opened_metadata = os.fstat(stream.fileno())
        opened_identity = _metadata_identity(opened_metadata)
        if (
            _is_link_like(opened_metadata)
            or not stat.S_ISREG(opened_metadata.st_mode)
            or opened_metadata.st_size > MAX_GZIP_BYTES
            or _object_identity(opened_metadata) != _object_identity(before_metadata)
            or (
                os.name == "nt"
                and (not opened_metadata.st_dev or not opened_metadata.st_ino)
            )
        ):
            raise ArchiveError("input archive identity changed before it was opened")
        _ensure_path_matches_descriptor(
            source,
            stream.fileno(),
            before_identity,
            "input archive identity changed before it was opened",
        )
        _validate_single_gzip(stream)
        try:
            archive = tarfile.open(fileobj=stream, mode="r:gz")
        except (OSError, tarfile.TarError, EOFError) as exc:
            raise ArchiveError("input is not a readable gzip-compressed tar archive") from exc
        with archive:
            member_count = 0
            for member in archive:
                member_count += 1
                if member_count > MAX_MEMBERS:
                    raise ArchiveError("archive member count is outside the accepted range")
                portable_name = unicodedata.normalize("NFKC", member.name).casefold()
                if (
                    not _safe_name(member.name)
                    or member.name in names
                    or portable_name in portable_names
                ):
                    raise ArchiveError("archive contains an unsafe or duplicate member path")
                names.add(member.name)
                portable_names.add(portable_name)
                portable_types[portable_name] = member.isdir()
                roots.add(PurePosixPath(portable_name).parts[0])
                if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                    raise ArchiveError("archive contains a link or special-file member")
                if member.isdir():
                    if member.size != 0:
                        raise ArchiveError("archive directory member has nonzero content size")
                    rows.append((member, b""))
                    continue
                if not member.isfile() or member.size < 0 or member.size > MAX_MEMBER_BYTES:
                    raise ArchiveError("archive contains an unsupported or oversized member")
                total += member.size
                if total > MAX_TOTAL_BYTES:
                    raise ArchiveError("archive expands beyond the accepted size limit")
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise ArchiveError("archive member could not be read")
                data = extracted.read(MAX_MEMBER_BYTES + 1)
                if len(data) != member.size:
                    raise ArchiveError("archive member size does not match its header")
                rows.append((member, data))
                digests[member.name] = hashlib.sha256(data).hexdigest()
            if member_count == 0:
                raise ArchiveError("archive member count is outside the accepted range")
        if _metadata_identity(os.fstat(stream.fileno())) != opened_identity:
            raise ArchiveError("input archive changed while it was being read")
        _ensure_path_matches_descriptor(
            source,
            stream.fileno(),
            before_identity,
            "input archive identity changed while it was being read",
        )
    if len(roots) != 1:
        raise ArchiveError("source distribution must contain exactly one top-level directory")
    root = next(iter(roots))
    if portable_types.get(root) is not True:
        raise ArchiveError("source distribution top level must be a directory")
    for portable_name in portable_types:
        parts = PurePosixPath(portable_name).parts
        for length in range(1, len(parts)):
            ancestor = PurePosixPath(*parts[:length]).as_posix()
            if portable_types.get(ancestor) is not True:
                raise ArchiveError("archive member ancestry is incomplete or not a directory")
    rows.sort(key=lambda row: row[0].name.encode("utf-8"))
    return rows, digests


def _normalized_info(source: tarfile.TarInfo, epoch: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(source.name)
    info.type = tarfile.DIRTYPE if source.isdir() else tarfile.REGTYPE
    info.size = 0 if source.isdir() else source.size
    info.mode = 0o755 if source.isdir() or source.mode & 0o111 else 0o644
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = epoch
    info.pax_headers = {}
    return info


def _write_archive(
    raw,
    rows: Iterable[Tuple[tarfile.TarInfo, bytes]],
    epoch: int,
) -> None:
    with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=epoch) as compressed:
        with tarfile.open(
            fileobj=compressed,
            mode="w",
            format=tarfile.PAX_FORMAT,
            encoding="utf-8",
            errors="strict",
        ) as archive:
            for original, data in rows:
                info = _normalized_info(original, epoch)
                archive.addfile(info, None if info.isdir() else io.BytesIO(data))


def _verify_gzip_header(path: Path, epoch: int) -> None:
    try:
        with path.open("rb") as stream:
            header = stream.read(10)
    except OSError as exc:
        raise ArchiveError("canonical gzip header could not be read") from exc
    if len(header) != 10 or header[:3] != b"\x1f\x8b\x08":
        raise ArchiveError("canonical output has an invalid gzip header")
    flags = header[3]
    if flags != 0:
        raise ArchiveError("canonical gzip header contains optional host metadata")
    if struct.unpack("<I", header[4:8])[0] != epoch:
        raise ArchiveError("canonical gzip timestamp does not match the source timestamp")
    if header[8] != 2 or header[9] != 255:
        raise ArchiveError("canonical gzip compression metadata is not fixed")


def verify_archive(
    path: Path,
    epoch: int,
    expected: Dict[str, str] | None = None,
) -> Dict[str, str]:
    _verify_gzip_header(path, epoch)
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            if archive.pax_headers:
                raise ArchiveError("archive contains global extended metadata")
    except (OSError, tarfile.TarError) as exc:
        raise ArchiveError("canonical tar metadata could not be verified") from exc
    rows, observed = _read_members(path)
    for member, _ in rows:
        expected_mode = 0o755 if member.isdir() or member.mode & 0o111 else 0o644
        if member.uid != 0 or member.gid != 0 or member.uname or member.gname:
            raise ArchiveError("archive contains host ownership metadata")
        if member.mtime != epoch or member.pax_headers:
            raise ArchiveError("archive contains non-canonical time or extended metadata")
        if stat.S_IMODE(member.mode) != expected_mode:
            raise ArchiveError("archive contains a non-canonical member mode")
    if expected is not None and observed != expected:
        raise ArchiveError("canonical archive contents differ from the input")
    return observed


def canonicalize(source: Path, destination: Path, epoch: int) -> None:
    if epoch < 0 or epoch > 0xFFFFFFFF:
        raise ArchiveError("source timestamp must fit the gzip timestamp field")
    source = _absolute_without_links(source)
    if source.is_symlink() or not source.is_file():
        raise ArchiveError("input must be one regular file")
    destination = _absolute_without_links(destination)
    if destination == source or destination.exists():
        raise ArchiveError("output must be a new path distinct from the input")
    destination.parent.mkdir(parents=True, exist_ok=True)
    _absolute_without_links(destination)

    rows, expected = _read_members(source)
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
        )
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as raw:
            _write_archive(raw, rows, epoch)
        verify_archive(temporary, epoch, expected)
        os.chmod(temporary, OUTPUT_MODE)
        _absolute_without_links(destination)
        os.link(temporary, destination)
        temporary.unlink()
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def canonicalize_dist(source_directory: Path, output_directory: Path, epoch: int) -> Path:
    source_directory = _absolute_without_links(source_directory)
    if source_directory.is_symlink() or not source_directory.is_dir():
        raise ArchiveError("distribution directory must be one regular directory")
    candidates = sorted(source_directory.glob("*.tar.gz"))
    if len(candidates) != 1:
        raise ArchiveError("distribution directory must contain exactly one source archive")
    output_directory = _absolute_without_links(output_directory)
    output_directory.mkdir(parents=True, exist_ok=True)
    _absolute_without_links(output_directory)
    destination = output_directory / candidates[0].name
    canonicalize(candidates[0], destination, epoch)
    return destination


def self_test() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        raw = root / "raw.tar.gz"
        result_a = root / "canonical-a.tar.gz"
        result_b = root / "canonical-b.tar.gz"
        with tarfile.open(raw, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
            directory_info = tarfile.TarInfo("sample-0.0.0")
            directory_info.type = tarfile.DIRTYPE
            directory_info.uid = 1234
            directory_info.gid = 5678
            directory_info.uname = "synthetic-owner"
            directory_info.gname = "synthetic-group"
            directory_info.mtime = 123456789
            archive.addfile(directory_info)
            data = b"synthetic\n"
            file_info = tarfile.TarInfo("sample-0.0.0/README.md")
            file_info.size = len(data)
            file_info.uid = 1234
            file_info.gid = 5678
            file_info.uname = "synthetic-owner"
            file_info.gname = "synthetic-group"
            file_info.mtime = 123456789
            archive.addfile(file_info, io.BytesIO(data))
        canonicalize(raw, result_a, 946684800)
        canonicalize(raw, result_b, 946684800)
        if result_a.read_bytes() != result_b.read_bytes():
            raise ArchiveError("canonical outputs are not byte-for-byte reproducible")

        same_metadata = root / "same-metadata.tar.gz"
        same_metadata.write_bytes(raw.read_bytes())
        raw_metadata = raw.stat()
        os.utime(
            same_metadata,
            ns=(raw_metadata.st_atime_ns, raw_metadata.st_mtime_ns),
        )
        identity_descriptor = _open_readonly(raw)
        try:
            _ensure_path_matches_descriptor(
                raw,
                identity_descriptor,
                _metadata_identity(raw.lstat()),
                "matching source descriptor was rejected",
            )
            try:
                _ensure_path_matches_descriptor(
                    same_metadata,
                    identity_descriptor,
                    _metadata_identity(raw.lstat()),
                    "same-metadata replacement was rejected",
                )
            except ArchiveError:
                pass
            else:
                raise ArchiveError("same-metadata replacement was not rejected")
        finally:
            os.close(identity_descriptor)

        swap_source = root / "swap-source.tar.gz"
        swap_replacement = root / "swap-replacement.tar.gz"
        swap_output = root / "swap-output.tar.gz"
        for candidate in (swap_source, swap_replacement):
            candidate.write_bytes(raw.read_bytes())
            os.utime(
                candidate,
                ns=(raw_metadata.st_atime_ns, raw_metadata.st_mtime_ns),
            )
        original_open_readonly = _open_readonly
        swap_done = False

        def swapping_open(path: Path) -> int:
            nonlocal swap_done
            if path == swap_source and not swap_done:
                swap_done = True
                os.replace(swap_replacement, swap_source)
            return original_open_readonly(path)

        globals()["_open_readonly"] = swapping_open
        try:
            try:
                canonicalize(swap_source, swap_output, 946684800)
            except ArchiveError:
                pass
            else:
                raise ArchiveError("pre-open source replacement was not rejected")
        finally:
            globals()["_open_readonly"] = original_open_readonly
        if not swap_done or swap_output.exists():
            raise ArchiveError("pre-open source replacement test was incomplete")

        restored_source = root / "restored-source.tar.gz"
        restored_replacement = root / "restored-replacement.tar.gz"
        restored_output = root / "restored-output.tar.gz"
        for candidate in (restored_source, restored_replacement):
            candidate.write_bytes(raw.read_bytes())
            os.utime(
                candidate,
                ns=(raw_metadata.st_atime_ns, raw_metadata.st_mtime_ns),
            )
        original_open_readonly = _open_readonly
        wrong_descriptor_opened = False

        def opening_restored_replacement(path: Path) -> int:
            nonlocal wrong_descriptor_opened
            if path == restored_source and not wrong_descriptor_opened:
                wrong_descriptor_opened = True
                return original_open_readonly(restored_replacement)
            return original_open_readonly(path)

        globals()["_open_readonly"] = opening_restored_replacement
        try:
            try:
                canonicalize(restored_source, restored_output, 946684800)
            except ArchiveError:
                pass
            else:
                raise ArchiveError("restored source replacement was not rejected")
        finally:
            globals()["_open_readonly"] = original_open_readonly
        if not wrong_descriptor_opened or restored_output.exists():
            raise ArchiveError("restored source replacement test was incomplete")

        for index in (8, 9):
            tampered = root / f"tampered-{index}.tar.gz"
            altered = bytearray(result_a.read_bytes())
            altered[index] ^= 1
            tampered.write_bytes(altered)
            try:
                verify_archive(tampered, 946684800)
            except ArchiveError:
                pass
            else:
                raise ArchiveError("non-canonical gzip metadata was not rejected")

        plain_tar = root / "invalid-name.tar"
        with tarfile.open(plain_tar, mode="w") as archive:
            invalid_info = tarfile.TarInfo("sample")
            invalid_info.type = tarfile.DIRTYPE
            archive.addfile(invalid_info)
        invalid_bytes = bytearray(plain_tar.read_bytes())
        invalid_bytes[0] = 0xFF
        invalid_bytes[148:156] = b"        "
        checksum = sum(invalid_bytes[:512])
        invalid_bytes[148:156] = f"{checksum:06o}\0 ".encode("ascii")
        invalid_archive = root / "invalid-name.tar.gz"
        with invalid_archive.open("wb") as raw_stream:
            with gzip.GzipFile(
                filename="",
                mode="wb",
                fileobj=raw_stream,
                mtime=0,
            ) as compressed:
                compressed.write(invalid_bytes)
        try:
            canonicalize(invalid_archive, root / "invalid-name-output.tar.gz", 946684800)
        except ArchiveError:
            pass
        else:
            raise ArchiveError("non-UTF-8 archive path was not rejected")

        truncated_archive = root / "truncated.tar.gz"
        truncated_archive.write_bytes(b"\x1f\x8b\x08")
        try:
            canonicalize(truncated_archive, root / "truncated-output.tar.gz", 946684800)
        except ArchiveError:
            pass
        else:
            raise ArchiveError("truncated gzip input was not rejected")

        directory_payload = root / "directory-payload.tar.gz"
        with tarfile.open(directory_payload, mode="w:gz") as archive:
            directory_with_data = tarfile.TarInfo("sample")
            directory_with_data.type = tarfile.DIRTYPE
            directory_with_data.size = 1
            archive.addfile(directory_with_data, io.BytesIO(b"x"))
        try:
            canonicalize(
                directory_payload,
                root / "directory-payload-output.tar.gz",
                946684800,
            )
        except ArchiveError:
            pass
        else:
            raise ArchiveError("directory payload was not rejected")

        valid_gzip = raw.read_bytes()
        corrupt_crc = bytearray(valid_gzip)
        corrupt_crc[-8] ^= 1
        corrupt_size = bytearray(valid_gzip)
        corrupt_size[-4] ^= 1
        gzip_integrity_cases = (
            ("tail-one", valid_gzip[:-1]),
            ("tail-eight", valid_gzip[:-8]),
            ("crc", bytes(corrupt_crc)),
            ("size", bytes(corrupt_size)),
            ("trailing", valid_gzip + b"trailing"),
            ("concatenated", valid_gzip + gzip.compress(b"synthetic")),
        )
        for label, payload in gzip_integrity_cases:
            malformed = root / f"gzip-{label}.tar.gz"
            malformed.write_bytes(payload)
            try:
                canonicalize(
                    malformed,
                    root / f"gzip-{label}-output.tar.gz",
                    946684800,
                )
            except ArchiveError:
                continue
            raise ArchiveError("gzip integrity violation was not rejected")

        existing = root / "existing.tar.gz"
        existing.write_bytes(b"do-not-overwrite")
        try:
            canonicalize(raw, existing, 946684800)
        except ArchiveError:
            pass
        else:
            raise ArchiveError("existing output was not rejected")
        if existing.read_bytes() != b"do-not-overwrite":
            raise ArchiveError("existing output was modified")

        unsafe_cases = (
            ("parent", (("../escape", tarfile.REGTYPE),)),
            ("case", (("sample/A.txt", tarfile.REGTYPE), ("sample/a.txt", tarfile.REGTYPE))),
            ("device", (("sample/CON.txt", tarfile.REGTYPE),)),
            ("device-stream", (("sample/CONIN$.txt", tarfile.REGTYPE),)),
            ("windows-character", (("sample/bad:name", tarfile.REGTYPE),)),
            ("link", (("sample/link", tarfile.SYMTYPE),)),
            (
                "file-ancestor",
                (
                    ("sample", tarfile.DIRTYPE),
                    ("sample/node", tarfile.REGTYPE),
                    ("sample/node/child", tarfile.REGTYPE),
                ),
            ),
            ("top-file", (("sample.txt", tarfile.REGTYPE),)),
        )
        for label, members in unsafe_cases:
            unsafe = root / f"unsafe-{label}.tar.gz"
            with tarfile.open(unsafe, mode="w:gz") as archive:
                for name, member_type in members:
                    info = tarfile.TarInfo(name)
                    info.type = member_type
                    if member_type == tarfile.REGTYPE:
                        payload = b"synthetic\n"
                        info.size = len(payload)
                        archive.addfile(info, io.BytesIO(payload))
                    elif member_type == tarfile.DIRTYPE:
                        archive.addfile(info)
                    else:
                        info.linkname = "sample/target"
                        archive.addfile(info)
            try:
                canonicalize(unsafe, root / f"unsafe-{label}-output.tar.gz", 946684800)
            except ArchiveError:
                continue
            raise ArchiveError("unsafe synthetic archive was not rejected")

        class SyntheticReparseMetadata:
            st_mode = stat.S_IFDIR | 0o755
            st_file_attributes = WINDOWS_REPARSE_POINT

        if not _is_link_like(SyntheticReparseMetadata()):
            raise ArchiveError("Windows reparse-point metadata was not rejected")


def main() -> int:
    parser = SafeArgumentParser(allow_abbrev=False)
    parser.add_argument("source", nargs="?", type=Path)
    parser.add_argument("destination", nargs="?", type=Path)
    parser.add_argument("--source-date-epoch", type=int)
    parser.add_argument("--dist-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            if any(
                value is not None
                for value in (
                    args.source,
                    args.destination,
                    args.source_date_epoch,
                    args.dist_dir,
                    args.output_dir,
                )
            ):
                parser.error("--self-test does not accept archive arguments")
            self_test()
            print("canonical sdist self-test: PASS")
            return 0
        if args.source_date_epoch is None:
            parser.error("--source-date-epoch is required")
        if args.dist_dir is not None or args.output_dir is not None:
            if args.source is not None or args.destination is not None:
                parser.error("positional archives cannot be combined with directory mode")
            if args.dist_dir is None or args.output_dir is None:
                parser.error("--dist-dir and --output-dir must be used together")
            canonicalize_dist(args.dist_dir, args.output_dir, args.source_date_epoch)
        else:
            if args.source is None or args.destination is None:
                parser.error("SOURCE and DESTINATION are required")
            canonicalize(args.source, args.destination, args.source_date_epoch)
        print("canonical sdist: PASS")
        return 0
    except ArchiveError as exc:
        print(f"canonical sdist: FAIL ({exc})")
        return 1
    except OSError:
        print("canonical sdist: FAIL (filesystem operation failed)")
        return 1
    except (tarfile.TarError, UnicodeError, EOFError):
        print("canonical sdist: FAIL (archive rejected)")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
