# Plan de implementación — `/ai` con 4 perfiles de proveedor en `bot-clusivai`

## 0. Objetivo

Modificar el proyecto `bot-clusivai` para que el comando `/ai` permita configurar cuatro perfiles/capacidades globales de IA:

1. **Texto**: usado para chat normal y recordatorios.
2. **Análisis**: usado para analizar videos de YouTube, videos de X.com/Twitter y repositorios de GitHub.
3. **Imagen / visión**: usado para análisis de imágenes, ya existente actualmente.
4. **Transcript / transcripción**: usado para convertir audio/video a texto. Actualmente la transcripción de X.com está acoplada a Groq Whisper.

> Nota importante para el agente: aunque el usuario usa la palabra “proveedores”, técnicamente conviene modelarlo como **capacidades configurables**. Cada capacidad tendrá un `provider` y un `model_name`. Esto mantiene la arquitectura actual del proyecto, donde ya existen las capacidades `text` y `vision`.

---

## 1. Contexto actual detectado en el repositorio

### 1.1. Archivos relevantes

El cambio toca principalmente estos archivos:

```text
bot.py
brain.py
database.py
video_handler.py
youtube_handler.py
repo_analysis_worker.py
requirements.txt
```

También puede ser conveniente crear archivos nuevos para separar responsabilidades:

```text
ai_registry.py              # Opcional/recomendado: constantes, labels y matriz provider-capability.
transcription_service.py    # Recomendado: lógica de proveedores de transcript.
tests/                      # Si el repo ya tiene estructura de tests, usarla; si no, crearla.
```

### 1.2. Estado actual de capacidades IA

Actualmente `database.py` define solo dos capacidades:

```python
AI_TEXT_CAPABILITY = 'text'
AI_VISION_CAPABILITY = 'vision'
SUPPORTED_AI_CAPABILITIES = (AI_TEXT_CAPABILITY, AI_VISION_CAPABILITY)
SUPPORTED_AI_PROVIDERS = ('openrouter', 'nvidia')
```

También existen tablas genéricas:

```sql
ai_global_settings (
    capability TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    model_name TEXT NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
)

ai_model_catalog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    capability TEXT NOT NULL,
    model_name TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_used_at DATETIME DEFAULT NULL,
    UNIQUE(provider, capability, model_name)
)
```

Esto es positivo porque **no debería requerirse una migración destructiva de base de datos**. Solo hay que ampliar las capacidades soportadas y sembrar defaults para las nuevas capacidades.

### 1.3. Estado actual del comando `/ai`

En `bot.py` ya existe un flujo de configuración con:

- `ai_command`
- `ai_settings_callback_handler`
- `build_ai_status_text`
- `build_ai_main_markup`
- `build_ai_capability_text`
- `build_ai_capability_markup`
- `build_ai_model_picker_text`
- `build_ai_model_picker_markup`
- `handle_pending_ai_model_entry`

Actualmente el menú principal solo muestra:

```text
Configurar texto
Configurar visión
```

El objetivo es que muestre:

```text
Configurar texto
Configurar análisis
Configurar visión
Configurar transcripción
```

### 1.4. Estado actual de video, repositorios y transcript

En `repo_analysis_worker.py`, el análisis de repositorios usa funciones importadas desde `brain.py`:

```python
from brain import process_repository_chunk, synthesize_repository_analysis
```

Por tanto, el cambio de proveedor/modelo para repositorios debe hacerse en `brain.py`, dentro de esas funciones, haciendo que llamen al perfil `analysis` y no al perfil `text`.

En `bot.py`, el análisis de videos usa `process_video_summary` desde `brain.py`. Esa función también debe usar la capacidad `analysis`.

En `video_handler.py`, la transcripción de audio de X.com está acoplada a Groq:

```python
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
url = "https://api.groq.com/openai/v1/audio/transcriptions"
data = {
    'model': 'whisper-large-v3-turbo',
    'response_format': 'json',
}
```

El objetivo es que el provider/modelo de transcripción venga desde `/ai`, no desde valores hardcodeados.

---

## 2. Diseño objetivo

### 2.1. Capacidades finales

Agregar estas constantes:

```python
AI_TEXT_CAPABILITY = 'text'
AI_ANALYSIS_CAPABILITY = 'analysis'
AI_VISION_CAPABILITY = 'vision'
AI_TRANSCRIPT_CAPABILITY = 'transcript'
```

El orden visual recomendado en `/ai`:

```python
AI_CAPABILITY_ORDER = (
    AI_TEXT_CAPABILITY,
    AI_ANALYSIS_CAPABILITY,
    AI_VISION_CAPABILITY,
    AI_TRANSCRIPT_CAPABILITY,
)
```

Labels recomendados:

```python
AI_CAPABILITY_LABELS = {
    AI_TEXT_CAPABILITY: 'Texto',
    AI_ANALYSIS_CAPABILITY: 'Análisis',
    AI_VISION_CAPABILITY: 'Visión',
    AI_TRANSCRIPT_CAPABILITY: 'Transcripción',
}
```

Descripción corta por capacidad:

```python
AI_CAPABILITY_DESCRIPTIONS = {
    AI_TEXT_CAPABILITY: 'Chat y recordatorios',
    AI_ANALYSIS_CAPABILITY: 'Videos de YouTube/X.com y repositorios GitHub',
    AI_VISION_CAPABILITY: 'Imágenes',
    AI_TRANSCRIPT_CAPABILITY: 'Audio/video a texto',
}
```

### 2.2. Proveedores finales

Mantener:

```python
openrouter
nvidia
```

Agregar:

```python
groq
```

Matriz recomendada de compatibilidad:

```python
AI_PROVIDER_CAPABILITIES = {
    'openrouter': {'text', 'analysis', 'vision'},
    'nvidia': {'text', 'analysis', 'vision'},
    'groq': {'transcript'},
}
```

> Si se quiere permitir Groq también para chat/análisis, se puede ampliar la matriz después, pero para esta tarea inicial conviene usar Groq solo como proveedor de transcripción para evitar mezclar endpoints de chat y audio.

### 2.3. Defaults recomendados

Variables de entorno sugeridas:

```env
# Texto: chat y recordatorios
DEFAULT_TEXT_PROVIDER=openrouter
MODEL_NAME=stepfun/step-3.5-flash:free

# Análisis: videos y repositorios
DEFAULT_ANALYSIS_PROVIDER=openrouter
ANALYSIS_MODEL_NAME=google/gemini-2.0-flash-exp:free

# Imagen / visión
DEFAULT_VISION_PROVIDER=openrouter
VISION_MODEL_NAME=nvidia/nemotron-nano-12b-v2-vl:free

# Transcript
DEFAULT_TRANSCRIPT_PROVIDER=groq
TRANSCRIPT_MODEL_NAME=whisper-large-v3-turbo

# API keys
OPENROUTER_API_KEY=...
NVIDIA_API_KEY=...
GROQ_API_KEY=...
```

