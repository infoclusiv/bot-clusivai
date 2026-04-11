import os
import requests
import json
import pytz
import logging
import re
import base64
import random
import threading
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv
from database import AI_TEXT_CAPABILITY, AI_VISION_CAPABILITY, get_ai_setting

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
MODEL_NAME = os.getenv("MODEL_NAME")
VISION_MODEL_NAME = os.getenv("VISION_MODEL_NAME")
DEFAULT_TEXT_PROVIDER = os.getenv("DEFAULT_TEXT_PROVIDER", "openrouter")
DEFAULT_VISION_PROVIDER = os.getenv("DEFAULT_VISION_PROVIDER", "openrouter")
DEFAULT_TEXT_MODEL = MODEL_NAME or "stepfun/step-3.5-flash:free"
DEFAULT_VISION_MODEL = VISION_MODEL_NAME or "nvidia/nemotron-nano-12b-v2-vl:free"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
TRANSIENT_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
TRANSIENT_FAILURE_KINDS = {"http_status_error", "timeout", "network_error"}
OPENROUTER_MAX_ATTEMPTS = int(os.getenv("OPENROUTER_MAX_ATTEMPTS", "3"))
OPENROUTER_RETRY_BASE_SECONDS = float(os.getenv("OPENROUTER_RETRY_BASE_SECONDS", "1.0"))
OPENROUTER_TEXT_TIMEOUT = int(os.getenv("OPENROUTER_TEXT_TIMEOUT", "60"))
OPENROUTER_VISION_TIMEOUT = int(os.getenv("OPENROUTER_VISION_TIMEOUT", "90"))
OPENROUTER_VIDEO_TIMEOUT = int(os.getenv("OPENROUTER_VIDEO_TIMEOUT", "90"))
OPENROUTER_REPO_TIMEOUT = int(os.getenv("OPENROUTER_REPO_TIMEOUT", "120"))
REPO_HISTORY_MESSAGES = int(os.getenv("REPO_HISTORY_MESSAGES", "2"))
REPO_CHUNK_TREE_CHARS = int(os.getenv("REPO_CHUNK_TREE_CHARS", "2500"))
REPO_SYNTHESIS_TREE_CHARS = int(os.getenv("REPO_SYNTHESIS_TREE_CHARS", "4000"))
REPO_PARTIAL_MAX_TOKENS = int(os.getenv("REPO_PARTIAL_MAX_TOKENS", "450"))
REPO_SYNTHESIS_MAX_TOKENS = int(os.getenv("REPO_SYNTHESIS_MAX_TOKENS", "800"))

# Configurar logging para este módulo
logger = logging.getLogger(__name__)
_last_brain_failure = threading.local()


def clear_last_brain_failure():
    _last_brain_failure.data = None


def get_last_brain_failure():
    return getattr(_last_brain_failure, "data", None)


def is_transient_brain_failure(failure=None):
    failure = failure or get_last_brain_failure()
    return bool(failure and (failure.get("transient") or failure.get("kind") in TRANSIENT_FAILURE_KINDS))


def _serialize_preview(value, limit=500):
    if value is None:
        return ""

    if isinstance(value, str):
        return value[:limit]

    try:
        return json.dumps(value, ensure_ascii=False, default=str)[:limit]
    except (TypeError, ValueError):
        return str(value)[:limit]


def _build_retry_delay(attempt):
    base_delay = max(0.1, OPENROUTER_RETRY_BASE_SECONDS)
    return min(base_delay * (2 ** max(0, attempt - 1)) + random.uniform(0, 0.25), 8.0)


def _record_brain_failure(kind, context_label, **details):
    failure = {
        "timestamp": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "kind": kind,
        "context": context_label,
        **details,
    }
    _last_brain_failure.data = failure
    logger.error(
        "Diagnóstico brain failure (%s): %s",
        context_label,
        json.dumps(failure, ensure_ascii=False, default=str),
    )
    return failure


def _coerce_provider_name(provider_name, fallback="openrouter"):
    normalized = str(provider_name or fallback).strip().lower()
    if normalized in {"openrouter", "nvidia"}:
        return normalized

    logger.warning(
        "Proveedor de IA no soportado en configuración (%s); usando %s",
        provider_name,
        fallback,
    )
    return fallback


def get_default_ai_settings():
    return {
        AI_TEXT_CAPABILITY: {
            "capability": AI_TEXT_CAPABILITY,
            "provider": _coerce_provider_name(DEFAULT_TEXT_PROVIDER, fallback="openrouter"),
            "model_name": os.getenv("MODEL_NAME") or DEFAULT_TEXT_MODEL,
            "source": "default",
        },
        AI_VISION_CAPABILITY: {
            "capability": AI_VISION_CAPABILITY,
            "provider": _coerce_provider_name(DEFAULT_VISION_PROVIDER, fallback="openrouter"),
            "model_name": os.getenv("VISION_MODEL_NAME") or DEFAULT_VISION_MODEL,
            "source": "default",
        },
    }


def get_ai_configuration(capability):
    normalized_capability = str(capability).strip().lower()
    default_config = get_default_ai_settings().get(normalized_capability)
    if default_config is None:
        raise ValueError(f"Capacidad de IA no soportada: {capability}")

    stored_config = get_ai_setting(normalized_capability)
    if stored_config and stored_config.get("provider") and stored_config.get("model_name"):
        return {
            "capability": normalized_capability,
            "provider": stored_config["provider"],
            "model_name": stored_config["model_name"],
            "updated_at": stored_config.get("updated_at"),
            "source": "database",
        }

    return dict(default_config)


def get_all_ai_configurations():
    return {
        AI_TEXT_CAPABILITY: get_ai_configuration(AI_TEXT_CAPABILITY),
        AI_VISION_CAPABILITY: get_ai_configuration(AI_VISION_CAPABILITY),
    }


