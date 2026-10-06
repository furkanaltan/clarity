"""Local recovery-set envelope. No collection, network, replay or restore activation."""

from __future__ import annotations

import argparse
import contextlib
import getpass
import gzip
import hashlib
import io
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import rove_recovery_set as recovery

VERSION = "1.0"
MAX_BYTES = 1024 * 1024 * 1024
MAX_ARCHIVE = MAX_BYTES + 32 * 1024 * 1024
MAX_FILES = 10000
SET_ID = re.compile(r"recovery_set_[0-9]{8}T[0-9]{6}Z_[0-9a-f]{32}")
FINGERPRINT = re.compile(r"[0-9A-F]{40}")
HASH = re.compile(r"[0-9a-f]{64}")
ARTIFACT = re.compile(
    r"artifacts/(?:public_reports/(?:[A-Za-z0-9_-]{16,128}/index\.html|support\.js)"
    rf"|reports/(?:archive/)?{recovery.PDF.pattern})"
)
META_FIELDS = {
    "wrapper_version", "recovery_set_id", "git_sha", "manifest_sha256",
    "archive_sha256", "ciphertext_sha256", "ciphertext_bytes", "encrypted_at_utc",
    "public_key_fingerprint", "encryption_key_fingerprint",
}


class CryptoError(RuntimeError):
    """Fixed error codes only; never relay GPG stderr, key UIDs or file contents."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise CryptoError(code)


def fingerprint(value: str) -> str:
    require(isinstance(value, str) and bool(FINGERPRINT.fullmatch(value)), "fingerprint_invalid")
    return value


def absolute(value: Path) -> Path:
    path = Path(value)
    require(path.is_absolute() and ".." not in path.parts, "path_invalid")
    return path


@contextlib.contextmanager
def directory(path: Path):
    """Anchor each path component, rather than following mutable directory symlinks."""
    path = absolute(path)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = new
        yield fd
    finally:
        os.close(fd)


def identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def read_file(path: Path, *, limit: int, target: Path | None = None) -> tuple[bytes, dict]:
    path = absolute(path)
    with directory(path.parent) as parent:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb") as source:
            before = os.fstat(source.fileno())
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, "file_type_invalid")
            require(before.st_size <= limit, "file_too_large")
            sink = target.open("xb") if target else io.BytesIO()
            try:
                if target:
                    os.chmod(target, 0o600)
                sha, size = hashlib.sha256(), 0
                while chunk := source.read(1024 * 1024):
                    size += len(chunk)
                    require(size <= limit, "file_too_large")
                    sha.update(chunk)
                    sink.write(chunk)
                after = os.fstat(source.fileno())
                current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
                require(identity(before) == identity(after) == identity(current), "source_changed")
                require(size == before.st_size, "source_changed")
                if target:
                    sink.flush()
                    os.fsync(sink.fileno())
                return (b"" if target else sink.getvalue()), {"sha256": sha.hexdigest(), "bytes": size}
            finally:
                sink.close()


def inventory(root: Path) -> dict[str, bool]:
    result = {}

    def visit(fd, prefix=""):
        require(not stat.S_IMODE(os.fstat(fd).st_mode) & 0o077, "set_not_private")
        for name in sorted(os.listdir(fd)):
            require(len(result) < MAX_FILES * 3, "too_many_entries")
            relative = prefix + name
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            require(not stat.S_IMODE(info.st_mode) & 0o077, "set_not_private")
            if stat.S_ISDIR(info.st_mode):
                result[relative] = True
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    require(identity(info) == identity(os.fstat(child)), "source_changed")
                    visit(child, relative + "/")
                finally:
                    os.close(child)
            else:
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "file_type_invalid")
                result[relative] = False

    with directory(root) as fd:
        visit(fd)
    return result


def layout(raw: bytes, set_id: str) -> tuple[dict, dict, dict]:
    manifest = recovery.strict_json(raw)
    require(isinstance(manifest, dict) and manifest.get("status") == "COMPLETE", "set_not_complete")
    require(bool(SET_ID.fullmatch(set_id)) and manifest.get("recovery_set_id") == set_id, "set_id_invalid")
    require(isinstance(manifest.get("account_restore_safety"), dict)
            and manifest["account_restore_safety"].get("status") == recovery.SAFE_FOR_ACCOUNT_RESTORE,
            "generation_not_safe")
    required = {"database": "database/clarity.db", "ledger": "ledger/account_delete_tombstones.jsonl",
                "runtime": "runtime/metadata.json"}
    records = {"manifest.json": {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}}
    for key, path in required.items():
        require(isinstance(manifest.get(key), dict) and manifest[key].get("path") == path,
                "required_path_invalid")
    require(isinstance(manifest.get("artifacts"), list), "manifest_invalid")
    items = [manifest[key] for key in required] + manifest["artifacts"]
    require(len(items) < MAX_FILES, "too_many_entries")
    for item in items:
        require(isinstance(item, dict), "manifest_invalid")
        path = item.get("path")
        require(isinstance(path, str) and path not in records
                and (path in required.values() or bool(ARTIFACT.fullmatch(path))), "manifest_path_invalid")
        require(type(item.get("bytes")) is int and 0 <= item["bytes"] <= MAX_BYTES
                and isinstance(item.get("sha256"), str) and bool(HASH.fullmatch(item["sha256"])),
                "manifest_checksum_invalid")
        records[path] = {"bytes": item["bytes"], "sha256": item["sha256"]}
    require(sum(item["bytes"] for item in records.values()) <= MAX_BYTES, "set_too_large")
    entries = dict.fromkeys(records, False)
    for path in records:
        for parent in PurePosixPath(path).parents:
            if str(parent) != ".":
                entries[str(parent)] = True
    return manifest, records, entries


def check_set(root: Path) -> tuple[dict, dict]:
    raw, _ = read_file(root / "manifest.json", limit=8 * 1024 * 1024)
    manifest, records, entries = layout(raw, root.name)
    require(inventory(root) == entries, "set_inventory_mismatch")
    # Called only on our isolated copy: SQLite never opens the input/source DB.
    recovery.verify_recovery_set(root)
    return manifest, records


def snapshot(source: Path, workspace: Path) -> tuple[Path, dict, dict]:
    raw, _ = read_file(source / "manifest.json", limit=8 * 1024 * 1024)
    _, records, entries = layout(raw, source.name)
    require(inventory(source) == entries, "set_inventory_mismatch")
    target = workspace / source.name
    target.mkdir(mode=0o700)
    for name, is_dir in sorted(entries.items()):
        path = target / name
        if is_dir:
            path.mkdir(mode=0o700)
        else:
            _, info = read_file(source / name, limit=records[name]["bytes"], target=path)
            require(info == records[name], "source_checksum_mismatch")
    manifest, copied = check_set(target)
    unchanged(source, records, entries)
    require(copied == records, "source_changed")
    return target, manifest, records


def unchanged(source: Path, records: dict, entries: dict | None = None) -> None:
    raw, _ = read_file(source / "manifest.json", limit=8 * 1024 * 1024)
    _, current, expected = layout(raw, source.name)
    require(current == records and inventory(source) == (entries or expected), "source_changed")
    for name, item in records.items():
        # Stream to a hash without retaining the complete set in memory.
        with directory((source / name).parent) as parent:
            fd = os.open(Path(name).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            with os.fdopen(fd, "rb") as handle:
                info = os.fstat(handle.fileno())
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                        and info.st_size == item["bytes"], "source_changed")
                sha, size = hashlib.sha256(), 0
                while chunk := handle.read(1024 * 1024):
                    size += len(chunk)
                    require(size <= item["bytes"], "source_changed")
                    sha.update(chunk)
                require(identity(info) == identity(os.fstat(handle.fileno()))
                        == identity(os.stat(Path(name).name, dir_fd=parent, follow_symlinks=False)), "source_changed")
                require(size == item["bytes"] and sha.hexdigest() == item["sha256"], "source_changed")
    require(inventory(source) == expected, "source_changed")


def archive_set(root: Path, output: Path, *, compressed: bool = True) -> None:
    entries = {root.name: True, **{root.name + "/" + name: is_dir for name, is_dir in inventory(root).items()}}
    with output.open("xb") as raw:
        os.chmod(output, 0o600)
        envelope = (gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=6)
                    if compressed else contextlib.nullcontext(raw))
        with envelope as stream:
            with tarfile.open(fileobj=stream, mode="w|", format=tarfile.USTAR_FORMAT) as archive:
                for name, is_dir in sorted(entries.items()):
                    item = tarfile.TarInfo(name)
                    item.type = tarfile.DIRTYPE if is_dir else tarfile.REGTYPE
                    item.mode = 0o700 if is_dir else 0o600
                    if is_dir:
                        archive.addfile(item)
                    else:
                        path = root.parent / name
                        item.size = path.stat().st_size
                        with path.open("rb") as content:
                            archive.addfile(item, content)
        raw.flush()
        os.fsync(raw.fileno())
    require(output.stat().st_size <= MAX_ARCHIVE, "archive_too_large")


def file_hash(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def unpack(archive_path: Path, destination: Path, set_id: str) -> Path:
    # Bound decompression BEFORE tar parsing (including hidden padding/PAX data).
    tar_path = destination / "unpacked.tar"
    with gzip.open(archive_path, "rb") as source, tar_path.open("xb") as target:
        os.chmod(tar_path, 0o600)
        total = 0
        while chunk := source.read(1024 * 1024):
            total += len(chunk)
            require(total <= MAX_ARCHIVE, "archive_too_large")
            target.write(chunk)
    seen, total = set(), 0
    with tarfile.open(tar_path, "r:") as archive:
        for item in archive:
            require(len(seen) < MAX_FILES * 3 and not item.pax_headers, "archive_structure_invalid")
            name = item.name
            path = PurePosixPath(name)
            require(not path.is_absolute() and str(path) == name and ".." not in path.parts
                    and "\\" not in name and all(ord(c) >= 32 for c in name)
                    and path.parts and path.parts[0] == set_id and name not in seen, "archive_path_invalid")
            relative = "/".join(path.parts[1:])
            require((item.type == tarfile.DIRTYPE or item.type == tarfile.REGTYPE)
                    and not item.linkname and not item.sparse, "archive_type_invalid")
            require(item.mode == (0o700 if item.isdir() else 0o600)
                    and item.uid == item.gid == item.mtime == 0
                    and not item.uname and not item.gname, "archive_metadata_invalid")
            require(item.isdir() or relative in {"manifest.json", "database/clarity.db",
                    "ledger/account_delete_tombstones.jsonl", "runtime/metadata.json"}
                    or bool(ARTIFACT.fullmatch(relative)), "archive_file_not_allowed")
            require(0 <= item.size <= MAX_BYTES, "archive_too_large")
            total += item.size
            require(total <= MAX_BYTES and (not item.isdir() or item.size == 0), "archive_too_large")
            seen.add(name)
            target = destination / name
            if item.isdir():
                target.mkdir(mode=0o700)
            else:
                with archive.extractfile(item) as source, target.open("xb") as output:
                    os.chmod(target, 0o600)
                    shutil.copyfileobj(source, output, 1024 * 1024)
    # Compare canonical TAR bytes, not recompressed gzip bytes across zlib versions.
    canonical = destination / "canonical.tar"
    archive_set(destination / set_id, canonical, compressed=False)
    require(file_hash(tar_path) == file_hash(canonical), "archive_not_canonical")
    canonical.unlink()
    tar_path.unlink()
    return destination / set_id


class GPG:
    def __init__(self, executable: str, home: Path):
        resolved = shutil.which(executable)
        require(bool(resolved), "gpg_unavailable")
        self.executable = str(Path(resolved).resolve())
        self.home = home
        self.agent_used = False
        self.env = {"PATH": str(Path(self.executable).parent) + os.pathsep + os.defpath,
                    "HOME": str(home), "GNUPGHOME": str(home), "LC_ALL": "C"}
        version = self.run(["--version"])
        match = re.search(rb"gpg \(GnuPG\) (\d+)\.(\d+)\.(\d+)", version)
        require(bool(match) and tuple(map(int, match.groups())) >= (2, 4, 4), "gpg_version_unsupported")

    def run(self, args: list[str], *, secret: bytes | None = None, agent: bool = False) -> bytes:
        self.agent_used |= agent
        command = [self.executable, "--no-options", "--homedir", str(self.home), "--batch", "--no-tty",
                   "--auto-key-locate", "clear", "--no-auto-key-retrieve", "--no-auto-key-import"]
        if not agent:
            command.append("--no-autostart")
        else:
            require(os.access(Path(self.executable).with_name("gpgconf"), os.X_OK), "gpgconf_unavailable")
        try:
            result = subprocess.run(command + args, input=secret, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, env=self.env, timeout=180, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise CryptoError("gpg_execution_failed") from None
        require(result.returncode == 0, "gpg_operation_failed")
        return result.stdout

    def import_key(self, key_path: Path, expected: str, *, private: bool = False,
                   encryption_fingerprint: str | None = None) -> str:
        if private:
            with directory(key_path.parent) as parent:
                info = os.stat(key_path.name, dir_fd=parent, follow_symlinks=False)
                require(info.st_uid == os.geteuid() and not stat.S_IMODE(info.st_mode) & 0o077,
                        "private_key_permissions_invalid")
        raw, _ = read_file(key_path, limit=1024 * 1024)
        temporary = self.home / "input-key"
        recovery.private_write(temporary, raw)
        try:
            output = self.run(["--with-colons", "--import-options", "show-only", "--dry-run", "--import", str(temporary)])
            keys, current = [], None
            for line in output.decode("utf-8", "replace").splitlines():
                fields = line.split(":")
                if fields[0] in {"pub", "sub", "sec", "ssb"}:
                    current = {"kind": fields[0], "validity": fields[1], "expires": fields[6],
                               "capabilities": fields[11], "fingerprint": None}
                    keys.append(current)
                elif fields[0] == "fpr" and current and current["fingerprint"] is None:
                    current["fingerprint"] = fields[9]
            primary = [key for key in keys if key["kind"] in {"pub", "sec"}]
            require(len(primary) == 1 and primary[0]["fingerprint"] == fingerprint(expected), "key_identity_mismatch")
            require(primary[0]["kind"] == ("sec" if private else "pub")
                    and (private or all(key["kind"] in {"pub", "sub"} for key in keys)), "key_material_invalid")
            def usable(key):
                return (key["validity"] not in {"r", "e", "d", "i"} and "D" not in key["capabilities"]
                        and (not key["expires"] or int(key["expires"]) > time.time()))
            if private:
                # Expiry/revocation must stop NEW encryption, not historical decryption.
                selected = fingerprint(encryption_fingerprint or expected)
                candidates = [key for key in keys if key["fingerprint"] == selected]
            else:
                require(usable(primary[0]), "key_unusable")
                candidates = [key for key in keys if "e" in key["capabilities"] and usable(key)]
            require(len(candidates) == 1, "encryption_key_ambiguous_or_missing")
            selected = fingerprint(candidates[0]["fingerprint"])
            self.run(["--import", str(temporary)], agent=private)
            return selected
        finally:
            temporary.unlink(missing_ok=True)

    def encrypt(self, archive: Path, output: Path, recipient: str) -> None:
        status = self.run(["--status-fd", "1", "--rfc4880", "--trust-model", "always",
                           "--cipher-algo", "AES256", "--compress-algo", "none", "--set-filename", "",
                           "--recipient", fingerprint(recipient) + "!", "--output", str(output),
                           "--encrypt", str(archive)])
        require(b"[GNUPG:] END_ENCRYPTION" in status, "encryption_incomplete")
        output.chmod(0o600)

    def decrypt(self, cipher: Path, output: Path, primary: str, recipient: str, passphrase: bytes) -> None:
        require(len(passphrase) <= 4096 and b"\n" not in passphrase and b"\r" not in passphrase,
                "passphrase_input_invalid")
        status = self.run(["--status-fd", "1", "--max-output", str(MAX_ARCHIVE), "--pinentry-mode", "loopback",
                           "--passphrase-fd", "0", "--output", str(output), "--decrypt", str(cipher)],
                          secret=passphrase + b"\n", agent=True)
        lines = [line.split() for line in status.decode("utf-8", "replace").splitlines()]
        keys = [line[2:4] for line in lines if line[:2] == ["[GNUPG:]", "DECRYPTION_KEY"]]
        targets = [line[2] for line in lines if line[:2] == ["[GNUPG:]", "ENC_TO"]]
        require(keys == [[recipient, primary]] and targets == [recipient[-16:]], "decryption_recipient_mismatch")
        for code in ("DECRYPTION_OKAY", "GOODMDC", "END_DECRYPTION"):
            require(sum(line[:2] == ["[GNUPG:]", code] for line in lines) == 1, "decryption_integrity_failed")
        output.chmod(0o600)

    def close(self) -> None:
        if self.agent_used:
            command = Path(self.executable).with_name("gpgconf")
            try:
                result = subprocess.run([str(command), "--homedir", str(self.home), "--kill", "gpg-agent"],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.env, timeout=15)
                require(result.returncode == 0, "gpg_agent_cleanup_failed")
            except (OSError, subprocess.TimeoutExpired):
                raise CryptoError("gpg_agent_cleanup_failed") from None


@contextlib.contextmanager
def gpg_session(executable: str, *, temp_parent: Path | None = None):
    # Keep temporary keyrings beside the private workspace, never in the default keyring.
    parent = Path("/tmp").resolve() if temp_parent is None else absolute(temp_parent)
    if temp_parent is not None:
        with directory(parent) as fd:
            info = os.fstat(fd)
            require(info.st_uid == os.geteuid() and not stat.S_IMODE(info.st_mode) & 0o077,
                    "gpg_workspace_not_private")
    socket_path_bytes = len(os.fsencode(parent)) + len(b"/rvc-XXXXXXXX/S.gpg-agent.browser")
    require(socket_path_bytes < 100, "gpg_workspace_path_too_long")
    with tempfile.TemporaryDirectory(prefix="rvc-", dir=str(parent)) as name:
        client = GPG(executable, Path(name))
        try:
            yield client
        finally:
            client.close()


@contextlib.contextmanager
def workspace(output: Path, inputs: list[Path]):
    output = absolute(output)
    with directory(output.parent) as parent:
        info = os.fstat(parent)
        require(info.st_uid == os.geteuid() and not stat.S_IMODE(info.st_mode) & 0o077,
                "output_parent_not_private")
    require(not output.exists() and not output.is_symlink(), "output_exists")
    for path in inputs:
        absolute(path)
        require(not output.is_relative_to(path) and not path.is_relative_to(output), "output_overlaps_input")
    with tempfile.TemporaryDirectory(prefix=".recovery-crypto-", dir=output.parent) as name:
        root = Path(name)
        publish = root / "publish"
        publish.mkdir(mode=0o700)
        yield root, publish
        # Exclusive publication: never replace an existing output, even an empty directory.
        output.mkdir(mode=0o700)
        try:
            for path in publish.iterdir():
                path.rename(output / path.name)
            with directory(output) as fd:
                os.fsync(fd)
        except BaseException:
            shutil.rmtree(output)
            raise


def validate_metadata(raw: bytes, expected_hash: str, expected_fingerprint: str) -> dict:
    require(bool(HASH.fullmatch(expected_hash)) and hashlib.sha256(raw).hexdigest() == expected_hash,
            "metadata_checksum_mismatch")
    data = recovery.strict_json(raw)
    require(isinstance(data, dict) and set(data) == META_FIELDS and data["wrapper_version"] == VERSION,
            "metadata_invalid")
    require(isinstance(data["recovery_set_id"], str) and bool(SET_ID.fullmatch(data["recovery_set_id"])), "metadata_invalid")
    for key in ("manifest_sha256", "archive_sha256", "ciphertext_sha256"):
        require(isinstance(data[key], str) and bool(HASH.fullmatch(data[key])), "metadata_invalid")
    require(isinstance(data["git_sha"], str) and bool(re.fullmatch("[0-9a-f]{40}", data["git_sha"])), "metadata_invalid")
    require(type(data["ciphertext_bytes"]) is int and 0 < data["ciphertext_bytes"] <= MAX_ARCHIVE,
            "metadata_invalid")
    fingerprint(data["encryption_key_fingerprint"])
    require(data["public_key_fingerprint"] == fingerprint(expected_fingerprint), "key_identity_mismatch")
    stamp = datetime.strptime(data["encrypted_at_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    require(stamp <= datetime.now(timezone.utc), "metadata_time_invalid")
    return data


def encrypt_set(source: Path, public_key: Path, expected_fingerprint: str, output: Path,
                *, gpg: str = "gpg", gpg_temp_parent: Path | None = None) -> dict:
    fingerprint(expected_fingerprint)
    with workspace(output, [source, public_key]) as (work, publish):
        copied, manifest, records = snapshot(source, work)
        archive = work / "set.tar.gz"
        archive_set(copied, archive)
        cipher = publish / (copied.name + ".tar.gz.gpg")
        with gpg_session(gpg, temp_parent=gpg_temp_parent or work) as client:
            recipient = client.import_key(public_key, expected_fingerprint)
            client.encrypt(archive, cipher, recipient)
        metadata = {
            "wrapper_version": VERSION, "recovery_set_id": copied.name,
            "git_sha": manifest["software"]["git_sha"], "manifest_sha256": records["manifest.json"]["sha256"],
            "archive_sha256": file_hash(archive), "ciphertext_sha256": file_hash(cipher),
            "ciphertext_bytes": cipher.stat().st_size,
            "encrypted_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "public_key_fingerprint": expected_fingerprint, "encryption_key_fingerprint": recipient,
        }
        raw = recovery.json_bytes(metadata)
        receipt = hashlib.sha256(raw).hexdigest()
        validate_metadata(raw, receipt, expected_fingerprint)
        recovery.private_write(publish / "crypto.json", raw)
        unchanged(source, records)
    return {"metadata_sha256": receipt, **metadata}


def verify_offline(ciphertext: Path, metadata_path: Path, metadata_sha256: str, private_key: Path,
                   expected_fingerprint: str, output: Path, *, passphrase: bytes = b"",
                   reference_set: Path | None = None, gpg: str = "gpg",
                   gpg_temp_parent: Path | None = None) -> dict:
    inputs = [ciphertext, metadata_path, private_key] + ([reference_set] if reference_set else [])
    with workspace(output, inputs) as (work, publish):
        raw, _ = read_file(metadata_path, limit=65536)
        metadata = validate_metadata(raw, metadata_sha256, expected_fingerprint)
        cipher = work / "cipher.gpg"
        _, info = read_file(ciphertext, limit=MAX_ARCHIVE, target=cipher)
        require(info == {"sha256": metadata["ciphertext_sha256"], "bytes": metadata["ciphertext_bytes"]},
                "ciphertext_checksum_mismatch")
        archive = work / "set.tar.gz"
        with gpg_session(gpg, temp_parent=gpg_temp_parent or work) as client:
            recipient = client.import_key(private_key, expected_fingerprint, private=True,
                                          encryption_fingerprint=metadata["encryption_key_fingerprint"])
            require(recipient == metadata["encryption_key_fingerprint"], "key_identity_mismatch")
            client.decrypt(cipher, archive, expected_fingerprint, recipient, passphrase)
        require(file_hash(archive) == metadata["archive_sha256"], "archive_checksum_mismatch")
        extracted = unpack(archive, publish, metadata["recovery_set_id"])
        manifest, records = check_set(extracted)
        require(records["manifest.json"]["sha256"] == metadata["manifest_sha256"]
                and manifest["software"]["git_sha"] == metadata["git_sha"], "manifest_binding_invalid")
        if reference_set:
            require(reference_set.name == extracted.name, "reference_set_mismatch")
            unchanged(reference_set, records)
    return {"recovery_set_id": metadata["recovery_set_id"], "verification": "PASS",
            "account_restore": "NOT_AUTHORIZED", "reference_compared": reference_set is not None}


def main() -> int:
    parser = argparse.ArgumentParser(description="Local recovery encryption/offline verification; never restore")
    parser.add_argument("--gpg", default="gpg")
    commands = parser.add_subparsers(dest="command", required=True)
    encrypt = commands.add_parser("encrypt")
    encrypt.add_argument("--gpg-temp-parent", type=Path)
    for name in ("set", "public-key", "output"):
        encrypt.add_argument("--" + name, type=Path, required=True)
    encrypt.add_argument("--fingerprint", required=True)
    verify = commands.add_parser("verify-offline")
    verify.add_argument("--gpg-temp-parent", type=Path)
    for name in ("ciphertext", "metadata", "private-key", "output"):
        verify.add_argument("--" + name, type=Path, required=True)
    verify.add_argument("--metadata-sha256", required=True)
    verify.add_argument("--fingerprint", required=True)
    verify.add_argument("--reference-set", type=Path)
    verify.add_argument("--passphrase-fd", type=int)
    args = parser.parse_args()
    try:
        if args.command == "encrypt":
            result = encrypt_set(args.set, args.public_key, args.fingerprint, args.output,
                                 gpg=args.gpg, gpg_temp_parent=args.gpg_temp_parent)
            print("ENCRYPTION=PASS")
            print("METADATA_SHA256=" + result["metadata_sha256"])
        else:
            if args.passphrase_fd is None:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", getpass.GetPassWarning)
                    secret = getpass.getpass("Offline key passphrase (not logged): ").encode("utf-8")
            else:
                with os.fdopen(os.dup(args.passphrase_fd), "rb") as handle:
                    secret = handle.readline(4098).rstrip(b"\n")
            verify_offline(args.ciphertext, args.metadata, args.metadata_sha256, args.private_key,
                           args.fingerprint, args.output, passphrase=secret,
                           reference_set=args.reference_set, gpg=args.gpg,
                           gpg_temp_parent=args.gpg_temp_parent)
            print("OFFLINE_VERIFICATION=PASS")
        print("ACCOUNT_RESTORE=NOT_AUTHORIZED; UPLOAD=NOT_RUN")
        return 0
    except Exception:
        # Even malformed JSON/GPG diagnostics can contain secrets supplied as input.
        print("RECOVERY_CRYPTO=FAIL; NO_OUTPUT_PUBLISHED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
