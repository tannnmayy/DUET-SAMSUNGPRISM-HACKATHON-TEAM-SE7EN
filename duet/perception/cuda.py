"""Make pip-installed CUDA libraries visible to CTranslate2.

The speech model runs on CTranslate2, whose Linux wheel is built against CUDA
12 and loads libcublas.so.12 and cuDNN 9 at run time by soname. Those
libraries arrive as pip packages (nvidia-cublas-cu12, nvidia-cudnn-cu12 -
dependencies of the pinned torch), which install under site-packages where
the dynamic loader does not look. LD_LIBRARY_PATH cannot fix that from inside
a running process, so the libraries are loaded here by absolute path with
RTLD_GLOBAL; CTranslate2's later dlopen by soname then finds them already
resident.

On Windows the CUDA DLLs live in torch/lib, which is added to the DLL search
path instead.

Everything here is best effort and silent on failure. Whether the GPU
actually works is decided by a trial transcription in setup() (asr.py), not
by this module: a library that loads is not proof that inference runs.
"""

from __future__ import annotations

import ctypes
import glob
import importlib.util
import os
import site
import sys
from typing import List

from .. import telemetry

_DONE = False

# Load order matters only in that dependencies must be resident first; the
# loop below retries until nothing further loads, so this is a preference.
_LINUX_LIBS = ("cuda_runtime", "cublas", "cudnn", "cuda_nvrtc")


def _site_dirs() -> List[str]:
    dirs: List[str] = []
    try:
        dirs.extend(site.getsitepackages())
    except Exception:  # noqa: BLE001 - virtualenvs without the API
        pass
    try:
        dirs.append(site.getusersitepackages())
    except Exception:  # noqa: BLE001
        pass
    dirs.extend(p for p in sys.path if p.endswith("site-packages"))
    seen, out = set(), []
    for d in dirs:
        if d and d not in seen and os.path.isdir(d):
            seen.add(d)
            out.append(d)
    return out


def _torch_lib_dir() -> str:
    spec = importlib.util.find_spec("torch")
    if spec is None or not spec.submodule_search_locations:
        return ""
    path = os.path.join(list(spec.submodule_search_locations)[0], "lib")
    return path if os.path.isdir(path) else ""


def prepare() -> List[str]:
    """Idempotent. Returns what was made visible, for the setup log."""
    global _DONE
    if _DONE:
        return []
    _DONE = True
    try:
        if sys.platform.startswith("win"):
            return _prepare_windows()
        if sys.platform.startswith("linux"):
            return _prepare_linux()
    except Exception as exc:  # noqa: BLE001 - never fatal
        telemetry.log("cuda.prepare_failed", error=type(exc).__name__ + ": " + str(exc))
    return []


def _prepare_windows() -> List[str]:
    added: List[str] = []
    candidates = [_torch_lib_dir()]
    for root in _site_dirs():
        candidates.extend(glob.glob(os.path.join(root, "nvidia", "*", "bin")))
    for directory in candidates:
        if not directory or not os.path.isdir(directory):
            continue
        try:
            os.add_dll_directory(directory)
        except (OSError, AttributeError):
            continue
        os.environ["PATH"] = directory + os.pathsep + os.environ.get("PATH", "")
        added.append(directory)
    telemetry.log("cuda.windows_dirs", dirs=added)
    return added


def _prepare_linux() -> List[str]:
    paths: List[str] = []
    for root in _site_dirs():
        for name in _LINUX_LIBS:
            paths.extend(sorted(glob.glob(os.path.join(root, "nvidia", name, "lib", "*.so*"))))
    loaded: List[str] = []
    pending = list(dict.fromkeys(paths))
    # Dependencies between the cuDNN sub-libraries are not declared in a
    # loadable order, so retry until a pass makes no progress.
    for _ in range(4):
        failed = []
        for path in pending:
            try:
                ctypes.CDLL(path, mode=ctypes.RTLD_GLOBAL)
                loaded.append(os.path.basename(path))
            except OSError:
                failed.append(path)
        if len(failed) == len(pending):
            break
        pending = failed
    telemetry.log("cuda.linux_preloaded", count=len(loaded), unresolved=len(pending))
    return loaded
