from typing import Literal

TTSLanguages = Literal[
    "auto", "en-US", "en-GB", "en-IN", "en-AU", "hi", "bn", "ta", "te", "mr", "gu", "kn"
]
"""Languages Koyal accepts. Each voice speaks exactly one of them; ``auto`` uses the voice's."""

TTSDefaultVoiceId = "6uWzc4xFj31t4jNJUaED"
"""Meera: female, Hindi, Indian accent. Handles Hinglish (Hindi mixed with English)."""