def get_provider_api_key(provider):
    normalized_provider = _coerce_provider_name(provider)
    if normalized_provider == "nvidia":
        return os.getenv("NVIDIA_API_KEY") or NVIDIA_API_KEY
    return os.getenv("OPENROUTER_API_KEY") or OPENROUTER_API_KEY


def get_text_model():
    return get_ai_configuration(AI_TEXT_CAPABILITY)["model_name"]


def get_text_provider():
    return get_ai_configuration(AI_TEXT_CAPABILITY)["provider"]


def get_vision_model():
    return get_ai_configuration(AI_VISION_CAPABILITY)["model_name"]


def get_vision_provider():
    return get_ai_configuration(AI_VISION_CAPABILITY)["provider"]


def build_openrouter_headers(api_key):
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def clean_model_response_text(text):
    if not isinstance(text, str):
        return ""

    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def extract_json_candidates_from_text(text):
    candidates = []
    start_index = None
    depth = 0
    in_string = False
    escape = False

    for index, char in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
            continue

        if char == '{':
            if depth == 0:
                start_index = index
            depth += 1
        elif char == '}' and depth > 0:
            depth -= 1
            if depth == 0 and start_index is not None:
                candidates.append(text[start_index:index + 1])
                start_index = None

    return candidates


def parse_structured_response(content, *, context_label):
    cleaned_content = clean_model_response_text(content)
    if not cleaned_content:
        logger.error("Respuesta vacía del modelo (%s)", context_label)
        _record_brain_failure(
            "empty_model_response",
            context_label,
            response_excerpt="",
            transient=False,
        )
        return None

    parse_attempts = [cleaned_content, *extract_json_candidates_from_text(cleaned_content)]
    seen_attempts = set()

    for candidate in parse_attempts:
        if candidate in seen_attempts:
            continue
        seen_attempts.add(candidate)

        try:
            parsed_result = json.loads(candidate)
        except json.JSONDecodeError:
            continue

        if not isinstance(parsed_result, dict):
            logger.warning(
                "Respuesta JSON con tipo inválido (%s): %s",
                context_label,
                type(parsed_result).__name__,
            )
            continue

        if parsed_result.get('id') is not None:
            try:
                parsed_result['id'] = int(parsed_result['id'])
            except (ValueError, TypeError):
                logger.warning("ID no es un número válido (%s): %s", context_label, parsed_result['id'])

        logger.info(
            "JSON parseado exitosamente (%s): %s",
            context_label,
            parsed_result.get('action', 'UNKNOWN'),
        )
        return parsed_result

    logger.error(
        "No se pudo parsear JSON (%s). Contenido: %s...",
        context_label,
        cleaned_content[:300],
    )
    _record_brain_failure(
        "invalid_json_response",
        context_label,
        response_excerpt=cleaned_content[:300],
        transient=False,
    )
    return None


def extract_message_content_text(content):
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue

            if isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item.get("content"), str):
                    parts.append(item["content"])

        return "".join(parts).strip()

    if content is None:
        return ""

    return str(content).strip()


def extract_response_text(response_data, *, log_context, ai_config):
    if 'choices' not in response_data or not response_data['choices']:
        logger.error("Respuesta inválida de API (%s): %s", log_context, response_data)
        _record_brain_failure(
            "invalid_provider_payload",
            log_context,
            provider=ai_config.get("provider"),
            capability=ai_config.get("capability"),
            model=ai_config.get("model_name"),
            response_excerpt=_serialize_preview(response_data),
            transient=False,
        )
        return None

    choice = response_data['choices'][0] or {}
    message = choice.get('message') or {}
    content = extract_message_content_text(message.get('content'))
    if content:
        return content

    delta = choice.get('delta') or {}
    content = extract_message_content_text(delta.get('content'))
    if content:
        return content

    logger.error("Respuesta vacía del proveedor (%s)", log_context)
    _record_brain_failure(
        "empty_model_response",
        log_context,
        provider=ai_config.get("provider"),
        capability=ai_config.get("capability"),
        model=ai_config.get("model_name"),
        response_excerpt=_serialize_preview(response_data),
        transient=False,
    )
    return None


