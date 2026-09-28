"""tests/conftest.py — FIN-C2-025 test bootstrap (released AgentCore SDK v1.0.0).

The L1 framework (``framework.*`` / ``shared.*``) is provided by the installed
``agenticstar-agentcore`` wheel — there is no vendored stub package and no sys.modules stubbing
(migrated from the interim stub model, SDK v1.0.0).

This only ensures the repo root is importable so ``import src...`` works when running
``pytest tests/`` without an editable install.
"""

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