No hardcodear el modelo de análisis si el repo ya usa otra variable. Si existe una configuración anterior en `.env`, respetarla y solo agregar fallback.

---

## 3. Plan de cambios por archivo

---

## 3.1. `database.py`

### Objetivo

Ampliar la base de datos para reconocer las nuevas capacidades y validar que cada proveedor sea compatible con la capacidad seleccionada.

### Cambios

#### 3.1.1. Agregar nuevas constantes

Cambiar:

```python
AI_TEXT_CAPABILITY = 'text'
AI_VISION_CAPABILITY = 'vision'
SUPPORTED_AI_CAPABILITIES = (AI_TEXT_CAPABILITY, AI_VISION_CAPABILITY)
SUPPORTED_AI_PROVIDERS = ('openrouter', 'nvidia')
```

Por:

```python
AI_TEXT_CAPABILITY = 'text'
AI_ANALYSIS_CAPABILITY = 'analysis'
AI_VISION_CAPABILITY = 'vision'
AI_TRANSCRIPT_CAPABILITY = 'transcript'

SUPPORTED_AI_CAPABILITIES = (
    AI_TEXT_CAPABILITY,
    AI_ANALYSIS_CAPABILITY,
    AI_VISION_CAPABILITY,
    AI_TRANSCRIPT_CAPABILITY,
)

SUPPORTED_AI_PROVIDERS = ('openrouter', 'nvidia', 'groq')
```

#### 3.1.2. Agregar matriz de compatibilidad

Agregar cerca de las constantes:

```python
AI_PROVIDER_CAPABILITIES = {
    'openrouter': (AI_TEXT_CAPABILITY, AI_ANALYSIS_CAPABILITY, AI_VISION_CAPABILITY),
    'nvidia': (AI_TEXT_CAPABILITY, AI_ANALYSIS_CAPABILITY, AI_VISION_CAPABILITY),
    'groq': (AI_TRANSCRIPT_CAPABILITY,),
}
```

#### 3.1.3. Crear función de validación provider-capability

Agregar:

```python
def provider_supports_capability(provider, capability):
    normalized_provider = normalize_ai_provider(provider)
    normalized_capability = normalize_ai_capability(capability)
    return normalized_capability in AI_PROVIDER_CAPABILITIES.get(normalized_provider, ())


def validate_provider_capability(provider, capability):
    if not provider_supports_capability(provider, capability):
        raise ValueError('El proveedor seleccionado no soporta esa capacidad de IA.')
```

#### 3.1.4. Usar validación en `save_ai_model`

Dentro de `save_ai_model`, después de normalizar:

```python
validate_provider_capability(normalized_provider, normalized_capability)
```

#### 3.1.5. Usar validación en `activate_ai_model`

Dentro de `activate_ai_model`, después de normalizar:

```python
validate_provider_capability(normalized_provider, normalized_capability)
```

### Criterios de aceptación

- `normalize_ai_capability('analysis')` no falla.
- `normalize_ai_capability('transcript')` no falla.
- `save_ai_model('groq', 'transcript', 'whisper-large-v3-turbo')` funciona.
- `save_ai_model('groq', 'vision', 'x')` falla con `ValueError`.
- Las tablas existentes no se eliminan ni pierden datos.

---

## 3.2. `brain.py`

### Objetivo

Hacer que cada tipo de operación use su propia capacidad:

- Chat/recordatorios → `text`
- Videos y repositorios → `analysis`
- Imágenes → `vision`
- Transcripción → no debería vivir aquí si se separa en `transcription_service.py`, pero debe poder consultar la configuración `transcript`.

### Cambios

#### 3.2.1. Actualizar imports

Cambiar:

```python
from database import AI_TEXT_CAPABILITY, AI_VISION_CAPABILITY, get_ai_setting
```

Por:

```python
from database import (
    AI_TEXT_CAPABILITY,
    AI_ANALYSIS_CAPABILITY,
    AI_VISION_CAPABILITY,
    AI_TRANSCRIPT_CAPABILITY,
    SUPPORTED_AI_CAPABILITIES,
    get_ai_setting,
)
```

#### 3.2.2. Agregar variables de entorno para nuevas capacidades

Agregar:

```python
ANALYSIS_MODEL_NAME = os.getenv("ANALYSIS_MODEL_NAME")
TRANSCRIPT_MODEL_NAME = os.getenv("TRANSCRIPT_MODEL_NAME")

DEFAULT_ANALYSIS_PROVIDER = os.getenv("DEFAULT_ANALYSIS_PROVIDER", "openrouter")
DEFAULT_TRANSCRIPT_PROVIDER = os.getenv("DEFAULT_TRANSCRIPT_PROVIDER", "groq")

DEFAULT_ANALYSIS_MODEL = ANALYSIS_MODEL_NAME or os.getenv("MODEL_NAME") or "stepfun/step-3.5-flash:free"
DEFAULT_TRANSCRIPT_MODEL = TRANSCRIPT_MODEL_NAME or "whisper-large-v3-turbo"
```

> El fallback de `DEFAULT_ANALYSIS_MODEL` puede ajustarse al modelo que prefieras. La idea es que análisis no dependa forzosamente del modelo de texto.

#### 3.2.3. Actualizar `_coerce_provider_name`

Actualmente solo acepta `openrouter` y `nvidia`.

Cambiar:

```python
if normalized in {"openrouter", "nvidia"}:
    return normalized
```

Por:

```python
if normalized in {"openrouter", "nvidia", "groq"}:
    return normalized
```

Sin embargo, no basta con aceptar `groq`; también debe validarse la compatibilidad provider-capability desde `database.py`.

#### 3.2.4. Actualizar `get_default_ai_settings`

Actualmente retorna solo `text` y `vision`.

Debe retornar:

```python
def get_default_ai_settings():
    return {
        AI_TEXT_CAPABILITY: {
            "capability": AI_TEXT_CAPABILITY,
            "provider": _coerce_provider_name(DEFAULT_TEXT_PROVIDER, fallback="openrouter"),
            "model_name": os.getenv("MODEL_NAME") or DEFAULT_TEXT_MODEL,
            "source": "default",
        },
        AI_ANALYSIS_CAPABILITY: {
            "capability": AI_ANALYSIS_CAPABILITY,
            "provider": _coerce_provider_name(DEFAULT_ANALYSIS_PROVIDER, fallback="openrouter"),
            "model_name": os.getenv("ANALYSIS_MODEL_NAME") or DEFAULT_ANALYSIS_MODEL,
            "source": "default",
        },
        AI_VISION_CAPABILITY: {
            "capability": AI_VISION_CAPABILITY,
            "provider": _coerce_provider_name(DEFAULT_VISION_PROVIDER, fallback="openrouter"),
            "model_name": os.getenv("VISION_MODEL_NAME") or DEFAULT_VISION_MODEL,
            "source": "default",
        },
        AI_TRANSCRIPT_CAPABILITY: {
            "capability": AI_TRANSCRIPT_CAPABILITY,
            "provider": _coerce_provider_name(DEFAULT_TRANSCRIPT_PROVIDER, fallback="groq"),
            "model_name": os.getenv("TRANSCRIPT_MODEL_NAME") or DEFAULT_TRANSCRIPT_MODEL,
            "source": "default",
        },
    }
```

#### 3.2.5. Actualizar `get_all_ai_configurations`

Actualmente está hardcodeado a dos capacidades.

Cambiar de:

```python
def get_all_ai_configurations():
    return {
        AI_TEXT_CAPABILITY: get_ai_configuration(AI_TEXT_CAPABILITY),
        AI_VISION_CAPABILITY: get_ai_configuration(AI_VISION_CAPABILITY),
    }
```

A:

```python
def get_all_ai_configurations():
    return {
        capability: get_ai_configuration(capability)
        for capability in SUPPORTED_AI_CAPABILITIES
    }
```

#### 3.2.6. Agregar getters opcionales

Agregar:

```python
def get_analysis_model():
    return get_ai_configuration(AI_ANALYSIS_CAPABILITY)["model_name"]


def get_analysis_provider():
    return get_ai_configuration(AI_ANALYSIS_CAPABILITY)["provider"]


def get_transcript_model():
    return get_ai_configuration(AI_TRANSCRIPT_CAPABILITY)["model_name"]


def get_transcript_provider():
    return get_ai_configuration(AI_TRANSCRIPT_CAPABILITY)["provider"]
```

#### 3.2.7. Hacer que video use `analysis`

Buscar la función:

```python
process_video_summary(...)
```

Dentro de esa función, cuando llame a:

```python
request_ai_text(...)
```

asegurarse de pasar:

```python
capability=AI_ANALYSIS_CAPABILITY
```

Ejemplo:

```python
summary_text = request_ai_text(
    messages,
    timeout=OPENROUTER_VIDEO_TIMEOUT,
    max_tokens=...,
    log_context="video/summary",
    capability=AI_ANALYSIS_CAPABILITY,
)
```

#### 3.2.8. Hacer que repositorios usen `analysis`

Buscar las funciones:

```python
process_repository_chunk(...)
synthesize_repository_analysis(...)
```

Dentro de ambas, cuando llamen a:

```python
request_ai_text(...)
```

pasar:

```python
capability=AI_ANALYSIS_CAPABILITY
```

Ejemplo:

```python
partial_analysis = request_ai_text(
    messages,
    timeout=OPENROUTER_REPO_TIMEOUT,
    max_tokens=REPO_PARTIAL_MAX_TOKENS,
    log_context=f"repo/chunk/{chunk_index}_of_{total_chunks}",
    capability=AI_ANALYSIS_CAPABILITY,
)
```

y:

```python
final_analysis = request_ai_text(
    messages,
    timeout=OPENROUTER_REPO_TIMEOUT,
    max_tokens=REPO_SYNTHESIS_MAX_TOKENS,
    log_context="repo/synthesis",
    capability=AI_ANALYSIS_CAPABILITY,
)
```

#### 3.2.9. Mantener chat/recordatorios en `text`

No cambiar `process_user_input` salvo que ya llame a `request_ai_text` sin capacidad. Si no la pasa, `request_ai_text` ya tiene default:

```python
capability=AI_TEXT_CAPABILITY
```

Eso está bien.

#### 3.2.10. Mantener visión en `vision`

Validar que `process_vision_input` pase:

```python
capability=AI_VISION_CAPABILITY
```

Si no lo hace, corregirlo.

### Criterios de aceptación

- Al analizar un video, el log debe mostrar `capacidad=analysis`.
- Al analizar un repositorio, el log debe mostrar `capacidad=analysis`.
- El chat normal y recordatorios deben seguir usando `capacidad=text`.
- La visión debe seguir usando `capacidad=vision`.
- Cambiar el modelo de análisis desde `/ai` debe afectar videos y repositorios sin cambiar texto ni visión.

---

## 3.3. `bot.py`

### Objetivo

Actualizar el menú `/ai` para administrar cuatro capacidades y no solo dos.

### Cambios

#### 3.3.1. Actualizar imports desde `database.py`

Cambiar:

```python
from database import (AI_TEXT_CAPABILITY, AI_VISION_CAPABILITY, UNCATEGORIZED_LABEL,
...
)
```

Por:

```python
from database import (
    AI_TEXT_CAPABILITY,
    AI_ANALYSIS_CAPABILITY,
    AI_VISION_CAPABILITY,
    AI_TRANSCRIPT_CAPABILITY,
    SUPPORTED_AI_CAPABILITIES,
    AI_PROVIDER_CAPABILITIES,
    UNCATEGORIZED_LABEL,
    ...
)
```

Si no se quiere importar `AI_PROVIDER_CAPABILITIES`, crear una función en `database.py`:

```python
get_supported_ai_providers_for_capability(capability)
```

y usar esa función desde `bot.py`.

#### 3.3.2. Actualizar `AI_PROVIDER_LABELS`

Cambiar:

```python
AI_PROVIDER_LABELS = {
    'openrouter': 'OpenRouter',
    'nvidia': 'Nvidia',
}
```

Por:

```python
AI_PROVIDER_LABELS = {
    'openrouter': 'OpenRouter',
    'nvidia': 'Nvidia',
    'groq': 'Groq',
}
```

#### 3.3.3. Actualizar `AI_CAPABILITY_LABELS`

Cambiar:

```python
AI_CAPABILITY_LABELS = {
    AI_TEXT_CAPABILITY: 'Texto',
    AI_VISION_CAPABILITY: 'Vision',
}
```

Por:

```python
AI_CAPABILITY_LABELS = {
    AI_TEXT_CAPABILITY: 'Texto',
    AI_ANALYSIS_CAPABILITY: 'Análisis',
    AI_VISION_CAPABILITY: 'Visión',
    AI_TRANSCRIPT_CAPABILITY: 'Transcripción',
}
```