def post_openrouter_chat(data, *, timeout, log_context, ai_config=None, max_attempts=None):
    ai_config = ai_config or get_ai_configuration(AI_TEXT_CAPABILITY)
    provider = "openrouter"
    model_name = data.get("model") or ai_config.get("model_name")
    api_key = get_provider_api_key(provider)
    if not api_key:
        logger.error("ERROR: OPENROUTER_API_KEY no está configurado")
        _record_brain_failure(
            "missing_api_key",
            log_context,
            provider=provider,
            capability=ai_config.get("capability"),
            model=model_name,
            transient=False,
        )
        return None

    max_attempts = max_attempts or OPENROUTER_MAX_ATTEMPTS
    headers = build_openrouter_headers(api_key)

    for attempt in range(1, max_attempts + 1):
        started_at = time.monotonic()
        try:
            logger.info(
                "Enviando request a %s (%s), intento %s/%s, capacidad=%s, modelo=%s",
                provider,
                log_context,
                attempt,
                max_attempts,
                ai_config.get('capability'),
                model_name,
            )
            response = requests.post(
                OPENROUTER_URL,
                headers=headers,
                json=data,
                timeout=timeout,
            )
            elapsed_ms = int((time.monotonic() - started_at) * 1000)

            logger.info(
                "Respuesta de %s (%s), intento %s/%s, capacidad=%s, modelo=%s, status=%s, duracion_ms=%s",
                provider,
                log_context,
                attempt,
                max_attempts,
                ai_config.get('capability'),
                model_name,
                response.status_code,
                elapsed_ms,
            )

            if response.status_code != 200:
                body_preview = response.text[:500]
                if response.status_code in TRANSIENT_STATUS_CODES and attempt < max_attempts:
                    sleep_seconds = _build_retry_delay(attempt)
                    logger.warning(
                        "%s devolvió status %s (%s), reintentando en %.2fs. Respuesta: %s",
                        provider,
                        response.status_code,
                        log_context,
                        sleep_seconds,
                        body_preview,
                    )
                    time.sleep(sleep_seconds)
                    continue

                logger.error(
                    "Error en API %s (%s): Status %s, Response: %s",
                    provider,
                    log_context,
                    response.status_code,
                    body_preview,
                )
                _record_brain_failure(
                    "http_status_error",
                    log_context,
                    provider=provider,
                    capability=ai_config.get("capability"),
                    model=model_name,
                    status_code=response.status_code,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    timeout_seconds=timeout,
                    duration_ms=elapsed_ms,
                    response_excerpt=body_preview,
                    transient=response.status_code in TRANSIENT_STATUS_CODES,
                )
                return None

            try:
                return response.json()
            except ValueError:
                logger.error(
                    "%s devolvió una respuesta no JSON (%s): %s",
                    provider,
                    log_context,
                    response.text[:500],
                )
                _record_brain_failure(
                    "invalid_provider_payload",
                    log_context,
                    provider=provider,
                    capability=ai_config.get("capability"),
                    model=model_name,
                    status_code=response.status_code,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    timeout_seconds=timeout,
                    duration_ms=elapsed_ms,
                    response_excerpt=response.text[:500],
                    transient=False,
                )
                return None

        except requests.exceptions.Timeout:
            if attempt < max_attempts:
                sleep_seconds = _build_retry_delay(attempt)
                logger.warning(
                    "Timeout al conectar con %s (%ss) (%s), reintentando en %.2fs",
                    provider,
                    timeout,
                    log_context,
                    sleep_seconds,
                )
                time.sleep(sleep_seconds)
                continue

            logger.error("Timeout al conectar con %s (%ss) (%s)", provider, timeout, log_context)
            _record_brain_failure(
                "timeout",
                log_context,
                provider=provider,
                capability=ai_config.get("capability"),
                model=model_name,
                attempt=attempt,
                max_attempts=max_attempts,
                timeout_seconds=timeout,
                transient=True,
            )
            return None
        except requests.exceptions.RequestException as exc:
            if attempt < max_attempts:
                sleep_seconds = _build_retry_delay(attempt)
                logger.warning(
                    "Error de conexión con %s (%s), reintentando en %.2fs: %s",
                    provider,
                    log_context,
                    sleep_seconds,
                    exc,
                )
                time.sleep(sleep_seconds)
                continue

            logger.error("Error de conexión con %s (%s): %s", provider, log_context, exc)
            _record_brain_failure(
                "network_error",
                log_context,
                provider=provider,
                capability=ai_config.get("capability"),
                model=model_name,
                attempt=attempt,
                max_attempts=max_attempts,
                timeout_seconds=timeout,
                exception_type=type(exc).__name__,
                exception_message=str(exc),
                transient=True,
            )
            return None
        except Exception as exc:
            logger.error(
                "Error inesperado al invocar %s (%s): %s",
                provider,
                log_context,
                exc,
                exc_info=True,
            )
            _record_brain_failure(
                "unexpected_exception",
                log_context,
                provider=provider,
                capability=ai_config.get("capability"),
                model=model_name,
                attempt=attempt,
                max_attempts=max_attempts,
                timeout_seconds=timeout,
                exception_type=type(exc).__name__,
                exception_message=str(exc),
                transient=False,
            )
            return None


