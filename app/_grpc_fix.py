"""
Startup patch for grpc DLL load failure on Windows.

Windows Application Control policies can block grpc's native cygrpc DLL.
ChromaDB imports grpc transitively through opentelemetry's OTLP gRPC exporter.
Since we only use ChromaDB locally (not via gRPC), we can safely stub grpc
so the import chain doesn't fail.

This module MUST be imported before any chromadb import.
"""
import sys
import types
import logging
import warnings

logger = logging.getLogger(__name__)


def _try_real_grpc() -> bool:
    """Attempt to import grpc normally. Returns True if it works."""
    try:
        from grpc._cython import cygrpc  # noqa: F401  — the actual DLL load
        return True
    except (ImportError, OSError):
        return False


def _install_grpc_stub():
    """Install a comprehensive grpc stub so opentelemetry's import chain succeeds."""
    logger.warning(
        "grpc native DLL unavailable (blocked by Windows policy or missing). "
        "Installing stub — ChromaDB local persistence is unaffected."
    )

    # Suppress version mismatch warnings from generated pb2_grpc files
    warnings.filterwarnings("ignore", message=".*grpc package installed.*", category=RuntimeWarning)

    # --------------- StatusCode enum ---------------
    class StatusCode:
        OK = 0
        CANCELLED = 1
        UNKNOWN = 2
        INVALID_ARGUMENT = 3
        DEADLINE_EXCEEDED = 4
        NOT_FOUND = 5
        ALREADY_EXISTS = 6
        PERMISSION_DENIED = 7
        RESOURCE_EXHAUSTED = 8
        FAILED_PRECONDITION = 9
        ABORTED = 10
        OUT_OF_RANGE = 11
        UNIMPLEMENTED = 12
        INTERNAL = 13
        UNAVAILABLE = 14
        DATA_LOSS = 15
        UNAUTHENTICATED = 16

    # --------------- Compression enum ---------------
    class Compression:
        NoCompression = 0
        Deflate = 1
        Gzip = 2

    # --------------- Exception types ---------------
    class RpcError(Exception):
        pass

    # --------------- Credential stubs ---------------
    class ChannelCredentials:
        pass

    class CallCredentials:
        pass

    class AuthMetadataPlugin:
        pass

    class Channel:
        pass

    # --------------- No-op helpers ---------------
    def _noop(*a, **kw):
        return None

    class _NoopChannel:
        def close(self): pass
        def unary_unary(self, *a, **kw): return _noop
        def unary_stream(self, *a, **kw): return _noop
        def stream_unary(self, *a, **kw): return _noop
        def stream_stream(self, *a, **kw): return _noop

    # --------------- Build module ---------------
    grpc_mod = types.ModuleType("grpc")
    grpc_mod.__version__ = "1.63.2"  # satisfies pb2_grpc version checks
    grpc_mod.__file__ = __file__

    grpc_mod.StatusCode = StatusCode
    grpc_mod.Compression = Compression
    grpc_mod.RpcError = RpcError
    grpc_mod.ChannelCredentials = ChannelCredentials
    grpc_mod.CallCredentials = CallCredentials
    grpc_mod.AuthMetadataPlugin = AuthMetadataPlugin
    grpc_mod.Channel = Channel
    grpc_mod.UnaryUnaryMultiCallable = type("UnaryUnaryMultiCallable", (), {})
    grpc_mod.UnaryStreamMultiCallable = type("UnaryStreamMultiCallable", (), {})
    grpc_mod.StreamUnaryMultiCallable = type("StreamUnaryMultiCallable", (), {})
    grpc_mod.StreamStreamMultiCallable = type("StreamStreamMultiCallable", (), {})

    for fn_name in (
        "ssl_channel_credentials", "insecure_channel", "secure_channel",
        "channel_ready_future", "metadata_call_credentials",
        "composite_channel_credentials", "access_token_call_credentials",
    ):
        setattr(grpc_mod, fn_name, _noop)

    sys.modules["grpc"] = grpc_mod

    # --------------- Sub-modules ---------------
    _sub_names = [
        "grpc._compression",
        "grpc._cython",
        "grpc._cython.cygrpc",
        "grpc.experimental",
        "grpc.experimental.aio",
        "grpc.aio",
    ]
    for name in _sub_names:
        mod = types.ModuleType(name)
        mod.__file__ = __file__
        sys.modules[name] = mod


def patch_grpc_if_needed():
    """Apply the grpc stub only if the real grpc can't load."""
    if "grpc" in sys.modules:
        return  # Already loaded (real or stub)
    if not _try_real_grpc():
        _install_grpc_stub()


# Auto-apply on import
patch_grpc_if_needed()