#### 3.3.4. Agregar descripciones

Agregar:

```python
AI_CAPABILITY_DESCRIPTIONS = {
    AI_TEXT_CAPABILITY: 'Chat y recordatorios',
    AI_ANALYSIS_CAPABILITY: 'Videos de YouTube/X.com y repositorios',
    AI_VISION_CAPABILITY: 'Imágenes',
    AI_TRANSCRIPT_CAPABILITY: 'Audio/video a texto',
}
```

#### 3.3.5. Actualizar `build_ai_status_text`

Actualmente construye solo texto y visión.

Reemplazar por una versión dinámica:

```python
def build_ai_status_text(notice=None):
    configs = get_all_ai_configurations()

    lines = [
        "⚙️ Configuración global de IA",
        "",
    ]

    for capability in (
        AI_TEXT_CAPABILITY,
        AI_ANALYSIS_CAPABILITY,
        AI_VISION_CAPABILITY,
        AI_TRANSCRIPT_CAPABILITY,
    ):
        config = configs[capability]
        label = get_ai_capability_label(capability)
        description = AI_CAPABILITY_DESCRIPTIONS.get(capability, "")
        suffix = f" — {description}" if description else ""
        lines.append(
            f"{label}: {get_ai_provider_label(config['provider'])} · {config['model_name']}{suffix}"
        )

    if notice:
        lines.extend(["", notice])

    return "\n".join(lines)
```

#### 3.3.6. Actualizar `build_ai_main_markup`

Actualmente muestra dos botones.

Reemplazar por:

```python
def build_ai_main_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Configurar texto", callback_data=f"{AI_CALLBACK_PREFIX}scope:{AI_TEXT_CAPABILITY}")],
        [InlineKeyboardButton("🧠 Configurar análisis", callback_data=f"{AI_CALLBACK_PREFIX}scope:{AI_ANALYSIS_CAPABILITY}")],
        [InlineKeyboardButton("🖼️ Configurar visión", callback_data=f"{AI_CALLBACK_PREFIX}scope:{AI_VISION_CAPABILITY}")],
        [InlineKeyboardButton("🎙️ Configurar transcripción", callback_data=f"{AI_CALLBACK_PREFIX}scope:{AI_TRANSCRIPT_CAPABILITY}")],
        [InlineKeyboardButton("🔄 Actualizar", callback_data=f"{AI_CALLBACK_PREFIX}menu")],
    ])
```

#### 3.3.7. Actualizar `build_ai_capability_text`

Agregar descripción por capacidad:

```python
description = AI_CAPABILITY_DESCRIPTIONS.get(capability)
if description:
    lines.insert(2, f"Uso: {description}")
```

Resultado esperado:

```text
⚙️ Configuración de Análisis

Uso: Videos de YouTube/X.com y repositorios
Proveedor activo: OpenRouter
Modelo activo: google/gemini-...

Elige el proveedor para ver y activar modelos guardados.
```

#### 3.3.8. Hacer dinámicos los botones de proveedor

Actualmente `build_ai_capability_markup` tiene hardcodeado:

```python
OpenRouter
Nvidia
```

Debe cambiar a algo como:

```python
def get_supported_providers_for_capability(capability):
    return [
        provider
        for provider, capabilities in AI_PROVIDER_CAPABILITIES.items()
        if capability in capabilities
    ]
```

Luego:

```python
def build_ai_capability_markup(capability):
    provider_buttons = []

    for provider in get_supported_providers_for_capability(capability):
        provider_buttons.append(
            InlineKeyboardButton(
                get_ai_provider_label(provider),
                callback_data=f"{AI_CALLBACK_PREFIX}provider:{capability}:{provider}",
            )
        )

    rows = []
    for index in range(0, len(provider_buttons), 2):
        rows.append(provider_buttons[index:index + 2])

    rows.append([InlineKeyboardButton("🏠 Menú", callback_data=f"{AI_CALLBACK_PREFIX}menu")])
    return InlineKeyboardMarkup(rows)
```

Con esto:

- Texto muestra OpenRouter / Nvidia.
- Análisis muestra OpenRouter / Nvidia.
- Visión muestra OpenRouter / Nvidia.
- Transcripción muestra Groq.

#### 3.3.9. Actualizar `get_provider_env_key`

Actualmente:

```python
def get_provider_env_key(provider):
    return 'NVIDIA_API_KEY' if provider == 'nvidia' else 'OPENROUTER_API_KEY'
```

Cambiar por:

```python
def get_provider_env_key(provider):
    if provider == 'nvidia':
        return 'NVIDIA_API_KEY'
    if provider == 'groq':
        return 'GROQ_API_KEY'
    return 'OPENROUTER_API_KEY'
```

#### 3.3.10. Actualizar `validate_ai_configuration`

Actualmente solo piensa en texto y visión.

Cambiar para:

1. Iterar por todas las capacidades.
2. Validar que exista la API key del proveedor activo.
3. Fallar al iniciar solo si falta el provider de texto.
4. Para análisis, visión y transcript, registrar warning en vez de detener todo el bot.
5. Mantener la validación real con request corto solo para `text`.

Ejemplo de intención:

```python
for capability, config in configs.items():
    api_key = os.getenv(get_provider_env_key(config['provider']), '')
    if api_key and not api_key.startswith('your-'):
        continue

    message = (
        f"La configuración de {get_ai_capability_label(capability)} usa "
        f"{get_ai_provider_label(config['provider'])}, pero falta {get_provider_env_key(config['provider'])}."
    )

    if capability == AI_TEXT_CAPABILITY:
        logging.error(message)
        return False

    logging.warning(message)
```

#### 3.3.11. Actualizar `seed_ai_catalog_defaults`

Actualmente:

```python
def seed_ai_catalog_defaults():
    ensure_default_ai_settings(get_default_ai_settings())
    save_ai_model('nvidia', AI_TEXT_CAPABILITY, DEFAULT_NVIDIA_TEXT_MODEL)
```

Actualizar para sembrar defaults de las cuatro capacidades.

Ejemplo:

```python
def seed_ai_catalog_defaults():
    ensure_default_ai_settings(get_default_ai_settings())

    # Opcional: modelos sugeridos para catálogo.
    save_ai_model('nvidia', AI_TEXT_CAPABILITY, DEFAULT_NVIDIA_TEXT_MODEL)
    save_ai_model('nvidia', AI_ANALYSIS_CAPABILITY, DEFAULT_NVIDIA_TEXT_MODEL)
    save_ai_model('groq', AI_TRANSCRIPT_CAPABILITY, os.getenv('TRANSCRIPT_MODEL_NAME', 'whisper-large-v3-turbo'))
```

