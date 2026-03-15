# Plan de Implementación: Soporte de URLs de YouTube en el Bot de Telegram

## Contexto y objetivo

El bot ya maneja enlaces de X.com y GitHub mostrando botones de acción al usuario. El objetivo es replicar ese patrón para URLs de YouTube, ofreciendo dos opciones:

- **🎬 Analizar Video** → obtiene la transcripción automáticamente (sin selección de idioma) y la analiza con el LLM.
- **⏰ Recordatorio** → delega al flujo normal de recordatorios ya existente.

La función `process_video_summary` de `brain.py` ya existe y no necesita ningún cambio.

---

## Archivos involucrados

| Archivo | Acción |
|---|---|
| `youtube_handler.py` | **Crear** (archivo nuevo) |
| `bot.py` | **Modificar** (4 inserciones puntuales) |
| `requirements.txt` | **Modificar** (1 línea nueva) |
| `brain.py` | Sin cambios |
| `database.py` | Sin cambios |

---

## PASO 1 — Crear `youtube_handler.py`

Crear el archivo `/youtube_handler.py` en la raíz del proyecto (mismo nivel que `bot.py`).

**Contenido completo del archivo:**

```python
"""
youtube_handler.py
Módulo para extraer la transcripción de un video de YouTube
usando youtube-transcript-api sin necesidad de API keys externos.
"""

import re
import logging

logger = logging.getLogger(__name__)

# Orden de preferencia de idiomas para la selección automática
LANGUAGE_PRIORITY = ['es', 'en', 'pt', 'fr', 'de']


def extract_video_id(url: str):
    """Extrae el video ID de cualquier formato de URL de YouTube.

    Soporta:
      - https://www.youtube.com/watch?v=VIDEO_ID
      - https://youtu.be/VIDEO_ID
      - https://www.youtube.com/embed/VIDEO_ID

    Returns:
        str con el video ID o None si no se encuentra.
    """
    if not url:
        return None

    patterns = [
        r'(?:youtube\.com/watch\?(?:.*&)?v=)([\w-]+)',
        r'(?:youtu\.be/)([\w-]+)',
        r'(?:youtube\.com/embed/)([\w-]+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def get_transcript(url: str):
    """Obtiene la transcripción completa de un video de YouTube de forma automática.

    Estrategia de selección de idioma (en orden):
      1. Subtítulos manuales en idiomas de LANGUAGE_PRIORITY.
      2. Subtítulos generados automáticamente en idiomas de LANGUAGE_PRIORITY.
      3. El primer subtítulo disponible (cualquier idioma).

    Returns:
        Tupla (transcript_text: str, None)           → éxito
        Tupla (None, error_message: str)             → fallo
    """
    try:
        from youtube_transcript_api import (
            YouTubeTranscriptApi,
            NoTranscriptFound,
            TranscriptsDisabled,
        )
    except ImportError:
        logger.error("youtube-transcript-api no está instalado. Ejecuta: pip install youtube-transcript-api")
        return None, "Error interno: youtube-transcript-api no está instalado."

    video_id = extract_video_id(url)
    if not video_id:
        return None, "No pude extraer el ID del video desde la URL proporcionada."

    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        transcript = None

        # 1. Intentar subtítulos manuales en idiomas prioritarios
        try:
            transcript = transcript_list.find_manually_created_transcript(LANGUAGE_PRIORITY)
            logger.info(f"Subtítulos manuales encontrados para {video_id}: {transcript.language_code}")
        except NoTranscriptFound:
            pass

        # 2. Intentar subtítulos generados automáticamente en idiomas prioritarios
        if transcript is None:
            try:
                transcript = transcript_list.find_generated_transcript(LANGUAGE_PRIORITY)
                logger.info(f"Subtítulos automáticos encontrados para {video_id}: {transcript.language_code}")
            except NoTranscriptFound:
                pass

        # 3. Tomar el primer disponible sin importar idioma
        if transcript is None:
            available = list(transcript_list)
            if not available:
                return None, "Este video no tiene subtítulos disponibles en ningún idioma."
            transcript = available[0]
            logger.info(f"Usando primer subtítulo disponible para {video_id}: {transcript.language_code}")

        # Obtener las entradas y concatenar el texto
        entries = transcript.fetch()
        full_text = ' '.join(entry['text'] for entry in entries)
        full_text = full_text.strip()

        if not full_text:
            return None, "La transcripción obtenida está vacía."

        logger.info(
            f"Transcripción obtenida para {video_id} | idioma: {transcript.language_code} | {len(full_text)} chars"
        )
        return full_text, None

    except TranscriptsDisabled:
        return None, "Este video tiene los subtítulos desactivados por el creador."
    except Exception as e:
        logger.error(f"Error obteniendo transcripción de {video_id}: {e}", exc_info=True)
        return None, f"No pude obtener la transcripción del video: {str(e)[:150]}"
```