def post_nvidia_chat(data, *, timeout, log_context, ai_config=None, max_attempts=None):
    ai_config = ai_config or get_ai_configuration(AI_TEXT_CAPABILITY)
    provider = "nvidia"
    model_name = data.get("model") or ai_config.get("model_name")
    api_key = get_provider_api_key(provider)
    if not api_key:
        logger.error("ERROR: NVIDIA_API_KEY no está configurado")
        _record_brain_failure(
            "missing_api_key",
            log_context,
            provider=provider,
            capability=ai_config.get("capability"),
            model=model_name,
            transient=False,
        )
        return None

    try:
        from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
    except ImportError as exc:
        logger.error("La dependencia openai no está instalada para el proveedor Nvidia")
        _record_brain_failure(
            "missing_dependency",
            log_context,
            provider=provider,
            capability=ai_config.get("capability"),
            model=model_name,
            dependency="openai",
            exception_message=str(exc),
            transient=False,
        )
        return None

    max_attempts = max_attempts or OPENROUTER_MAX_ATTEMPTS

    for attempt in range(1, max_attempts + 1):
        started_at = time.monotonic()
        try:
            logger.info(
                "Enviando request a %s (%s), intento %s/%s, capacidad=%s, modelo=%s",
                provider,
                log_context,
                attempt,
                max_attempts,
                ai_config.get('capability'),
                model_name,
            )
            client = OpenAI(
                base_url=NVIDIA_BASE_URL,
                api_key=api_key,
                timeout=timeout,
            )
            response = client.chat.completions.create(**data)
            elapsed_ms = int((time.monotonic() - started_at) * 1000)

            logger.info(
                "Respuesta de %s (%s), intento %s/%s, capacidad=%s, modelo=%s, duracion_ms=%s",
                provider,
                log_context,
                attempt,
                max_attempts,
                ai_config.get('capability'),
                model_name,
                elapsed_ms,
            )

            if hasattr(response, 'model_dump'):
                return response.model_dump(exclude_none=True)
            if isinstance(response, dict):
                return response

            return json.loads(response.json())

        except APITimeoutError:
            if attempt < max_attempts:
                sleep_seconds = _build_retry_delay(attempt)
                logger.warning(
                    "Timeout al conectar con %s (%ss) (%s), reintentando en %.2fs",
                    provider,
                    timeout,
                    log_context,
                    sleep_seconds,
                )
                time.sleep(sleep_seconds)
                continue

            logger.error("Timeout al conectar con %s (%ss) (%s)", provider, timeout, log_context)
            _record_brain_failure(
                "timeout",
                log_context,
                provider=provider,
                capability=ai_config.get("capability"),
                model=model_name,
                attempt=attempt,
                max_attempts=max_attempts,
                timeout_seconds=timeout,
                transient=True,
            )
            return None
        except APIConnectionError as exc:
            if attempt < max_attempts:
                sleep_seconds = _build_retry_delay(attempt)
                logger.warning(
                    "Error de conexión con %s (%s), reintentando en %.2fs: %s",
                    provider,
                    log_context,
                    sleep_seconds,
                    exc,
                )
                time.sleep(sleep_seconds)
                continue

            logger.error("Error de conexión con %s (%s): %s", provider, log_context, exc)
            _record_brain_failure(
                "network_error",
                log_context,
                provider=provider,
                capability=ai_config.get("capability"),
                model=model_name,
                attempt=attempt,
                max_attempts=max_attempts,
                timeout_seconds=timeout,
                exception_type=type(exc).__name__,
                exception_message=str(exc),
                transient=True,
            )
            return None
        except APIStatusError as exc:
            status_code = getattr(exc, 'status_code', None)
            body_preview = str(exc)[:500]
            elapsed_ms = int((time.monotonic() - started_at) * 1000)

            if status_code in TRANSIENT_STATUS_CODES and attempt < max_attempts:
                sleep_seconds = _build_retry_delay(attempt)
                logger.warning(
                    "%s devolvió status %s (%s), reintentando en %.2fs. Respuesta: %s",
                    provider,
                    status_code,
                    log_context,
                    sleep_seconds,
                    body_preview,
                )
                time.sleep(sleep_seconds)
                continue

            logger.error(
                "Error en API %s (%s): Status %s, Response: %s",
                provider,
                log_context,
                status_code,
                body_preview,
            )
            _record_brain_failure(
                "http_status_error",
                log_context,
                provider=provider,
                capability=ai_config.get("capability"),
                model=model_name,
                status_code=status_code,
                attempt=attempt,
                max_attempts=max_attempts,
                timeout_seconds=timeout,
                duration_ms=elapsed_ms,
                response_excerpt=body_preview,
                transient=status_code in TRANSIENT_STATUS_CODES,
            )
            return None
        except Exception as exc:
            logger.error(
                "Error inesperado al invocar %s (%s): %s",
                provider,
                log_context,
                exc,
                exc_info=True,
            )
            _record_brain_failure(
                "unexpected_exception",
                log_context,
                provider=provider,
                capability=ai_config.get("capability"),
                model=model_name,
                attempt=attempt,
                max_attempts=max_attempts,
                timeout_seconds=timeout,
                exception_type=type(exc).__name__,
                exception_message=str(exc),
                transient=False,
            )
            return None


def post_ai_chat(data, *, timeout, log_context, ai_config, max_attempts=None):
    provider = _coerce_provider_name(ai_config.get("provider"))
    if provider == "nvidia":
        return post_nvidia_chat(
            data,
            timeout=timeout,
            log_context=log_context,
            ai_config=ai_config,
            max_attempts=max_attempts,
        )

    return post_openrouter_chat(
        data,
        timeout=timeout,
        log_context=log_context,
        ai_config=ai_config,
        max_attempts=max_attempts,
    )

def extract_json_from_text(text):
    """
    Extrae JSON válido de texto que puede contener markdown o texto adicional.
    Busca el primer '{' y el último '}' para extraer el objeto JSON.
    """
    try:
        for json_str in extract_json_candidates_from_text(text):
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                continue

        start = text.find('{')
        end = text.rfind('}')

        if start == -1 or end == -1 or end <= start:
            return None

        json_str = text[start:end+1]
        return json.loads(json_str)
    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"Error extrayendo JSON: {e} de texto: {text[:100]}...")
        return None


def request_ai_text(messages, timeout=OPENROUTER_TEXT_TIMEOUT, max_tokens=None, log_context=None, capability=AI_TEXT_CAPABILITY):
    """Hace una llamada de texto al proveedor activo y retorna texto plano."""
    clear_last_brain_failure()
    ai_config = get_ai_configuration(capability)
    data = {
        "model": ai_config["model_name"],
        "messages": messages,
    }
    if max_tokens is not None:
        data["max_tokens"] = max_tokens

    resolved_log_context = log_context or f"texto/{len(messages)}_mensajes"
    response_data = post_ai_chat(
        data,
        timeout=timeout,
        log_context=resolved_log_context,
        ai_config=ai_config,
    )
    if not response_data:
        return None

    content = extract_response_text(
        response_data,
        log_context=resolved_log_context,
        ai_config=ai_config,
    )
    if content is None:
        return None

    logger.info(
        "Respuesta de %s generada: %s caracteres",
        ai_config["provider"],
        len(content),
    )
    return content

