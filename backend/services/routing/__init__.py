"""Routing service package — haversine great-circle distance + stop ordering.

Framework-agnostic (no LangChain, no ``agent/``/``api/`` imports) per the project
layering rules, so it stays swappable. See ``routing_service.py`` for the seam
that a future Google Distance Matrix (road/rail travel-time) backend slots into.
"""