---

## PASO 2 — Modificar `requirements.txt`

Agregar la siguiente línea al final del archivo `requirements.txt`:

```
youtube-transcript-api
```

El archivo final debe quedar así:

```
Flask
Flask-Cors
gitingest
python-dateutil
python-dotenv
python-telegram-bot[job-queue]
pytz
requests
yt-dlp
youtube-transcript-api
```

---

## PASO 3 — Modificar `bot.py`

### 3.1 — Agregar el import de `youtube_handler`

Ubicar el bloque de imports al inicio de `bot.py`. Localizar esta línea:

```python
from repo_handler import (
    GitHubRepositoryError,
    extract_github_repo_url,
    ingest_github_repository,
    split_repository_content,
)
```

Agregar inmediatamente **después** de esa línea:

```python
from youtube_handler import get_transcript as get_youtube_transcript, extract_video_id as extract_youtube_video_id
```

---

### 3.2 — Agregar la función helper `extract_youtube_url`

Ubicar la función existente `extract_x_url` que está importada desde `video_handler`. Después del bloque de imports (antes de la función `build_webapp_url` o en cualquier lugar del módulo antes de `handle_message`), agregar esta función:

```python
def extract_youtube_url(text: str):
    """Detecta y extrae una URL de YouTube de un texto.

    Soporta:
      - https://www.youtube.com/watch?v=...
      - https://youtu.be/...

    Returns:
        La URL encontrada (str) o None si no hay coincidencia.
    """
    if not text:
        return None
    import re
    pattern = r'https?://(?:www\.)?(?:youtube\.com/watch\?(?:[^\s]*&)?v=[\w-]+|youtu\.be/[\w-]+)(?:\S*)?'
    match = re.search(pattern, text)
    return match.group(0) if match else None
```

> **Nota:** el módulo `re` ya está importado en `bot.py` a través de otros módulos. Si no está en los imports del archivo, agregar `import re` al bloque de imports del inicio.

---

### 3.3 — Agregar detección de YouTube en `handle_message`

Ubicar dentro de la función `handle_message` el siguiente bloque **existente** (es el primero que aparece en el `if user_text:`):

```python
    if user_text:
        github_url = extract_github_repo_url(user_text)
        if github_url:
```

Agregar el bloque de YouTube **antes** de la detección de GitHub, de forma que el `if user_text:` quede así:

```python
    if user_text:
        # ── DETECCIÓN DE ENLACE DE YOUTUBE ──
        youtube_url = extract_youtube_url(user_text)
        if youtube_url:
            logging.info(f"Enlace de YouTube detectado para usuario {user_id}: {youtube_url}")

            msg_id = update.message.message_id
            if 'youtube_urls' not in context.user_data:
                context.user_data['youtube_urls'] = {}
            context.user_data['youtube_urls'][str(msg_id)] = {
                'url': youtube_url,
                'text': user_text
            }

            keyboard = [
                [
                    InlineKeyboardButton("🎬 Analizar Video", callback_data=f"yt_analyze:{msg_id}"),
                    InlineKeyboardButton("⏰ Recordatorio",   callback_data=f"yt_reminder:{msg_id}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.message.reply_text(
                "📺 Detecté un enlace de YouTube. ¿Qué deseas hacer?",
                reply_markup=reply_markup
            )
            return

        github_url = extract_github_repo_url(user_text)
        if github_url:
            # ... (código existente sin cambios) ...
```

---

### 3.4 — Agregar la función `process_youtube_video`

Ubicar la función existente `process_github_repository`. Agregar la nueva función **inmediatamente antes** de ella:

```python
async def process_youtube_video(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, user_text: str):
    """Pipeline: obtiene transcripción de YouTube → analiza con LLM → responde al usuario."""
    user_id = update.effective_user.id

    logging.info(f"Procesando video de YouTube para usuario {user_id}: {url}")

    if 'history' not in context.user_data:
        context.user_data['history'] = []

    history = context.user_data.get('history', [])

    try:
        # ── PASO 1: Obtener transcripción ──
        status_msg = await update.effective_message.reply_text(
            "📄 Obteniendo transcripción del video de YouTube..."
        )

        transcript, error = get_youtube_transcript(url)

        if transcript is None:
            await status_msg.edit_text(f"❌ {error}")
            return

        # ── PASO 2: Analizar con LLM ──
        await status_msg.edit_text("🧠 Analizando el contenido del video...")

        user_instruction = user_text.replace(url, '').strip() or None
        if user_instruction and len(user_instruction.strip('., ')) < 3:
            user_instruction = None

        summary = process_video_summary(transcript, user_instruction, history)

        # ── PASO 3: Enviar resultado ──
        await status_msg.delete()

        if summary:
            response_text = f"📺 **Análisis del video de YouTube**\n\n{summary}"
        else:
            response_text = (
                "⚠️ No pude generar el análisis automático. "
                "Aquí tienes la transcripción del video:\n\n"
                f"📝 _{transcript[:3000]}_"
            )
            if len(transcript) > 3000:
                response_text += "\n\n_(Transcripción truncada)_"

        if len(response_text) > 4096:
            for chunk in split_message(response_text, 4096):
                await update.effective_message.reply_text(chunk, parse_mode="Markdown")
        else:
            try:
                await update.effective_message.reply_text(response_text, parse_mode="Markdown")
            except Exception:
                await update.effective_message.reply_text(
                    response_text.replace('*', '').replace('_', '')
                )

        # ── Actualizar historial ──
        context.user_data['history'].append({
            "role": "user",
            "content": f"[Compartí un video de YouTube: {url}]. {user_instruction or 'Analízalo.'}"
        })
        context.user_data['history'].append({
            "role": "assistant",
            "content": json.dumps({
                "action": "YOUTUBE_ANALYSIS",
                "url": url,
                "transcript": transcript[:2000],
                "summary": (summary or "")[:1000],
                "reply": summary or "Transcripción enviada directamente."
            }, ensure_ascii=False)
        })

        if len(context.user_data['history']) > 16:
            context.user_data['history'] = context.user_data['history'][-16:]

        logging.info(f"Video de YouTube procesado exitosamente para usuario {user_id}")

    except Exception as e:
        logging.error(f"Error procesando video de YouTube para usuario {user_id}: {e}", exc_info=True)
        if update.effective_message:
            try:
                await update.effective_message.reply_text(
                    "❌ Hubo un error inesperado procesando el video. Intenta de nuevo."
                )
            except Exception:
                pass
```

---

### 3.5 — Extender `x_link_callback_handler` para manejar `yt_`

Ubicar dentro de `x_link_callback_handler` el siguiente bloque existente:

```python
        if action.startswith("x_"):
            link_data = context.user_data.get('x_urls', {}).get(msg_id)
        elif action.startswith("gh_"):
            link_data = context.user_data.get('github_urls', {}).get(msg_id)
        else:
            link_data = None
```

Reemplazarlo por:

```python
        if action.startswith("x_"):
            link_data = context.user_data.get('x_urls', {}).get(msg_id)
        elif action.startswith("gh_"):
            link_data = context.user_data.get('github_urls', {}).get(msg_id)
        elif action.startswith("yt_"):
            link_data = context.user_data.get('youtube_urls', {}).get(msg_id)
        else:
            link_data = None
```

Luego, ubicar el bloque de `if/elif` que despacha las acciones. Actualmente termina en:

```python
        elif action == "gh_reminder":
            await query.edit_message_text("⏳ Preparando tu recordatorio...")

            instruction = (
                f"Quiero agendar un recordatorio para revisar este repositorio de GitHub: {url}. "
                "Si no indico claramente cuándo, pregúntame para cuándo quiero el recordatorio."
            )

            extra_text = user_text.replace(url, '').strip()
            if extra_text and len(extra_text) > 2:
                instruction = f"Quiero agendar un recordatorio: {extra_text}. Repositorio GitHub: {url}"

            await process_normal_message(update, context, instruction, user_id)
```

Agregar inmediatamente **después** de ese bloque (antes del `except`):

