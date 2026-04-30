"""Generated protobuf/gRPC bindings — produced by gen.sh in the repo root.

The generated *_pb2.py / *_pb2_grpc.py files use absolute imports rooted at
the proto include path (e.g. ``import entities.common.timestamp_pb2``).  We
append this directory to ``sys.path`` so those imports resolve. ``append``
(not ``insert``) keeps us from shadowing same-named packages elsewhere on
the path.
"""

import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
if _HERE not in _sys.path:
    _sys.path.append(_HERE)
