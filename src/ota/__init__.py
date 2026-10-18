"""Optical Traffic Alert (OTA).

Camera-based collision warning for light aircraft. The processing chain is:

    source -> candidate detector -> crop classifier -> tracker -> threat assessment -> announcer

Every stage is a small, replaceable component so the same pipeline runs on a
Windows laptop with a video file and later on a Jetson with GMSL2 cameras.

This is NOT a certified device. It is an experimental aid and never a
replacement for see-and-avoid.
"""

__version__ = "0.3.0"