```python
        elif action == "yt_analyze":
            await query.edit_message_text("⏳ Iniciando análisis del video de YouTube...")
            await process_youtube_video(update, context, url, user_text)

        elif action == "yt_reminder":
            await query.edit_message_text("⏳ Preparando tu recordatorio...")

            instruction = (
                f"Quiero agendar un recordatorio para revisar este video de YouTube: {url}. "
                "Si no indico claramente cuándo, pregúntame para cuándo quiero el recordatorio."
            )

            extra_text = user_text.replace(url, '').strip()
            if extra_text and len(extra_text) > 2:
                instruction = f"Quiero agendar un recordatorio: {extra_text}. Video de YouTube: {url}"

            await process_normal_message(update, context, instruction, user_id)
```

---

### 3.6 — Actualizar el patrón del `CallbackQueryHandler`

Ubicar al final de `bot.py`, en el bloque `if __name__ == '__main__':`, esta línea:

```python
    application.add_handler(CallbackQueryHandler(x_link_callback_handler, pattern=r"^(x_|gh_)"))
```

Reemplazarla por:

```python
    application.add_handler(CallbackQueryHandler(x_link_callback_handler, pattern=r"^(x_|gh_|yt_)"))
```

---

## PASO 4 — Verificación de la implementación

El agente debe confirmar que todos los cambios están aplicados correctamente revisando los siguientes puntos:

### 4.1 Checklist de archivos

- [ ] `youtube_handler.py` existe en la raíz del proyecto
- [ ] `requirements.txt` contiene la línea `youtube-transcript-api`
- [ ] `bot.py` importa `get_transcript` y `extract_video_id` desde `youtube_handler`
- [ ] `bot.py` contiene la función `extract_youtube_url`
- [ ] `bot.py` contiene la función `process_youtube_video`
- [ ] `handle_message` en `bot.py` detecta URLs de YouTube antes de las de GitHub
- [ ] `x_link_callback_handler` maneja los prefijos `yt_analyze` y `yt_reminder`
- [ ] El `CallbackQueryHandler` incluye el patrón `yt_` en su regex

### 4.2 Instalación de dependencia

Después de aplicar los cambios, ejecutar en el entorno del servidor:

```bash
pip install youtube-transcript-api
```

O si el proyecto usa un `venv`:

```bash
/home/clusiv/bot-recordatorios/venv/bin/pip install youtube-transcript-api
```

### 4.3 Reinicio del servicio

```bash
sudo systemctl restart clusivai-bot.service
sudo systemctl status clusivai-bot.service
```

---

## Flujo esperado después de la implementación

```
Usuario envía: https://www.youtube.com/watch?v=XYZ
        │
        ▼
handle_message() → extract_youtube_url() detecta la URL
        │
        ▼
Guarda en context.user_data['youtube_urls'][msg_id]
Muestra botones: [🎬 Analizar Video]  [⏰ Recordatorio]
        │
   ┌────┴──────────────────┐
   ▼                       ▼
yt_analyze              yt_reminder
   │                       │
   ▼                       └──→ process_normal_message()
get_youtube_transcript(url)        (flujo de recordatorios
   ├─ list_transcripts()             ya existente — sin cambios)
   ├─ busca manual [es,en,pt,fr,de]
   ├─ fallback: generado automático
   └─ fallback: primer disponible
        │
        ▼
process_video_summary(transcript, instrucción, history)
   (función ya existente en brain.py — sin cambios)
        │
        ▼
Respuesta enviada al usuario por Telegram
Historial de conversación actualizado
```

---

## Notas importantes para el agente

1. **No modificar `brain.py` ni `database.py`** — la función `process_video_summary` ya existe y es completamente reutilizable tal como está.

2. **Orden de detección en `handle_message`**: YouTube debe comprobarse **antes** que GitHub y X.com para evitar falsos negativos si alguna URL tiene parámetros compartidos. El orden correcto es: `YouTube → GitHub → X.com`.

3. **La librería `youtube-transcript-api` no requiere ninguna API key** — es una solución de scraping open source que funciona con videos que tienen subtítulos habilitados (manuales o automáticos).

4. **Videos sin subtítulos**: si el video no tiene ningún tipo de subtítulo, la función retorna un mensaje de error claro que el bot mostrará al usuario con ❌.

5. **El campo `re`**: la función `extract_youtube_url` usa `import re` internamente. Si el agente prefiere, puede mover ese import al bloque de imports del módulo al inicio de `bot.py` (donde ya están los otros imports), eliminando el `import re` dentro del cuerpo de la función.
