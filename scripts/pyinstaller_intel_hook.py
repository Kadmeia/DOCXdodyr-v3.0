"""PyInstaller runtime hook for macOS x86_64 compatibility shims."""
import sys
import platform

if sys.platform == "darwin" and platform.machine() == "x86_64":
    try:
        import intel_torch_compat
        intel_torch_compat.apply_intel_torch_compat()
    except Exception:
        pass