def process_user_input(text, history=None, active_reminders=None):
    clear_last_brain_failure()

    # Obtener hora actual de Bogotá
    tz_bogota = pytz.timezone('America/Bogota')
    now = datetime.now(tz_bogota)
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    
    # Generar Mini-Calendario (Hoy + 14 días)
    # Esto ayuda a la IA a aterrizar "el sábado 21" a una fecha real sin calcular
    mini_calendar = []
    dias_espanol = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
    
    for i in range(15):
        future_date = now + timedelta(days=i)
        day_name = dias_espanol[future_date.weekday()]
        date_iso = future_date.strftime("%Y-%m-%d")
        mini_calendar.append(f"- {day_name} {date_iso}")
    
    mini_calendar_str = "\n".join(mini_calendar)
    
    # Preparar contexto de recordatorios activos
    reminders_context = ""
    if active_reminders:
        reminders_context = "\nRECORDATORIOS ACTIVOS ACTUALES DEL USUARIO (Fuente de Verdad):\n"
        for r in active_reminders:
            recur_info = f" [Recurrente: {r[3]}]" if len(r) > 3 and r[3] else ""
            reminders_context += f"- ID {r[0]}: \"{r[1]}\" para el {r[2]}{recur_info}\n"
    else:
        reminders_context = "\nEl usuario no tiene recordatorios activos actualmente.\n"

    system_prompt = f"""
    Eres 'Clusivai', un asistente personal inteligente. 
    CONTEXTO CALENDARIO (ÚSALO COMO VERDAD ABSOLUTA PARA FECHAS):
    Hora actual en Bogotá: {now_str}
    
    PRÓXIMOS DÍAS (Mini-Calendario):
    {mini_calendar_str}
    
    - Si el usuario dice "el sábado 21", BUSCA en la lista de arriba qué día dice "Sábado ...-21" y usa ESA fecha exacta.
    - Si el usuario dice "mañana", toma la fecha del segundo renglón del calendario.
    - Si el usuario dice "el próximo viernes", busca el primer Viernes que aparezca en la lista (o el segundo si hoy es viernes y se refiere al siguiente).
    - TIENES ACCESO AL HISTORIAL DE CONVERSACIÓN. Úsalo para entender el contexto y resolver ambigüedades.
    
    {reminders_context}
    
    REGLA DE ORO SOBRE FECHAS:
    - NO calcules fechas mentalmente si puedes buscarlas en el Mini-Calendario.
    - Si el usuario menciona un día de la semana y un número (ej: "Lunes 4"), VERIFICA en el calendario que coincidan. Si en el calendario el día 4 es Martes, CORRIGE o usa la fecha del calendario que tenga sentido (prioriza el número si es específico).
    
    REGLA DE ORO SOBRE IDs:
    - Cuando el usuario quiera ACTUALIZAR, BORRAR o PREGUNTAR por un recordatorio, utiliza EXCLUSIVAMENTE los IDs listados arriba en 'RECORDATORIOS ACTIVOS ACTUALES'.
    - Si el usuario menciona un ID que NO está en la lista superior, dile amablemente que ese ID no existe y muéstrale los IDs que sí tiene disponibles.
    - No alucines IDs. Si la lista superior está vacía, el usuario no tiene nada que modificar o borrar.
    
    Debes responder ÚNICAMENTE con un objeto JSON con esta estructura:
    {{
        "action": "CREATE" | "LIST" | "DELETE" | "UPDATE" | "CHAT" | "SET_SETTING" | "CONSULTAR_NOTAS",
        "id": número de ID (solo para UPDATE y SET_SETTING si aplica),
        "setting_name": "nombre del ajuste (solo para SET_SETTING, ej: 'daily_summary')",
        "value": valor del ajuste (ej: true, false, o una hora '07:45:00'),
        "message": "descripción de la tarea (solo para recordatorios)",
        "date": "YYYY-MM-DD HH:MM:SS" (fecha calculada para CREATE o UPDATE),
        "recurrence": "cadena RRULE (solo si es recurrente, ej: FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR) o null",
        "reply": "Tu respuesta directa si la acción es CHAT o confirmación de acción"
    }}

    Reglas:
    - Si el usuario pide activar o desactivar el resumen diario (ej: "activa el resumen diario", "no quiero más el listado matutino"), usa action: "SET_SETTING" con setting_name: "daily_summary" y value: true/false.
    - Si el usuario especifica una hora para el resumen (ej: "listado a las 8am"), usa action: "SET_SETTING", setting_name: "daily_summary_time" y value: "HH:MM:SS".
    - Si el usuario saluda o pregunta algo general (como "¿qué hora es?"), usa action: "CHAT".
    - Si quiere ver sus recordatorios ("mis recordatorios", "lista", "cuáles tengo"), usa action: "LIST".
    - Si quiere crear un recordatorio, usa action: "CREATE" con la descripción de la tarea.
    - Si la instrucción implica repetición (ej: "diario", "todos los lunes", "cada semana", "lunes a viernes"), genera en el campo "recurrence" una regla RRULE válida (formato iCalendar).
      Ejemplos:
      "lunes a viernes a las 5pm" -> "FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR"
      "todos los domingos" -> "FREQ=WEEKLY;BYDAY=SU"
      "cada día a las 10am" -> "FREQ=DAILY"
      Si no es recurrente, pon "recurrence": null.
    - Si quiere borrar un recordatorio, usa action: "DELETE" y en el campo "message" extrae SOLO el identificador (ID numérico o palabra clave principal), sin verbos como "borra", "quitar", "elimina", etc.
      Ejemplo: Si dice "Borra el recordatorio con ID 5" → "message": "5"
      Ejemplo: Si dice "Elimina la tarea de la leche" → "message": "leche"
    - Si quiere cambiar, corregir, posponer o modificar un recordatorio existente, usa action: "UPDATE".
      Extrae el ID del recordatorio y los campos a cambiar. Incluye en el JSON solo los campos que cambian.
      IMPORTANTE: El campo "id" DEBE ser un número entero (INTEGER), no una cadena de texto.
      El ID debe extraerse de los resultados previos de LIST o de ALERTAS recientes.
      Ejemplo: Si dice "Cambia el recordatorio 5 a las 3 PM" → {{"action": "UPDATE", "id": 5, "date": "2026-02-14 15:00:00", "reply": "..."}}
      Ejemplo: Si dice "Edita la tarea 3 a comprar pan" → {{"action": "UPDATE", "id": 3, "message": "comprar pan", "reply": "..."}}
    - IMPORTANTE SOBRE RECORDATORIOS ENVIADOS Y ALERTAS:
      1. Cuando un recordatorio suena y se envía al usuario, su estado cambia a 'sent' (NO se borra) y aparece en el historial como un alert JSON.
      2. Si el usuario pide "reprogramar", "posponer" o "cambiar" un recordatorio que acaba de sonar, usa action: "UPDATE".
      3. Confía en los IDs que el usuario mencione o que hayan aparecido en el historial reciente (especialmente en los objetos con action: "ALERT").
      4. Al reprogramar (cambiar la fecha), el recordatorio se reactivará automáticamente pasándolo a 'pending'.
    - REGLA CRÍTICA SOBRE PRONOMBRES:
      Si el usuario usa palabras como "este", "ese", "el anterior", "el último" o "el que acaba de sonar" 
      inmediatamente después de que el asistente haya enviado una alerta (action: "ALERT" con ID: X), 
      la acción es UPDATE y el ID debe ser el que aparece en la última alerta del historial.
    - Responde siempre de forma amable en español.
    - IMPORTANTE: Si en el historial existe una pregunta de confirmación o seguimiento, el siguiente mensaje del usuario es una RESPUESTA a esa pregunta, no una nueva acción.
    
    REGLA SOBRE IMÁGENES:
    - Si el mensaje del usuario incluye "[📸 El usuario adjuntó una imagen a este mensaje]", significa que hay una imagen adjunta que se guardará automáticamente con el recordatorio.
    - Cuando veas este indicador y el usuario diga "recuérdame esto", "guarda esto", o similar, crea un recordatorio con action: "CREATE".
    - En el campo "message", incluye la descripción que dio el usuario. No necesitas describir la imagen, ya se adjunta automáticamente.
    - Si el usuario solo adjuntó la imagen sin dar instrucciones claras de fecha/hora, pregúntale cuándo quiere ser recordado.
    
    NOTAS PERSISTENTES (son DIFERENTES de los recordatorios):
    - Las NOTAS se guardan con el comando /nota y NO tienen fecha/hora. Son datos que el usuario quiere recordar (contraseñas, datos, ideas, etc.).
    - Los RECORDATORIOS tienen fecha/hora y generan alertas.
    - Si el usuario pregunta por información guardada, datos personales, contraseñas, notas, o dice "¿qué notas tengo?", "¿cuál era la clave del wifi?", o cualquier consulta sobre información que pudo haber guardado como nota, usa action: "CONSULTAR_NOTAS".
    - NO necesitas ningún parámetro extra para CONSULTAR_NOTAS, solo pon: {{"action": "CONSULTAR_NOTAS", "reply": ""}}
    """

    messages = [
        {"role": "system", "content": system_prompt}
    ]
    
    # Extender con historial si existe
    if history:
        messages.extend(history)
    
    # Agregar mensaje actual del usuario
    messages.append({"role": "user", "content": text})
    
    content = request_ai_text(
        messages,
        timeout=OPENROUTER_TEXT_TIMEOUT,
        log_context=f"recordatorios/{len(messages)}_mensajes",
    )
    if content is None:
        return None

    logger.info(f"Respuesta cruda de IA: {str(content)[:200]}...")

    parsed_result = parse_structured_response(content, context_label="recordatorios")
    if parsed_result is None:
        return None

    return parsed_result


