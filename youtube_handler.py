"""
youtube_handler.py
Modulo para extraer la transcripcion de un video de YouTube
usando youtube-transcript-api sin necesidad de API keys externas.
"""

import logging
import re

logger = logging.getLogger(__name__)

LANGUAGE_PRIORITY = ["es", "en", "pt", "fr", "de"]


def extract_video_id(url: str):
    """Extrae el video ID de cualquier formato de URL de YouTube."""
    if not url:
        return None

    patterns = [
        r"(?:youtube\.com/watch\?(?:.*&)?v=)([\w-]+)",
        r"(?:youtu\.be/)([\w-]+)",
        r"(?:youtube\.com/embed/)([\w-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def get_transcript(url: str):
    """Obtiene la transcripcion completa de un video de YouTube de forma automatica."""
    try:
        from youtube_transcript_api import (
            NoTranscriptFound,
            TranscriptsDisabled,
            YouTubeTranscriptApi,
        )
    except ImportError:
        logger.error(
            "youtube-transcript-api no esta instalado. Ejecuta: pip install youtube-transcript-api"
        )
        return None, "Error interno: youtube-transcript-api no esta instalado."

    video_id = extract_video_id(url)
    if not video_id:
        return None, "No pude extraer el ID del video desde la URL proporcionada."

    try:
        transcript_api = YouTubeTranscriptApi()
        transcript_list = transcript_api.list(video_id)
        transcript = None

        try:
            transcript = transcript_list.find_manually_created_transcript(LANGUAGE_PRIORITY)
            logger.info(
                "Subtitulos manuales encontrados para %s: %s",
                video_id,
                transcript.language_code,
            )
        except NoTranscriptFound:
            pass

        if transcript is None:
            try:
                transcript = transcript_list.find_generated_transcript(LANGUAGE_PRIORITY)
                logger.info(
                    "Subtitulos automaticos encontrados para %s: %s",
                    video_id,
                    transcript.language_code,
                )
            except NoTranscriptFound:
                pass

        if transcript is None:
            available = list(transcript_list)
            if not available:
                return None, "Este video no tiene subtitulos disponibles en ningun idioma."
            transcript = available[0]
            logger.info(
                "Usando primer subtitulo disponible para %s: %s",
                video_id,
                transcript.language_code,
            )

        entries = transcript.fetch()
        full_text = " ".join(entry["text"] for entry in entries).strip()

        if not full_text:
            return None, "La transcripcion obtenida esta vacia."

        logger.info(
            "Transcripcion obtenida para %s | idioma: %s | %s chars",
            video_id,
            transcript.language_code,
            len(full_text),
        )
        return full_text, None

    except TranscriptsDisabled:
        return None, "Este video tiene los subtitulos desactivados por el creador."
    except Exception as e:
        logger.error("Error obteniendo transcripcion de %s: %s", video_id, e, exc_info=True)
        return None, f"No pude obtener la transcripcion del video: {str(e)[:150]}"