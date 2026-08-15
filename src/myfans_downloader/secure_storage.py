"""Windows user-scoped secret protection using the built-in DPAPI."""

from __future__ import annotations

import base64
import ctypes
import os
from ctypes import wintypes

PREFIX = "dpapi:"
_DESCRIPTION = "MyFans Downloader Auth Token"
_ENTROPY = b"MyFansDownloader/auth-token/v1"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _input_blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data)
    blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    return blob, buffer


def _crypt32():
    library = ctypes.WinDLL("crypt32", use_last_error=True)
    library.CryptProtectData.argtypes = (
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    )
    library.CryptProtectData.restype = wintypes.BOOL
    library.CryptUnprotectData.argtypes = (
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    )
    library.CryptUnprotectData.restype = wintypes.BOOL
    return library


def _local_free(pointer) -> None:
    if pointer:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.LocalFree.argtypes = (wintypes.HLOCAL,)
        kernel32.LocalFree.restype = wintypes.HLOCAL
        kernel32.LocalFree(ctypes.cast(pointer, wintypes.HLOCAL))


def protect_secret(secret: str) -> str:
    if not secret:
        return ""
    if os.name != "nt":
        raise OSError("Secure token storage requires Windows DPAPI.")

    source, source_buffer = _input_blob(secret.encode("utf-8"))
    entropy, entropy_buffer = _input_blob(_ENTROPY)
    output = _DataBlob()
    # Keep the backing buffers alive for the duration of the native call.
    _ = (source_buffer, entropy_buffer)
    if not _crypt32().CryptProtectData(
        ctypes.byref(source),
        _DESCRIPTION,
        ctypes.byref(entropy),
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        encrypted = ctypes.string_at(output.pbData, output.cbData)
        return PREFIX + base64.b64encode(encrypted).decode("ascii")
    finally:
        _local_free(output.pbData)


def unprotect_secret(value: str) -> str:
    if not value:
        return ""
    if not value.startswith(PREFIX):
        raise OSError(
            "The stored Auth Token is not protected. Enter it again in Settings."
        )
    if os.name != "nt":
        raise OSError("This token was protected for a Windows user account.")

    try:
        encrypted = base64.b64decode(value[len(PREFIX) :], validate=True)
    except ValueError as exc:
        raise OSError("The protected Auth Token is corrupted.") from exc
    source, source_buffer = _input_blob(encrypted)
    entropy, entropy_buffer = _input_blob(_ENTROPY)
    output = _DataBlob()
    description = wintypes.LPWSTR()
    _ = (source_buffer, entropy_buffer)
    if not _crypt32().CryptUnprotectData(
        ctypes.byref(source),
        ctypes.byref(description),
        ctypes.byref(entropy),
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output),
    ):
        raise OSError(
            "The Auth Token cannot be decrypted by the current Windows user. "
            "Enter it again in Settings."
        )
    try:
        return ctypes.string_at(output.pbData, output.cbData).decode("utf-8")
    finally:
        _local_free(output.pbData)
        _local_free(description)