Cuidado: si se agrega matriz de compatibilidad, no guardar Groq para visión ni Nvidia para transcript.

### Criterios de aceptación

- `/ai` muestra las cuatro opciones.
- Cada opción muestra proveedor/modelo activo.
- Al entrar en `Transcripción`, solo aparece `Groq` si la matriz deja solo Groq.
- Al agregar un modelo manual para `Transcripción`, se guarda con capability `transcript`.
- Al activar un modelo en `Análisis`, no cambia el modelo de `Texto`.

---

## 3.4. `video_handler.py`

### Objetivo

Eliminar el acoplamiento rígido de transcripción a:

```python
GROQ_API_KEY
whisper-large-v3-turbo
```

y hacer que use la capacidad configurable `transcript`.

### Opción recomendada: crear `transcription_service.py`

Crear un archivo nuevo:

```text
transcription_service.py
```

Responsabilidad:

- Leer la configuración activa de `AI_TRANSCRIPT_CAPABILITY`.
- Resolver provider.
- Resolver API key.
- Llamar al endpoint de transcripción correcto.
- Retornar `(transcript, error)`.

### Código base sugerido

```python
import os
import logging
import requests

from database import AI_TRANSCRIPT_CAPABILITY
from brain import get_ai_configuration

logger = logging.getLogger(__name__)

GROQ_TRANSCRIPT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"

SUPPORTED_AUDIO_MIME_TYPES = {
    '.flac': 'audio/flac',
    '.m4a': 'audio/m4a',
    '.mp3': 'audio/mpeg',
    '.mp4': 'audio/mp4',
    '.mpeg': 'audio/mpeg',
    '.mpga': 'audio/mpeg',
    '.oga': 'audio/ogg',
    '.ogg': 'audio/ogg',
    '.opus': 'audio/ogg',
    '.wav': 'audio/wav',
    '.webm': 'audio/webm',
}


def get_audio_mime_type(audio_path):
    extension = os.path.splitext(audio_path)[1].lower()
    return SUPPORTED_AUDIO_MIME_TYPES.get(extension, 'application/octet-stream')


def get_transcript_api_key(provider):
    if provider == "groq":
        return os.getenv("GROQ_API_KEY")
    return None


def transcribe_audio_with_active_provider(audio_path, timeout=120):
    config = get_ai_configuration(AI_TRANSCRIPT_CAPABILITY)
    provider = config["provider"]
    model_name = config["model_name"]

    if provider == "groq":
        return transcribe_audio_with_groq(audio_path, model_name, timeout=timeout)

    logger.error("Proveedor de transcripción no soportado: %s", provider)
    return None, f"Proveedor de transcripción no soportado: {provider}"


def transcribe_audio_with_groq(audio_path, model_name, timeout=120):
    api_key = get_transcript_api_key("groq")
    if not api_key:
        logger.error("GROQ_API_KEY no está configurado")
        return None, "Error de configuración: GROQ_API_KEY no está definido."

    headers = {
        "Authorization": f"Bearer {api_key}"
    }

    try:
        file_size = os.path.getsize(audio_path)
        logger.info(
            "Transcribiendo audio con Groq | modelo=%s | path=%s | size_kb=%.1f",
            model_name,
            audio_path,
            file_size / 1024,
        )

        with open(audio_path, "rb") as audio_file:
            files = {
                "file": (
                    os.path.basename(audio_path),
                    audio_file,
                    get_audio_mime_type(audio_path),
                )
            }
            data = {
                "model": model_name,
                "response_format": "json",
            }

            response = requests.post(
                GROQ_TRANSCRIPT_URL,
                headers=headers,
                files=files,
                data=data,
                timeout=timeout,
            )

        if response.status_code == 413:
            return None, "El archivo de audio es demasiado grande para la API de transcripción."

        if response.status_code == 429:
            return None, "Se excedió el límite de la API de transcripción. Intenta de nuevo en unos minutos."

        if response.status_code != 200:
            logger.error("Groq transcript error: %s - %s", response.status_code, response.text[:300])
            return None, f"Error en la transcripción (código {response.status_code}). Intenta de nuevo."

        result = response.json()
        transcript = result.get("text", "").strip()

        if not transcript:
            return None, "La transcripción está vacía. El audio podría no contener voz clara."

        logger.info("Transcripción exitosa con Groq: %s caracteres", len(transcript))
        return transcript, None

    except requests.exceptions.Timeout:
        logger.error("Timeout al transcribir audio")
        return None, "La transcripción tardó demasiado. Intenta con un video más corto."
    except requests.exceptions.ConnectionError:
        logger.error("Error de conexión con proveedor de transcripción")
        return None, "Error de conexión con el servicio de transcripción."
    except Exception as exc:
        logger.error("Error inesperado transcribiendo audio: %s", exc, exc_info=True)
        return None, f"Error inesperado al transcribir: {str(exc)[:100]}"
```

### Cambios en `video_handler.py`

Reemplazar la implementación de `transcribe_audio` por un wrapper retrocompatible:

```python
from transcription_service import transcribe_audio_with_active_provider

def transcribe_audio(audio_path):
    return transcribe_audio_with_active_provider(audio_path)
```

O mover completamente la función y actualizar imports en `bot.py`.

### Cuidado con ciclos de importación

Si `transcription_service.py` importa `brain.get_ai_configuration`, y `brain.py` no importa `video_handler.py`, no debería haber ciclo problemático.

Si aparece ciclo, mover `get_ai_configuration` y defaults a un archivo neutral como:

```text
ai_settings.py
```

Pero primero intentar el cambio mínimo.

### Criterios de aceptación

- `video_handler.py` ya no tiene el modelo `'whisper-large-v3-turbo'` hardcodeado dentro de `transcribe_audio`.
- Cambiar el modelo en `/ai > Transcripción > Groq` cambia el modelo usado para transcribir.
- Si falta `GROQ_API_KEY`, el error sigue siendo claro.
- La descarga de audio de X.com no se rompe.
- El límite `MAX_AUDIO_SIZE_BYTES` sigue funcionando.

---

## 3.5. `youtube_handler.py`

### Objetivo

Decidir claramente el rol del perfil `transcript` para YouTube.

Actualmente YouTube no usa Groq para transcribir. El pipeline usa:

1. YouTube Data API para detectar subtítulos.
2. RapidAPI para obtener transcript.

Esto no es exactamente “transcripción de audio”; es recuperación de subtítulos/transcript existente.

### Decisión recomendada

Mantener YouTube como está para obtener transcript si hay subtítulos, pero documentar el flujo:

- `youtube_handler.py` obtiene transcript desde subtítulos/RapidAPI.
- Luego `brain.process_video_summary` analiza ese transcript usando la capacidad `analysis`.

Opcionalmente, agregar fallback futuro:

1. Intentar transcript con RapidAPI.
2. Si no hay transcript:
   - descargar audio con `yt-dlp`;
   - transcribir con capacidad `transcript`;
   - analizar con capacidad `analysis`.

### Implementación mínima para esta tarea

No cambiar `youtube_handler.py` salvo que el flujo actual requiera fallback.

### Implementación recomendada si se quiere unificar transcript

Agregar una función nueva:

```python
def get_transcript_or_audio_transcription(url: str):
    transcript, error = get_transcript(url)
    if transcript:
        return transcript, None, "youtube_transcript"

    # Futuro:
    # descargar audio con yt-dlp
    # transcribir con transcription_service
    # return transcript, error, "audio_transcription"

    return None, error, "youtube_transcript"
```

### Criterios de aceptación

- YouTube sigue funcionando como antes.
- El análisis posterior del transcript de YouTube usa `analysis`, no `text`.
- El provider `transcript` no se usa innecesariamente si ya existe transcript por subtítulos.

---

## 3.6. `repo_analysis_worker.py`

### Objetivo

No cambiar la arquitectura del worker salvo que sea necesario.

Actualmente el worker llama:

```python
process_repository_chunk(...)
synthesize_repository_analysis(...)
```

El cambio real debe estar en `brain.py`.

### Cambios

No tocar `repo_analysis_worker.py` salvo para mejorar logs si se desea.

Opcional:

```python
_emit(
    progress_queue,
    "progress",
    text="🧠 Analizando el repositorio con el perfil de análisis...",
)
```

### Criterios de aceptación

- El worker sigue ejecutando en proceso aislado.
- El botón de cancelar análisis sigue funcionando.
- El análisis de repositorios usa el provider/modelo activo de `analysis`.

---

## 3.7. `requirements.txt`

### Objetivo

Validar si se necesitan dependencias nuevas.

Actualmente ya existen:

```text
openai
requests
yt-dlp
python-dotenv
python-telegram-bot[job-queue]
gitingest
```

Para la implementación mínima:

- No agregar nuevas dependencias.
- La transcripción con Groq puede seguir usando `requests`.

Si se decide implementar Groq mediante cliente OpenAI-compatible para chat, `openai` ya existe.

### Criterios de aceptación

- `pip install -r requirements.txt` sigue funcionando.
- No se agregan paquetes innecesarios.

---

## 4. Flujo final esperado

### 4.1. Chat normal

Usuario escribe:

```text
Hola, recuérdame comprar leche mañana a las 8
```

Flujo:

```text
bot.py
→ process_user_input(...)
→ request_ai_text(..., capability='text')
→ provider/model activo de Texto
```

### 4.2. Análisis de repositorio

Usuario envía:

```text
Analiza este repo https://github.com/owner/repo
```

Flujo:

```text
bot.py
→ repo_analysis_worker.py
→ process_repository_chunk(..., capability='analysis')
→ synthesize_repository_analysis(..., capability='analysis')
→ provider/model activo de Análisis
```

### 4.3. Análisis de video de YouTube

Usuario envía:

```text
Resume este video https://youtube.com/watch?v=...
```

Flujo:

```text
bot.py
→ youtube_handler.py obtiene transcript
→ brain.process_video_summary(..., capability='analysis')
→ provider/model activo de Análisis
```

### 4.4. Análisis de video de X.com

Usuario envía:

```text
Analiza este video https://x.com/user/status/...
```

Flujo:

```text
bot.py
→ video_handler.download_audio(...)
→ transcription_service.transcribe_audio_with_active_provider(...)
→ provider/model activo de Transcripción
→ brain.process_video_summary(..., capability='analysis')
→ provider/model activo de Análisis
```

### 4.5. Imagen

Usuario envía una imagen.

Flujo:

```text
bot.py
→ process_vision_input(..., capability='vision')
→ provider/model activo de Visión
```

---

## 5. Plan de implementación paso a paso para el agente

## Fase 1 — Preparar branch y baseline

1. Crear branch:

```bash
git checkout -b feature/ai-four-provider-profiles
```

2. Ejecutar bot/tests actuales si existen.
3. Revisar que el bot inicia correctamente antes de tocar código.
4. No cambiar lógica de negocio todavía.

---

## Fase 2 — Ampliar capacidades en `database.py`

1. Agregar:
   - `AI_ANALYSIS_CAPABILITY`
   - `AI_TRANSCRIPT_CAPABILITY`
2. Ampliar `SUPPORTED_AI_CAPABILITIES`.
3. Agregar `groq` a `SUPPORTED_AI_PROVIDERS`.
4. Agregar `AI_PROVIDER_CAPABILITIES`.
5. Crear:
   - `provider_supports_capability`
   - `validate_provider_capability`
   - opcional: `get_supported_ai_providers_for_capability`
6. Aplicar validación dentro de:
   - `save_ai_model`
   - `activate_ai_model`
7. Ejecutar prueba manual rápida desde Python:

```bash
python - <<'PY'
from database import *

print(normalize_ai_capability('analysis'))
print(normalize_ai_capability('transcript'))
print(normalize_ai_provider('groq'))
print(provider_supports_capability('groq', 'transcript'))
print(provider_supports_capability('groq', 'vision'))
PY
```

Resultado esperado:

```text
analysis
transcript
groq
True
False
```

---

## Fase 3 — Ampliar defaults y configuración en `brain.py`

1. Importar nuevas capacidades.
2. Agregar variables:
   - `ANALYSIS_MODEL_NAME`
   - `TRANSCRIPT_MODEL_NAME`
   - `DEFAULT_ANALYSIS_PROVIDER`
   - `DEFAULT_TRANSCRIPT_PROVIDER`
   - `DEFAULT_ANALYSIS_MODEL`
   - `DEFAULT_TRANSCRIPT_MODEL`
3. Permitir `groq` en `_coerce_provider_name`.
4. Actualizar `get_default_ai_settings` para retornar cuatro capacidades.
5. Actualizar `get_all_ai_configurations` para iterar dinámicamente sobre `SUPPORTED_AI_CAPABILITIES`.
6. Agregar getters:
   - `get_analysis_model`
   - `get_analysis_provider`
   - `get_transcript_model`
   - `get_transcript_provider`
7. Validar desde shell:

```bash
python - <<'PY'
from brain import get_default_ai_settings, get_all_ai_configurations
print(get_default_ai_settings())
print(get_all_ai_configurations())
PY
```