def process_vision_input(text, image_base64, history=None, active_reminders=None):
    """Procesa mensajes con imágenes usando el modelo de visión.
    
    Args:
        text: Texto del usuario (puede ser caption o instrucción posterior)
        image_base64: Imagen codificada en base64 (formato: data:image/jpeg;base64,...)
        history: Historial de conversación
        active_reminders: Recordatorios activos del usuario
    
    Returns:
        Dict con la respuesta parseada o None si hay error
    """
    clear_last_brain_failure()
    
    # Obtener hora actual de Bogotá
    tz_bogota = pytz.timezone('America/Bogota')
    now = datetime.now(tz_bogota)
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    
    # Generar Mini-Calendario
    mini_calendar = []
    dias_espanol = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
    
    for i in range(15):
        future_date = now + timedelta(days=i)
        day_name = dias_espanol[future_date.weekday()]
        date_iso = future_date.strftime("%Y-%m-%d")
        mini_calendar.append(f"- {day_name} {date_iso}")
    
    mini_calendar_str = "\n".join(mini_calendar)
    
    # Preparar contexto de recordatorios activos
    reminders_context = ""
    if active_reminders:
        reminders_context = "\nRECORDATORIOS ACTIVOS ACTUALES DEL USUARIO (Fuente de Verdad):\n"
        for r in active_reminders:
            recur_info = f" [Recurrente: {r[3]}]" if len(r) > 3 and r[3] else ""
            reminders_context += f"- ID {r[0]}: \"{r[1]}\" para el {r[2]}{recur_info}\n"
    else:
        reminders_context = "\nEl usuario no tiene recordatorios activos actualmente.\n"

    # Prompt del sistema para el modelo de visión
    system_prompt = f"""
    Eres 'Clusivai', un asistente personal inteligente con capacidad de ver imágenes.
    CONTEXTO CALENDARIO (ÚSALO COMO VERDAD ABSOLUTA PARA FECHAS):
    Hora actual en Bogotá: {now_str}
    
    PRÓXIMOS DÍAS (Mini-Calendario):
    {mini_calendar_str}
    
    {reminders_context}
    
    IMPORTANTE: Cuando analices una imagen, haz lo siguiente:
    1. Describe brevemente qué ves en la imagen (si es relevante para la tarea)
    2. Si el usuario pide crear un recordatorio basado en la imagen, extrae la información relevante
    3. Si la imagen contiene texto (captura de pantalla, documento, nota), transcríbelo
    
    Debes responder ÚNICAMENTE con un objeto JSON con esta estructura:
    {{
        "action": "CREATE" | "LIST" | "DELETE" | "UPDATE" | "CHAT" | "SET_SETTING" | "CONSULTAR_NOTAS",
        "id": número de ID (solo para UPDATE y SET_SETTING si aplica),
        "setting_name": "nombre del ajuste (solo para SET_SETTING)",
        "value": valor del ajuste (ej: true, false, o una hora '07:45:00'),
        "message": "descripción de la tarea (solo para recordatorios)",
        "date": "YYYY-MM-DD HH:MM:SS" (fecha calculada para CREATE o UPDATE),
        "recurrence": "cadena RRULE (solo si es recurrente) o null",
        "reply": "Tu respuesta directa si la acción es CHAT o confirmación de acción"
    }}

    Reglas para crear recordatorios desde imágenes:
    - Si el usuario dice "recuérdame esto", "guarda esto", o algo similar, crea un recordatorio con action: "CREATE"
    - La descripción del recordatorio debe incluir lo que vez en la imagen (texto, información relevante, etc.)
    - Si no hay instrucción clara pero hay una imagen, pregunta al usuario qué quiere hacer con ella
    
    Ejemplos:
    - Usuario envía imagen de una factura con texto "Pagar el 15" → CREATE con message: "Pagar factura (texto de imagen: [contenido])"
    - Usuario envía imagen y dice "recuérdame revisar esto mañana" → CREATE con message basado en la imagen
    """

    # Construir mensaje con contenido multimodal (texto + imagen)
    user_content = [
        {"type": "text", "text": text},
        {"type": "image_url", "image_url": {"url": image_base64}}
    ]
    
    messages = [
        {"role": "system", "content": system_prompt}
    ]
    
    # Extender con historial si existe (solo mensajes de texto para evitar problemas)
    if history:
        for msg in history:
            if isinstance(msg.get("content"), str):
                messages.append(msg)
    
    # Agregar mensaje actual con imagen
    messages.append({"role": "user", "content": user_content})
    
    content = request_ai_text(
        messages,
        timeout=OPENROUTER_VISION_TIMEOUT,
        log_context="vision",
        capability=AI_VISION_CAPABILITY,
    )
    if content is None:
        return None

    logger.info(f"Respuesta cruda de IA (visión): {str(content)[:200]}...")

    parsed_result = parse_structured_response(content, context_label="vision")
    if parsed_result is None:
        return None

    return parsed_result


