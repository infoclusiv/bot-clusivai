import os
import logging
import requests

from brain import get_ai_configuration
from database import AI_TRANSCRIPT_CAPABILITY

logger = logging.getLogger(__name__)

GROQ_TRANSCRIPT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
MAX_AUDIO_SIZE_BYTES = 25 * 1024 * 1024


def get_transcript_api_key(provider):
    """Resuelve la API key del proveedor configurado para transcripción."""
    if provider == "groq":
        return os.getenv("GROQ_API_KEY")
    return None


def transcribe_audio_with_groq(audio_path, model_name, get_audio_mime_type, *, timeout=120):
    """Transcribe audio usando la API compatible de Groq."""
    api_key = get_transcript_api_key("groq")
    if not api_key:
        logger.error("GROQ_API_KEY no está configurado en las variables de entorno")
        return None, "Error de configuración: GROQ_API_KEY no está definido."

    try:
        file_size = os.path.getsize(audio_path)
        logger.info(
            "Transcribiendo audio con Groq: %s (%.1f KB), modelo=%s",
            audio_path,
            file_size / 1024,
            model_name,
        )

        if file_size > MAX_AUDIO_SIZE_BYTES:
            size_mb = file_size / (1024 * 1024)
            return None, f"El audio es demasiado grande ({size_mb:.1f} MB). El límite es 25 MB."

        with open(audio_path, 'rb') as audio_file:
            files = {
                'file': (
                    os.path.basename(audio_path),
                    audio_file,
                    get_audio_mime_type(audio_path),
                )
            }
            data = {
                'model': model_name,
                'response_format': 'json',
            }
            response = requests.post(
                GROQ_TRANSCRIPT_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                files=files,
                data=data,
                timeout=timeout,
            )

        if response.status_code == 413:
            return None, "El archivo de audio es demasiado grande para la API de transcripción."

        if response.status_code == 429:
            return None, "Se excedió el límite de la API de transcripción. Intenta de nuevo en unos minutos."

        if response.status_code != 200:
            logger.error("Groq API error: %s - %s", response.status_code, response.text[:300])
            return None, f"Error en la transcripción (código {response.status_code}). Intenta de nuevo."

        result = response.json()
        transcript = result.get('text', '').strip()
        if not transcript:
            return None, "La transcripción está vacía. El audio podría no contener voz clara."

        logger.info("Transcripción exitosa con Groq: %s caracteres", len(transcript))
        return transcript, None
    except requests.exceptions.Timeout:
        logger.error("Timeout al transcribir audio con Groq (%ss)", timeout)
        return None, "La transcripción tardó demasiado. Intenta con un video más corto."
    except requests.exceptions.ConnectionError:
        logger.error("Error de conexión con Groq API")
        return None, "Error de conexión con el servicio de transcripción."
    except Exception as exc:
        logger.error("Error inesperado transcribiendo audio: %s", exc, exc_info=True)
        return None, f"Error inesperado al transcribir: {str(exc)[:100]}"


def transcribe_audio_with_active_provider(audio_path, get_audio_mime_type, *, timeout=120):
    """Usa la configuración activa de la capacidad transcript para transcribir audio."""
    config = get_ai_configuration(AI_TRANSCRIPT_CAPABILITY)
    provider = config["provider"]
    model_name = config["model_name"]

    if provider == "groq":
        return transcribe_audio_with_groq(
            audio_path,
            model_name,
            get_audio_mime_type,
            timeout=timeout,
        )

    logger.error("Proveedor de transcripción no soportado: %s", provider)
    return None, f"Proveedor de transcripción no soportado: {provider}"
