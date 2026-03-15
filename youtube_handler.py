"""youtube_handler.py.

Pipeline de transcripcion de YouTube en dos pasos:
1. Consultar YouTube Data API v3 para descubrir los idiomas de subtitulos.
2. Solicitar la transcripcion a RapidAPI usando el idioma seleccionado.
"""

import logging
import os
import re

import requests

logger = logging.getLogger(__name__)

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY")

LANGUAGE_PRIORITY = ["es", "en", "pt", "fr", "de"]
YOUTUBE_CAPTIONS_URL = "https://youtube.googleapis.com/youtube/v3/captions"
RAPIDAPI_HOST = "youtube-transcript3.p.rapidapi.com"
RAPIDAPI_URL = f"https://{RAPIDAPI_HOST}/api/transcript-with-url"


def extract_video_id(url: str):
    """Extrae el video ID de cualquier formato estandar de URL de YouTube."""
    if not url:
        return None

    patterns = [
        r"(?:https?://)?(?:www\.)?youtube\.com/watch\?(?:[^&]*&)*v=([^&\s]+)",
        r"(?:https?://)?(?:www\.)?youtu\.be/([^?\s]+)",
        r"(?:https?://)?(?:www\.)?youtube\.com/embed/([^?\s]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def _select_best_language(languages: dict):
    """Selecciona el mejor idioma disponible segun el orden de prioridad."""
    if not languages:
        return None

    available_codes = [code for code in languages.values() if code]
    for preferred in LANGUAGE_PRIORITY:
        if preferred in available_codes:
            return preferred

    for preferred in LANGUAGE_PRIORITY:
        for code in available_codes:
            if code.startswith(preferred):
                return code

    return available_codes[0] if available_codes else None


def select_transcript_language(languages: dict):
    """Expone la seleccion automatica de idioma para consumidores externos."""
    return _select_best_language(languages)


def _youtube_api_params(video_id: str):
    return {
        "part": "snippet",
        "videoId": video_id,
        "key": YOUTUBE_API_KEY,
    }


def _extract_youtube_languages(payload):
    languages = {}

    for item in payload.get("items", []):
        snippet = item.get("snippet") or {}
        language_code = snippet.get("language")
        if not isinstance(language_code, str) or not language_code.strip():
            continue

        display_name = snippet.get("name")
        if isinstance(display_name, dict):
            display_name = display_name.get("simpleText")

        if not isinstance(display_name, str) or not display_name.strip():
            display_name = language_code

        languages[display_name.strip()] = language_code.strip()

    return languages


def _rapidapi_headers():
    if not RAPIDAPI_KEY:
        return None
    return {
        "x-rapidapi-key": RAPIDAPI_KEY,
        "x-rapidapi-host": RAPIDAPI_HOST,
    }


def _extract_transcript_text(payload):
    if isinstance(payload, str):
        return payload.strip()

    if isinstance(payload, list):
        parts = []
        for item in payload:
            if isinstance(item, str):
                value = item.strip()
            elif isinstance(item, dict):
                value = (
                    item.get("text")
                    or item.get("subtitle")
                    or item.get("content")
                    or item.get("transcript")
                )
                value = value.strip() if isinstance(value, str) else ""
            else:
                value = ""

            if value:
                parts.append(value)
        return " ".join(parts).strip()

    if not isinstance(payload, dict):
        return ""

    for key in ("transcript", "text", "flatText", "flat_text", "content", "subtitle"):
        if key not in payload:
            continue

        value = payload.get(key)
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text

        if isinstance(value, (list, dict)):
            text = _extract_transcript_text(value)
            if text:
                return text

    for key in ("segments", "items", "captions", "subtitles", "lines"):
        if key in payload:
            text = _extract_transcript_text(payload[key])
            if text:
                return text

    return ""


def _fetch_rapidapi_payload(url: str, lang_code: str | None = None):
    headers = _rapidapi_headers()
    if headers is None:
        return None, "RAPIDAPI_KEY no esta configurada en las variables de entorno."

    params = {
        "url": url,
        "flat_text": "true",
    }
    if lang_code:
        params["lang"] = lang_code

    try:
        response = requests.get(RAPIDAPI_URL, headers=headers, params=params, timeout=20)
        response.raise_for_status()
        return response.json(), None
    except requests.exceptions.Timeout:
        return None, "Tiempo de espera agotado obteniendo la transcripcion. Intenta de nuevo."
    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        if status == 401:
            return None, "RapidAPI rechazo la autenticacion. Verifica RAPIDAPI_KEY."
        if status == 403:
            return None, "Acceso denegado por RapidAPI. Verifica RAPIDAPI_KEY y tu cuota."
        if status == 404:
            return None, "RapidAPI no encontro subtitulos para este video o idioma."
        if status == 429:
            return None, "RapidAPI alcanzo el limite de solicitudes. Intenta de nuevo mas tarde."
        return None, f"Error HTTP {status} al consultar RapidAPI."
    except requests.exceptions.RequestException as exc:
        logger.error("Error de red consultando RapidAPI: %s", exc)
        return None, "Error de red al obtener la transcripcion. Revisa tu conexion."
    except ValueError:
        logger.error("RapidAPI devolvio una respuesta no JSON para %s", url)
        return None, "RapidAPI devolvio una respuesta invalida."
    except Exception as exc:
        logger.error("Error inesperado consultando RapidAPI: %s", exc, exc_info=True)
        return None, f"Error inesperado al obtener la transcripcion: {str(exc)[:120]}"


def fetch_available_languages(video_id: str):
    """Consulta YouTube Data API v3 para descubrir subtitulos disponibles."""
    if not YOUTUBE_API_KEY:
        return {}, "YOUTUBE_API_KEY no esta configurada en las variables de entorno."

    try:
        response = requests.get(
            YOUTUBE_CAPTIONS_URL,
            params=_youtube_api_params(video_id),
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.Timeout:
        return {}, "Tiempo de espera agotado al consultar YouTube. Intenta de nuevo."
    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        if status == 400:
            return {}, "YouTube rechazo la solicitud de subtitulos para este video."
        if status == 403:
            return {}, "Acceso denegado a YouTube Data API. Verifica YOUTUBE_API_KEY y tu cuota."
        if status == 404:
            return {}, "No encontre el video en YouTube. Verifica la URL."
        return {}, f"Error HTTP {status} al consultar YouTube Data API."
    except requests.exceptions.RequestException as exc:
        logger.error("Error de red consultando YouTube Data API: %s", exc)
        return {}, "Error de red al consultar YouTube. Revisa tu conexion."
    except ValueError:
        logger.error("YouTube Data API devolvio una respuesta no JSON para %s", video_id)
        return {}, "YouTube Data API devolvio una respuesta invalida."
    except Exception as exc:
        logger.error("Error inesperado consultando YouTube Data API: %s", exc, exc_info=True)
        return {}, f"Error inesperado obteniendo idiomas: {str(exc)[:120]}"

    languages = _extract_youtube_languages(data)
    if not languages:
        return {}, "Este video no tiene subtitulos disponibles."

    logger.info(
        "Idiomas descubiertos para %s: %s",
        video_id,
        ", ".join(sorted(set(languages.values()))),
    )
    return languages, None


def fetch_transcript_by_lang(url: str, lang_code: str | None):
    """Obtiene la transcripcion en un idioma concreto o en el idioma por defecto."""
    payload, error = _fetch_rapidapi_payload(url, lang_code=lang_code)
    if payload is None:
        return None, error

    transcript = _extract_transcript_text(payload)
    if not transcript:
        if lang_code:
            return None, f"No encontre transcripcion util para el idioma {lang_code}."
        return None, "La transcripcion obtenida esta vacia."

    return transcript, None


def get_transcript(url: str, languages: dict | None = None):
    """Obtiene la transcripcion completa de un video de YouTube."""
    video_id = extract_video_id(url)
    if not video_id:
        return None, "No pude extraer el ID del video desde la URL proporcionada."

    discovery_error = None
    if languages is None:
        languages, discovery_error = fetch_available_languages(video_id)

    selected_lang = _select_best_language(languages)
    if not selected_lang:
        return None, discovery_error or "No pude determinar un idioma de subtitulos para el video."

    attempted = [selected_lang]

    available_codes = [code for code in languages.values() if code and code not in attempted]
    for preferred in LANGUAGE_PRIORITY:
        for code in available_codes:
            if code == preferred and code not in attempted:
                attempted.append(code)

    for preferred in LANGUAGE_PRIORITY:
        for code in available_codes:
            if code.startswith(preferred) and code not in attempted:
                attempted.append(code)

    for code in available_codes:
        if code not in attempted:
            attempted.append(code)

    attempted.append(None)
    last_error = discovery_error or "No pude obtener la transcripcion del video."

    for lang_code in attempted:
        transcript, error = fetch_transcript_by_lang(url, lang_code)
        if transcript:
            logger.info(
                "Transcripcion obtenida para %s | idioma: %s | %s chars",
                video_id,
                lang_code or "default",
                len(transcript),
            )
            return transcript, None

        if error:
            last_error = error
            logger.info(
                "Intento de transcripcion fallido para %s | idioma: %s | %s",
                video_id,
                lang_code or "default",
                error,
            )

    return None, last_error