def process_notes_query(user_query, notes_data, history=None):
    """Genera una respuesta natural del LLM basada en las notas del usuario.
    
    Args:
        user_query: La pregunta original del usuario
        notes_data: Lista de tuplas con la forma actual de notas en la base de datos
        history: Historial de conversación
    
    Returns:
        String con la respuesta natural, o None si hay error
    """
    clear_last_brain_failure()

    # Formatear notas como contexto
    if notes_data:
        notes_context = "NOTAS GUARDADAS POR EL USUARIO:\n"
        for note in notes_data:
            note_id = note[0]
            content = note[1]

            if len(note) >= 6:
                category = note[2] or 'Sin categoría'
                created_at = note[3]
            else:
                category = 'Sin categoría'
                created_at = note[2]

            notes_context += f"- [Nota #{note_id} | Categoría: {category}] {content} (guardada: {created_at})\n"
    else:
        notes_context = "El usuario NO tiene notas guardadas actualmente."
    
    system_prompt = f"""Eres 'Clusivai', un asistente personal inteligente.
El usuario te preguntó algo y necesitas responder basándote en sus notas guardadas.

{notes_context}

Reglas:
- Responde de forma natural y amable en español.
- Si el usuario pregunta por algo específico (como una contraseña o dato), busca en las notas y respóndele directamente.
- Si pide ver todas sus notas, lístalas de forma organizada.
- Si no tiene notas o no encuentras lo que busca, díselo amablemente y sugiere usar /nota para guardar información.
- Responde SOLO con texto plano (NO JSON). Tu respuesta se enviará directamente al usuario.
"""
    
    messages = [{"role": "system", "content": system_prompt}]
    
    if history:
        messages.extend(history)
    
    messages.append({"role": "user", "content": user_query})
    
    content = request_ai_text(
        messages,
        timeout=OPENROUTER_TEXT_TIMEOUT,
        log_context="notas",
    )
    if content is None:
        return None

    content = content.strip()
    logger.info(f"Respuesta de notas: {content[:100]}...")
    return content

def process_video_summary(transcript, user_instruction=None, history=None, video_source="X.com"):
    """Analiza y resume la transcripción de un video usando el proveedor activo.
    
    Args:
        transcript: Texto transcrito del video.
        user_instruction: Instrucción adicional del usuario (ej: "¿de qué hablan?").
        history: Historial de conversación.
        video_source: Fuente del video, por ejemplo "X.com" o "YouTube".
    
    Returns:
        String con el resumen/análisis, o None si hay error.
    """
    clear_last_brain_failure()

    # Truncar transcript si es muy largo para no exceder límites del modelo
    max_transcript_chars = 15000  # ~3750 tokens aprox
    truncated = False
    if len(transcript) > max_transcript_chars:
        transcript = transcript[:max_transcript_chars]
        truncated = True

    source_label = (video_source or "video").strip()
    source_upper = source_label.upper()
    transcript_origin = "se ha transcrito automaticamente su audio"
    if source_label.lower() == "youtube":
        transcript_origin = "se han obtenido automaticamente sus subtitulos/transcripcion"
    
    system_prompt = f"""Eres 'Clusivai', un asistente personal inteligente.
El usuario compartio un video de {source_label} y {transcript_origin}.

Tu tarea es analizar la transcripción y proporcionar:

1. 📋 **Resumen**: Un resumen conciso y claro del contenido del video (2-4 oraciones).
2. 🔑 **Puntos clave**: Los puntos o ideas principales mencionados (lista con bullets).
3. 💡 **Datos relevantes**: Si hay nombres, cifras, fechas, o datos importantes, menciónalos.

Reglas:
- Responde en español, de forma clara y bien organizada.
- Usa emojis para hacer la respuesta visual y agradable.
- Si la transcripción tiene errores de reconocimiento de voz (palabras mal escritas, fragmentos inconexos), haz tu mejor esfuerzo para interpretar el contenido.
- Si el usuario hizo una pregunta específica sobre el video, enfócate en responderla ADEMÁS del resumen.
- Si la transcripción parece ser música, letra de canción, o contenido no hablado, indícalo.
- Responde SOLO con texto plano formateado (NO JSON). Tu respuesta se enviará directamente al usuario.
- No incluyas la transcripción completa en tu respuesta, solo el análisis.
"""

    messages = [{"role": "system", "content": system_prompt}]
    
    # Agregar historial relevante (solo los últimos mensajes para contexto)
    if history:
        # Limitar historial para no exceder el contexto del modelo
        recent_history = history[-6:] if len(history) > 6 else history
        for msg in recent_history:
            if isinstance(msg.get("content"), str):
                messages.append(msg)
    
    # Construir mensaje del usuario con la transcripción
    user_content = f"TRANSCRIPCION DEL VIDEO DE {source_upper}:\n"
    user_content += "─" * 40 + "\n"
    user_content += transcript
    user_content += "\n" + "─" * 40
    
    if truncated:
        user_content += "\n⚠️ (La transcripción fue truncada por ser muy larga. Analiza lo disponible.)"
    
    if user_instruction:
        user_content += f"\n\nINSTRUCCIÓN DEL USUARIO: {user_instruction}"
    
    messages.append({"role": "user", "content": user_content})
    
    content = request_ai_text(
        messages,
        timeout=OPENROUTER_VIDEO_TIMEOUT,
        log_context="video_summary",
    )
    if content is None:
        return None

    content = content.strip()
    logger.info(f"Resumen de video generado: {len(content)} caracteres")
    return content


