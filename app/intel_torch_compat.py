"""Compatibility shims for running modern Transformers with PyTorch 2.2 on macOS x86_64."""
from __future__ import annotations

import platform
import sys
from typing import Any

_APPLIED = False


def apply_intel_torch_compat() -> bool:
    """Apply minimal shims for PyTorch 2.2 / Transformers compatibility on Intel/x86_64."""
    global _APPLIED
    if _APPLIED:
        return True

    try:
        import torch
    except ImportError:
        return False

    # Only apply when torch version is < 2.4 / 2.5
    try:
        ver_parts = tuple(int(x) for x in torch.__version__.split("+")[0].split(".")[:2])
    except Exception:
        ver_parts = (2, 2)

    if ver_parts >= (2, 5):
        _APPLIED = True
        return True

    # 1. torch.compiler
    try:
        import torch.compiler
        if not hasattr(torch.compiler, "is_compiling"):
            torch.compiler.is_compiling = lambda: False
    except Exception:
        pass

    # 2. torch.library shims
    try:
        import torch.library
        if not hasattr(torch.library, "custom_op"):
            def _custom_op(*args: Any, **kwargs: Any):
                def decorator(fn: Any):
                    return fn
                return decorator
            torch.library.custom_op = _custom_op

        if not hasattr(torch.library, "register_fake"):
            def _register_fake(*args: Any, **kwargs: Any):
                def decorator(fn: Any):
                    return fn
                return decorator
            torch.library.register_fake = _register_fake

        if not hasattr(torch.library, "register_autograd"):
            def _register_autograd(*args: Any, **kwargs: Any):
                def decorator(fn: Any):
                    return fn
                return decorator
            torch.library.register_autograd = _register_autograd
    except Exception:
        pass

    # 3. uint aliases
    if not hasattr(torch, "uint16"):
        torch.uint16 = torch.int32
    if not hasattr(torch, "uint32"):
        torch.uint32 = torch.int64
    if not hasattr(torch, "uint64"):
        torch.uint64 = torch.int64

    # 4. get_default_device
    if not hasattr(torch, "get_default_device"):
        torch.get_default_device = lambda: torch.device("cpu")

    # 5. nn.Buffer
    try:
        import torch.nn as nn
        if not hasattr(nn, "Buffer"):
            class Buffer(torch.Tensor):
                def __new__(cls, data=None, requires_grad=False, persistent=True):
                    if data is None:
                        data = torch.empty(0)
                    if not isinstance(data, torch.Tensor):
                        data = torch.tensor(data)
                    t = torch.Tensor._make_subclass(cls, data, requires_grad)
                    t.persistent = persistent
                    return t

            nn.Buffer = Buffer
            _orig_nn_setattr = nn.Module.__setattr__

            def _module_setattr(self, name, value):
                if isinstance(value, Buffer):
                    self.register_buffer(name, value, persistent=getattr(value, "persistent", True))
                else:
                    _orig_nn_setattr(self, name, value)

            nn.Module.__setattr__ = _module_setattr
    except Exception:
        pass

    # 6. is_autocast_enabled with device_type argument
    try:
        _orig_is_autocast = torch.is_autocast_enabled
        def _compat_is_autocast(device_type: str | None = None) -> bool:
            if device_type == "cpu":
                return getattr(torch, "is_autocast_cpu_enabled", lambda: False)()
            return _orig_is_autocast()
        torch.is_autocast_enabled = _compat_is_autocast
    except Exception:
        pass

    # 7. torch.distributed.tensor
    try:
        import torch.distributed
        if not hasattr(torch.distributed, "tensor"):
            try:
                import torch.distributed._tensor as _dist_tensor
                torch.distributed.tensor = _dist_tensor
                sys.modules["torch.distributed.tensor"] = _dist_tensor
            except Exception:
                pass
    except Exception:
        pass

    # 8. transformers sdpa attention compat
    try:
        import transformers.integrations.sdpa_attention as sdpa
        sdpa.use_gqa_in_sdpa = lambda *args, **kwargs: False
    except Exception:
        pass

    # 9. transformers fsdp check compat (prevent importing unsupported torch.distributed.fsdp on PyTorch 2.2)
    try:
        import transformers.distributed.fsdp as fsdp
        fsdp.is_fsdp_managed_module = lambda module: False
    except Exception:
        pass

    _APPLIED = True
    return True


# Auto-apply on import if running under macOS x86_64
if sys.platform == "darwin" and platform.machine() == "x86_64":
    apply_intel_torch_compat()
