"""Local audio transcription service using mlx-whisper."""

import logging
import tempfile
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Available models for local transcription (mlx-community HuggingFace models)
WHISPER_MODELS = {
    "tiny": {
        "name": "mlx-community/whisper-tiny",
        "description": "Fastest, lowest accuracy",
        "speed": "40x realtime",
    },
    "base": {
        "name": "mlx-community/whisper-base",
        "description": "Fast, basic accuracy",
        "speed": "30x realtime",
    },
    "small": {
        "name": "mlx-community/whisper-small",
        "description": "Good accuracy",
        "speed": "20x realtime",
    },
    "medium": {
        "name": "mlx-community/whisper-medium",
        "description": "High accuracy",
        "speed": "10x realtime",
    },
    "large-v3": {
        "name": "mlx-community/whisper-large-v3",
        "description": "Highest accuracy",
        "speed": "5x realtime",
    },
    "large-v3-turbo": {
        "name": "mlx-community/whisper-large-v3-turbo",
        "description": "Best balance of speed/accuracy (recommended)",
        "speed": "15x realtime",
    },
}

DEFAULT_MODEL = "large-v3-turbo"


class LocalTranscriber:
    """
    Local audio transcription using mlx-whisper.

    Optimized for Apple Silicon (M1/M2/M3/M4).
    Uses Apple's MLX framework for fast inference.
    """

    def __init__(self, model: str = DEFAULT_MODEL):
        """
        Initialize the transcriber.

        Args:
            model: Whisper model to use (see WHISPER_MODELS)
        """
        self.model_name = model
        self._model_path = WHISPER_MODELS.get(model, {}).get("name", f"mlx-community/whisper-{model}")

    def transcribe_file(self, audio_path: str | Path) -> dict[str, Any]:
        """
        Transcribe an audio file.

        Args:
            audio_path: Path to audio file (mp3, wav, m4a, etc.)

        Returns:
            dict with 'text' key containing the transcript
        """
        import mlx_whisper

        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        logger.info(f"Transcribing with mlx-whisper ({self.model_name}): {audio_path.name}")

        result = mlx_whisper.transcribe(
            str(audio_path),
            path_or_hf_repo=self._model_path,
            verbose=False,
        )

        return {
            "text": result.get("text", ""),
            "model": self.model_name,
            "file": str(audio_path),
        }

    async def transcribe_url(self, audio_url: str) -> dict[str, Any]:
        """
        Download and transcribe audio from a URL.

        Args:
            audio_url: URL to audio file

        Returns:
            dict with 'text' key containing the transcript
        """
        # Download to temp file
        logger.info(f"Downloading audio from: {audio_url[:80]}...")

        async with httpx.AsyncClient() as client:
            response = await client.get(audio_url, timeout=300.0, follow_redirects=True)
            response.raise_for_status()

            # Determine extension from URL or content-type
            content_type = response.headers.get("content-type", "")
            if "audio/mpeg" in content_type or audio_url.endswith(".mp3"):
                ext = ".mp3"
            elif "audio/wav" in content_type or audio_url.endswith(".wav"):
                ext = ".wav"
            elif "audio/mp4" in content_type or audio_url.endswith(".m4a"):
                ext = ".m4a"
            else:
                ext = ".mp3"  # Default to mp3

            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                tmp.write(response.content)
                tmp_path = tmp.name

        try:
            result = self.transcribe_file(tmp_path)
            result["source_url"] = audio_url
            return result
        finally:
            # Clean up temp file
            Path(tmp_path).unlink(missing_ok=True)

    @staticmethod
    def available_models() -> list[dict]:
        """Get list of available Whisper models."""
        return [{"id": k, **v} for k, v in WHISPER_MODELS.items()]


# Singleton instance
_transcriber: LocalTranscriber | None = None


def get_transcriber(model: str = DEFAULT_MODEL) -> LocalTranscriber:
    """Get or create the transcriber instance."""
    global _transcriber
    if _transcriber is None or _transcriber.model_name != model:
        _transcriber = LocalTranscriber(model=model)
    return _transcriber


async def transcribe_podcast_episode(audio_url: str, model: str = DEFAULT_MODEL) -> str:
    """
    Convenience function to transcribe a podcast episode.

    Args:
        audio_url: URL to the podcast audio
        model: Whisper model to use

    Returns:
        Transcript text
    """
    transcriber = get_transcriber(model)
    result = await transcriber.transcribe_url(audio_url)
    return result.get("text", "")
