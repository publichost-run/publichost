"""Entry point: python -m publichost (delegates to the tunnel service)."""

from __future__ import annotations

from .tunnel.app import main

if __name__ == "__main__":
    main()