Resultado esperado:

- Aparecen `text`, `analysis`, `vision`, `transcript`.
- No se rompe si la DB aún no tenía filas de `analysis` o `transcript`.

---

## Fase 4 — Actualizar menú `/ai` en `bot.py`

1. Importar nuevas capacidades.
2. Agregar labels de:
   - `analysis`
   - `transcript`
   - `groq`
3. Actualizar `build_ai_status_text` para mostrar 4 filas.
4. Actualizar `build_ai_main_markup` para mostrar 4 botones.
5. Cambiar `build_ai_capability_markup` para construir providers dinámicamente desde matriz.
6. Actualizar `get_provider_env_key` para soportar `groq`.
7. Actualizar `validate_ai_configuration` para iterar por todas las capacidades.
8. Actualizar `seed_ai_catalog_defaults`.

Prueba manual:

1. Iniciar bot.
2. En Telegram escribir:

```text
/ai
```

3. Verificar que aparecen:

```text
📝 Configurar texto
🧠 Configurar análisis
🖼️ Configurar visión
🎙️ Configurar transcripción
```

4. Entrar en cada opción y verificar providers:
   - Texto: OpenRouter / Nvidia
   - Análisis: OpenRouter / Nvidia
   - Visión: OpenRouter / Nvidia
   - Transcripción: Groq

---

## Fase 5 — Enrutar análisis de video/repositorio a `analysis`

1. En `brain.py`, localizar:
   - `process_video_summary`
   - `process_repository_chunk`
   - `synthesize_repository_analysis`
2. En cada llamada a `request_ai_text`, pasar:

```python
capability=AI_ANALYSIS_CAPABILITY
```

3. Revisar logs existentes en `post_ai_chat`, porque ya registran `capacidad`.
4. Ejecutar flujo manual de análisis de repo y verificar en logs:

```text
capacidad=analysis
```

5. Ejecutar flujo manual de análisis de YouTube/X.com y verificar:

```text
capacidad=analysis
```

---

## Fase 6 — Crear servicio de transcripción configurable

1. Crear `transcription_service.py`.
2. Mover o replicar ahí:
   - `get_audio_mime_type`
   - lógica de llamada a Groq
   - lectura de config activa de `AI_TRANSCRIPT_CAPABILITY`
3. En `video_handler.py`, mantener `transcribe_audio(audio_path)` como wrapper:

```python
from transcription_service import transcribe_audio_with_active_provider

def transcribe_audio(audio_path):
    return transcribe_audio_with_active_provider(audio_path)
```

4. Eliminar de `video_handler.py`:
   - `GROQ_API_KEY = os.getenv("GROQ_API_KEY")` si ya no se usa.
   - modelo hardcodeado `'whisper-large-v3-turbo'` dentro del request.
5. Validar que el modelo venga de:

```python
get_ai_configuration(AI_TRANSCRIPT_CAPABILITY)["model_name"]
```

6. Probar manualmente:
   - Abrir `/ai`.
   - Entrar a `Transcripción`.
   - Agregar/activar modelo Groq.
   - Enviar video de X.com.
   - Confirmar en logs:

```text
Transcribiendo audio con Groq | modelo=<modelo_activo>
```

---

## Fase 7 — Documentar variables de entorno

Crear si no existe:

```text
.env.example
```

Con:

```env
TELEGRAM_BOT_TOKEN=

OPENROUTER_API_KEY=
NVIDIA_API_KEY=
GROQ_API_KEY=

DEFAULT_TEXT_PROVIDER=openrouter
MODEL_NAME=stepfun/step-3.5-flash:free

DEFAULT_ANALYSIS_PROVIDER=openrouter
ANALYSIS_MODEL_NAME=stepfun/step-3.5-flash:free

DEFAULT_VISION_PROVIDER=openrouter
VISION_MODEL_NAME=nvidia/nemotron-nano-12b-v2-vl:free

DEFAULT_TRANSCRIPT_PROVIDER=groq
TRANSCRIPT_MODEL_NAME=whisper-large-v3-turbo

YOUTUBE_API_KEY=
RAPIDAPI_KEY=

PUBLIC_WEBAPP_URL=
WEBAPP_URL=
LOG_FILE_PATH=logs/clusivai-bot.log
```

No incluir claves reales.

---

## Fase 8 — Tests mínimos recomendados

Si el repo no tiene tests, crear carpeta:

```text
tests/
```

y configurar `pytest` solo si se acepta agregar dependencia. Si no se quiere agregar dependencia, crear scripts de smoke test.

### 8.1. Tests de DB

Casos:

```python
def test_supported_ai_capabilities_include_analysis_and_transcript():
    assert normalize_ai_capability('analysis') == 'analysis'
    assert normalize_ai_capability('transcript') == 'transcript'


def test_groq_supports_transcript_only():
    assert provider_supports_capability('groq', 'transcript') is True
    assert provider_supports_capability('groq', 'vision') is False
```

### 8.2. Tests de defaults

```python
def test_default_ai_settings_has_four_capabilities():
    settings = get_default_ai_settings()
    assert set(settings.keys()) == {'text', 'analysis', 'vision', 'transcript'}
```

### 8.3. Tests de UI `/ai`

Probar que `build_ai_status_text()` incluye:

```text
Texto
Análisis
Visión
Transcripción
```

### 8.4. Tests de enrutamiento

Mockear `request_ai_text` y validar que:

- `process_video_summary` usa `capability='analysis'`.
- `process_repository_chunk` usa `capability='analysis'`.
- `synthesize_repository_analysis` usa `capability='analysis'`.

### 8.5. Tests de transcript

Mockear `requests.post` y validar que:

- Usa `GROQ_API_KEY`.
- Usa el modelo activo de DB/config.
- Retorna transcript desde `result["text"]`.

---

## Fase 9 — Validación manual completa

### 9.1. Validar `/ai`

1. Escribir `/ai`.
2. Confirmar cuatro opciones.
3. Entrar a cada perfil.
4. Agregar modelo de prueba en cada capacidad.
5. Activar modelo.
6. Volver al menú principal y verificar que se refleja el cambio.

### 9.2. Validar texto

Enviar:

```text
Hola, ¿qué puedes hacer?
```

Esperado:

- Usa capacidad `text`.
- No usa `analysis`.

### 9.3. Validar recordatorio

Enviar:

```text
Recuérdame revisar el correo mañana a las 9 am
```

Esperado:

- Usa capacidad `text`.
- Crea recordatorio.

### 9.4. Validar YouTube

Enviar:

```text
Resume este video: <url_youtube>
```

Esperado:

- `youtube_handler.py` obtiene transcript.
- `brain.process_video_summary` usa capacidad `analysis`.

### 9.5. Validar X.com

Enviar:

```text
Analiza este video: <url_x_com>
```

Esperado:

- `video_handler.download_audio` descarga audio.
- `transcription_service` usa capacidad `transcript`.
- `brain.process_video_summary` usa capacidad `analysis`.

### 9.6. Validar GitHub

Enviar:

```text
Analiza este repo: https://github.com/owner/repo
```

Esperado:

- `repo_analysis_worker.py` procesa chunks.
- `brain.process_repository_chunk` usa capacidad `analysis`.
- `brain.synthesize_repository_analysis` usa capacidad `analysis`.

### 9.7. Validar imagen

Enviar imagen.

Esperado:

- Usa capacidad `vision`.
- No cambia respecto al comportamiento actual.

---

## 6. Riesgos y cuidados

### 6.1. Riesgo: confundir provider con capability

No crear cuatro providers llamados:

```text
texto
analysis
vision
transcript
```

Eso sería incorrecto.

Lo correcto es:

```text
capability = text | analysis | vision | transcript
provider = openrouter | nvidia | groq
model_name = modelo exacto
```

### 6.2. Riesgo: romper DB existente

No cambiar nombres de columnas.
No borrar tablas.
No hacer migración destructiva.

Solo agregar nuevas capabilities en código y dejar que `ensure_default_ai_settings` inserte filas faltantes.

### 6.3. Riesgo: permitir combinaciones inválidas

Evitar:

```text
Groq + Vision
Nvidia + Transcript
OpenRouter + Transcript
```

al menos en la primera versión.

Usar matriz de compatibilidad.

### 6.4. Riesgo: YouTube transcript no es igual a transcripción

YouTube actualmente obtiene subtítulos/transcript desde APIs externas. No forzar Groq transcript para YouTube si ya se obtuvo transcript por subtítulos.

La secuencia correcta:

```text
Obtener transcript existente → analizar con analysis
```

Solo usar `transcript` si se implementa fallback descargando audio.

### 6.5. Riesgo: imports circulares

Si aparece un ciclo como:

```text
brain.py → transcription_service.py → brain.py
```

resolver moviendo configuración común a:

```text
ai_settings.py
```

o:

```text
ai_registry.py
```

Idealmente, constantes y registry deberían vivir fuera de `brain.py`.

---

## 7. Checklist final para el agente

Antes de entregar, confirmar:

- [ ] `database.py` soporta `text`, `analysis`, `vision`, `transcript`.
- [ ] `database.py` soporta provider `groq`.
- [ ] Existe matriz provider-capability.
- [ ] `/ai` muestra cuatro opciones.
- [ ] `/ai > Transcripción` permite configurar Groq/modelo.
- [ ] `/ai > Análisis` permite configurar proveedor/modelo separado del texto.
- [ ] Video summary usa `AI_ANALYSIS_CAPABILITY`.
- [ ] Repo analysis usa `AI_ANALYSIS_CAPABILITY`.
- [ ] Transcript de X.com usa `AI_TRANSCRIPT_CAPABILITY`.
- [ ] Chat y recordatorios siguen usando `AI_TEXT_CAPABILITY`.
- [ ] Imagen sigue usando `AI_VISION_CAPABILITY`.
- [ ] No se borran datos existentes de `reminders.db`.
- [ ] `.env.example` existe o fue actualizado.
- [ ] Logs muestran capability/proveedor/modelo en cada request.
- [ ] Pruebas manuales completadas.

---

## 8. Prompt sugerido para ejecutar con un agente de IA

Copia este bloque y pásaselo al agente que hará los cambios:

```text
Actúa como un ingeniero senior de Python y Telegram bots.

Necesito modificar el repositorio bot-clusivai para que el comando /ai permita configurar cuatro capacidades globales de IA:

1. text: chat y recordatorios.
2. analysis: análisis de videos de YouTube/X.com y análisis de repositorios GitHub.
3. vision: análisis de imágenes, ya existente.
4. transcript: transcripción de audio/video, actualmente acoplada a Groq Whisper.

No confundas capability con provider:
- capability = text | analysis | vision | transcript
- provider = openrouter | nvidia | groq
- model_name = nombre exacto del modelo activo para esa capability.

Debes implementar los cambios siguiendo este plan:
1. Ampliar database.py con AI_ANALYSIS_CAPABILITY y AI_TRANSCRIPT_CAPABILITY.
2. Agregar groq a providers.
3. Crear matriz provider-capability.
4. Hacer que save_ai_model y activate_ai_model validen que el provider soporte la capability.
5. Actualizar brain.py para defaults de 4 capacidades.
6. Hacer que get_all_ai_configurations sea dinámico.
7. Hacer que process_video_summary, process_repository_chunk y synthesize_repository_analysis usen AI_ANALYSIS_CAPABILITY.
8. Mantener process_user_input en AI_TEXT_CAPABILITY.
9. Mantener process_vision_input en AI_VISION_CAPABILITY.
10. Actualizar bot.py para que /ai muestre cuatro opciones.
11. Hacer dinámicos los botones de providers por capability.
12. Agregar soporte de GROQ_API_KEY en get_provider_env_key.
13. Crear o refactorizar transcription_service.py para que la transcripción use AI_TRANSCRIPT_CAPABILITY y no modelo hardcodeado.
14. Mantener video_handler.transcribe_audio como wrapper retrocompatible si es necesario.
15. Actualizar o crear .env.example.
16. Agregar pruebas o smoke tests mínimos.
17. Validar manualmente chat, recordatorios, análisis de YouTube, análisis de X.com, análisis de repo e imagen.

No hagas migraciones destructivas.
No borres tablas existentes.
No cambies la forma en que se guardan recordatorios/notas.
Entrega un resumen de archivos modificados, decisiones tomadas y pruebas realizadas.
```

---

## 9. Entrega esperada del agente

Al final, el agente debe entregar algo así:

```text
Cambios realizados:
- database.py: nuevas capabilities analysis/transcript, provider groq, matriz de compatibilidad.
- brain.py: defaults para cuatro capacidades, análisis enroutado a analysis.
- bot.py: menú /ai ampliado a cuatro perfiles.
- transcription_service.py: nuevo servicio configurable para transcript.
- video_handler.py: transcribe_audio ahora delega en transcription_service.
- .env.example: nuevas variables.

Pruebas:
- /ai muestra cuatro opciones.
- Texto usa capability=text.
- Repositorio usa capability=analysis.
- Video usa capability=analysis.
- X.com usa transcript=groq y luego analysis.
- Imagen usa capability=vision.
```
