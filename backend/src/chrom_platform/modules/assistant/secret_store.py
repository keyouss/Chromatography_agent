"""Windows CurrentUser DPAPI storage for assistant provider credentials."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import uuid


class SecretStoreError(RuntimeError):
    """Safe failure raised when protected secret storage is unavailable."""


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _blob(value: bytes):
    storage = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    return _DataBlob(len(value), ctypes.cast(storage, ctypes.POINTER(ctypes.c_ubyte))), storage


def _protect(value: bytes, *, unprotect: bool) -> bytes:
    if os.name != "nt":
        raise SecretStoreError("Windows CurrentUser DPAPI is unavailable")
    input_blob, input_storage = _blob(value)
    output_blob = _DataBlob()
    crypt = ctypes.windll.crypt32
    kernel = ctypes.windll.kernel32
    blob_pointer = ctypes.POINTER(_DataBlob)
    crypt.CryptProtectData.argtypes = [blob_pointer, wintypes.LPCWSTR, blob_pointer, wintypes.LPVOID, wintypes.LPVOID, wintypes.DWORD, blob_pointer]
    crypt.CryptProtectData.restype = wintypes.BOOL
    crypt.CryptUnprotectData.argtypes = [blob_pointer, ctypes.POINTER(wintypes.LPWSTR), blob_pointer, wintypes.LPVOID, wintypes.LPVOID, wintypes.DWORD, blob_pointer]
    crypt.CryptUnprotectData.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel.LocalFree.restype = wintypes.HLOCAL
    if unprotect:
        ok = crypt.CryptUnprotectData(ctypes.byref(input_blob), None, None, None, None, 0, ctypes.byref(output_blob))
    else:
        ok = crypt.CryptProtectData(ctypes.byref(input_blob), "Chromatography assistant LLM secret", None, None, None, 0, ctypes.byref(output_blob))
    del input_storage
    if not ok:
        raise SecretStoreError("DPAPI operation failed for the current Windows user")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel.LocalFree(output_blob.pbData)


def _current_user_sid() -> str:
    """Resolve the process token SID without depending on pywin32."""
    if os.name != "nt":
        raise SecretStoreError("Windows CurrentUser DPAPI is unavailable")
    advapi = ctypes.windll.advapi32
    kernel = ctypes.windll.kernel32
    token = wintypes.HANDLE()
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.GetTokenInformation.restype = wintypes.BOOL
    advapi.ConvertSidToStringSidW.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.LPWSTR)]
    advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise SecretStoreError("Current Windows user token is unavailable")
    try:
        needed = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        buffer = ctypes.create_string_buffer(needed.value)
        if not advapi.GetTokenInformation(token, 1, buffer, needed, ctypes.byref(needed)):
            raise SecretStoreError("Current Windows user identity is unavailable")
        sid_pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        text = wintypes.LPWSTR()
        if not advapi.ConvertSidToStringSidW(sid_pointer, ctypes.byref(text)):
            raise SecretStoreError("Current Windows user identity is unavailable")
        try:
            return text.value
        finally:
            kernel.LocalFree(text)
    finally:
        kernel.CloseHandle(token)


def _protect_acl(path: Path, *, directory: bool) -> None:
    """Set a protected DACL for the current user and SYSTEM only."""
    advapi = ctypes.windll.advapi32
    kernel = ctypes.windll.kernel32
    sid = _current_user_sid()
    inheritance = "OICI" if directory else ""
    descriptor = wintypes.LPVOID()
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.DWORD)]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    advapi.GetSecurityDescriptorDacl.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.BOOL)]
    advapi.GetSecurityDescriptorDacl.restype = wintypes.BOOL
    advapi.SetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID]
    advapi.SetNamedSecurityInfoW.restype = wintypes.DWORD
    kernel.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel.LocalFree.restype = wintypes.HLOCAL
    if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        f"D:P(A;{inheritance};FA;;;{sid})(A;{inheritance};FA;;;SY)", 1, ctypes.byref(descriptor), None
    ):
        raise SecretStoreError("Protected secret ACL could not be prepared")
    try:
        dacl = wintypes.LPVOID()
        present = wintypes.BOOL()
        defaulted = wintypes.BOOL()
        if not advapi.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)) or not present.value:
            raise SecretStoreError("Protected secret ACL could not be read")
        result = advapi.SetNamedSecurityInfoW(
            str(path), 1, 0x00000004 | 0x80000000, None, None, dacl, None
        )
        if result != 0:
            raise SecretStoreError("Protected secret ACL could not be applied")
    finally:
        kernel.LocalFree(descriptor)


def _is_reparse(path: Path) -> bool:
    try:
        attributes = path.lstat().st_file_attributes
    except FileNotFoundError:
        return False
    return bool(attributes & 0x400) or path.is_symlink()


def _resolve_secret_root(candidate: str | Path, *, require_runtime_exists: bool = False) -> Path:
    """Validate the secret root against the local or explicitly bound runtime."""
    candidate = Path(candidate)
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if not candidate.is_absolute() or candidate.name != "assistant-llm-secrets" or not local_app_data:
        raise SecretStoreError("Protected secret root is invalid")
    if ".." in candidate.parts:
        raise SecretStoreError("Protected secret root is invalid")

    runtime_base = (Path(local_app_data) / "ChromatographyPlatform").absolute()
    root = candidate.absolute()
    try:
        root.relative_to(runtime_base)
    except ValueError as exc:
        raise SecretStoreError("Protected secret directory must be inside the approved runtime root") from exc
    if require_runtime_exists and not runtime_base.is_dir():
        raise SecretStoreError("Protected runtime root is unavailable")

    configured_runtime = os.environ.get("CHROM_ASSISTANT_RUNTIME_ROOT", "").strip()
    if configured_runtime:
        runtime = Path(configured_runtime)
        if (
            not runtime.is_absolute()
            or ".." in runtime.parts
            or runtime.absolute().parent != runtime_base
            or root != runtime.absolute() / "assistant-llm-secrets"
            or (require_runtime_exists and not runtime.is_dir())
        ):
            raise SecretStoreError("Protected secret root does not match the approved runtime")
        canonical_anchor = runtime
    else:
        canonical_anchor = runtime_base

    chain = []
    current = root
    while current != runtime_base:
        chain.append(current)
        parent = current.parent
        if parent == current:
            raise SecretStoreError("Protected secret directory escapes the approved runtime root")
        current = parent
    chain.append(runtime_base)
    if any(path.exists() and _is_reparse(path) for path in chain):
        raise SecretStoreError("Protected secret directory contains a reparse point")

    if require_runtime_exists and not root.parent.is_dir():
        raise SecretStoreError("Protected runtime directory is unavailable")
    try:
        if not root.resolve(strict=False).is_relative_to(canonical_anchor.resolve(strict=require_runtime_exists)):
            raise ValueError("secret root resolves outside its approved anchor")
    except (OSError, ValueError) as exc:
        raise SecretStoreError("Protected secret directory must be inside the approved runtime root") from exc
    return root


class WindowsDpapiSecretStore:
    """Store opaque references under an explicitly supplied restricted root."""

    PREFIX = "dpapi:v1:"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        if not self.root.is_absolute() or self.root.name != "assistant-llm-secrets":
            raise SecretStoreError("Protected secret root must be an absolute assistant secret directory")

    def _ensure_root(self) -> Path:
        if os.name != "nt":
            raise SecretStoreError("Windows CurrentUser DPAPI is unavailable")
        root = _resolve_secret_root(self.root, require_runtime_exists=True)
        if not root.exists():
            root.mkdir(mode=0o700)
        if not root.is_dir() or _is_reparse(root):
            raise SecretStoreError("Protected secret directory is invalid")
        _protect_acl(root, directory=True)
        return root

    @classmethod
    def _identifier(cls, reference: str) -> str:
        if not isinstance(reference, str) or not reference.startswith(cls.PREFIX):
            raise SecretStoreError("Protected secret reference is invalid")
        identifier = reference[len(cls.PREFIX):]
        if len(identifier) != 32 or any(ch not in "0123456789abcdef" for ch in identifier):
            raise SecretStoreError("Protected secret reference is invalid")
        return identifier

    def put(self, secret: str) -> str:
        if not isinstance(secret, str) or not secret:
            raise SecretStoreError("Secret value is empty")
        root = self._ensure_root()
        identifier = uuid.uuid4().hex
        target = root / f"{identifier}.dpapi"
        temp = root / f".{identifier}.{uuid.uuid4().hex}.tmp"
        try:
            protected = _protect(secret.encode("utf-8"), unprotect=False)
            with temp.open("xb") as handle:
                handle.write(protected)
                handle.flush()
                os.fsync(handle.fileno())
            _protect_acl(temp, directory=False)
            if target.exists() or _is_reparse(target):
                raise SecretStoreError("Protected secret identifier collision")
            os.replace(temp, target)
            _protect_acl(target, directory=False)
        except SecretStoreError:
            raise
        except Exception as exc:
            raise SecretStoreError("Protected secret could not be written") from exc
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
        return self.PREFIX + identifier

    def get(self, reference: str) -> str:
        root = self._ensure_root()
        target = root / f"{self._identifier(reference)}.dpapi"
        try:
            if not target.is_file() or _is_reparse(target):
                raise SecretStoreError("Protected secret is unavailable")
            protected = target.read_bytes()
            if not protected or len(protected) > 1_048_576:
                raise SecretStoreError("Protected secret payload is invalid")
            return _protect(protected, unprotect=True).decode("utf-8")
        except SecretStoreError:
            raise
        except Exception as exc:
            raise SecretStoreError("Protected secret could not be decrypted for the current Windows user") from exc

    def delete(self, reference: str) -> None:
        root = self._ensure_root()
        target = root / f"{self._identifier(reference)}.dpapi"
        try:
            if _is_reparse(target):
                raise SecretStoreError("Protected secret path is invalid")
            target.unlink(missing_ok=True)
        except SecretStoreError:
            raise
        except OSError as exc:
            raise SecretStoreError("Protected secret could not be removed") from exc


__all__ = ["SecretStoreError", "WindowsDpapiSecretStore"]


def default_secret_store() -> WindowsDpapiSecretStore | None:
    """Resolve the protected deployment root without reading its files."""
    if os.name != "nt":
        return None
    configured = os.environ.get("CHROM_ASSISTANT_SECRET_ROOT", "").strip()
    if not configured:
        return None
    candidate = Path(configured)
    try:
        _resolve_secret_root(candidate)
    except (SecretStoreError, OSError, ValueError):
        return None
    return WindowsDpapiSecretStore(candidate)


__all__.append("default_secret_store")