def process_repository_chunk(repo_slug, repo_summary, repo_tree, chunk_content, chunk_index, total_chunks, history=None):
    """Analiza una parte del digest de GitIngest y produce hallazgos parciales compactos."""
    system_prompt = """Eres 'Clusivai', un asistente técnico que analiza repositorios de GitHub.
El usuario quiere entender de qué trata un repositorio basándote en un digest generado con GitIngest.

Tu tarea es analizar SOLO la parte suministrada y devolver hallazgos parciales compactos y útiles para una síntesis posterior.

Reglas:
- Responde en español.
- No inventes archivos, tecnologías ni comportamiento que no aparezcan en el digest.
- Sé concreto y técnico.
- Devuelve SOLO texto plano.
- Máximo 6 bullets en total.
- Cada bullet debe ocupar una sola línea y priorizar lo diferencial.
- Usa exactamente estas 3 secciones:
    1. Objetivo o responsabilidad visible
    2. Componentes o flujos detectados
    3. Stack, integraciones o huecos relevantes
"""

    messages = [{"role": "system", "content": system_prompt}]

    if history:
        recent_history = history[-REPO_HISTORY_MESSAGES:] if len(history) > REPO_HISTORY_MESSAGES else history
        for msg in recent_history:
            if isinstance(msg.get("content"), str):
                messages.append(msg)

    user_content = (
        f"REPOSITORIO: {repo_slug}\n"
        f"PARTE: {chunk_index}/{total_chunks}\n\n"
        f"RESUMEN GITINGEST:\n{repo_summary}\n\n"
        f"ESTRUCTURA DEL REPOSITORIO:\n{repo_tree[:REPO_CHUNK_TREE_CHARS]}\n\n"
        f"CONTENIDO DE ESTA PARTE:\n{chunk_content}"
    )
    messages.append({"role": "user", "content": user_content})

    return request_ai_text(
        messages,
        timeout=OPENROUTER_REPO_TIMEOUT,
        max_tokens=REPO_PARTIAL_MAX_TOKENS,
        log_context=f"repo_chunk/{chunk_index}_{total_chunks}",
    )


def synthesize_repository_analysis(repo_slug, repo_summary, repo_tree, partial_analyses, history=None):
    """Consolida los análisis parciales en una explicación final compacta."""
    system_prompt = """Eres 'Clusivai', un asistente técnico que explica repositorios de GitHub en español.
Has recibido varios análisis parciales del mismo repositorio y debes producir una explicación final compacta, clara y puntual.

La respuesta final debe seguir esta estructura:
1. Qué hace el repositorio o qué problema resuelve.
2. Cómo está organizado y cuáles son sus componentes principales.
3. Stack, integraciones y observaciones relevantes.

Reglas:
- Responde en español y en texto plano.
- Máximo 8 bullets en total.
- Cada bullet debe ocupar una sola línea.
- Evita repetir hallazgos entre secciones.
- Si una conclusión no está totalmente confirmada, indícalo en una frase corta.
- No hables de ti mismo ni del proceso interno de análisis.
"""

    messages = [{"role": "system", "content": system_prompt}]

    if history:
        recent_history = history[-REPO_HISTORY_MESSAGES:] if len(history) > REPO_HISTORY_MESSAGES else history
        for msg in recent_history:
            if isinstance(msg.get("content"), str):
                messages.append(msg)

    partials_text = "\n\n".join(
        f"ANÁLISIS PARCIAL {index}:\n{analysis}"
        for index, analysis in enumerate(partial_analyses, start=1)
        if analysis
    )

    user_content = (
        f"REPOSITORIO: {repo_slug}\n\n"
        f"RESUMEN GITINGEST:\n{repo_summary}\n\n"
        f"ESTRUCTURA DEL REPOSITORIO:\n{repo_tree[:REPO_SYNTHESIS_TREE_CHARS]}\n\n"
        f"ANÁLISIS PARCIALES:\n{partials_text}"
    )
    messages.append({"role": "user", "content": user_content})

    return request_ai_text(
        messages,
        timeout=OPENROUTER_REPO_TIMEOUT,
        max_tokens=REPO_SYNTHESIS_MAX_TOKENS,
        log_context="repo_synthesis",
    )