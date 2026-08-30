"""
xau_algo/health/__init__.py
============================
Health monitoring package.

Exports
-------
  HealthState  — thread-safe in-memory health state
  HealthServer — FastAPI HTTP server (background daemon thread)
"""

from xau_algo.health.state import HealthState
from xau_algo.health.server import HealthServer

__all__ = ["HealthState", "HealthServer"]
