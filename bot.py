import os
import multiprocessing
import logging
import json
import queue
import re
import urllib.parse
import uuid
import pytz
import logging
import json
import base64
from logging.handlers import RotatingFileHandler
from datetime import datetime
from dateutil import rrule
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters, CommandHandler, CallbackQueryHandler

from brain import (
    get_all_ai_configurations,
    get_default_ai_settings,
    get_last_brain_failure,
    is_transient_brain_failure,
    process_notes_query,
    process_user_input,
    process_video_summary,
    process_vision_input,
    request_ai_text,
)
from database import (AI_TEXT_CAPABILITY, AI_VISION_CAPABILITY, UNCATEGORIZED_LABEL,
                      activate_ai_model, add_reminder, create_note,
                      delete_reminder_by_text, ensure_default_ai_settings,
                      get_ai_model_by_id, get_connection, get_notes_by_user,
                      get_saved_ai_models, get_today_reminders,
                      get_user_reminders, get_users_with_daily_summary,
                      normalize_note_category, save_ai_model,
                      set_daily_summary, update_reminder_by_id)
from repo_analysis_worker import run_repository_analysis_worker
from video_handler import extract_x_url, download_audio, transcribe_audio, cleanup_audio
from repo_handler import extract_github_repo_url
from youtube_handler import (
    fetch_available_languages as fetch_youtube_available_languages,
    fetch_transcript_by_lang as fetch_youtube_transcript_by_lang,
    get_transcript as get_youtube_transcript,
    extract_video_id as extract_youtube_video_id,
    select_transcript_language as select_youtube_transcript_language,
)

load_dotenv()

WEBAPP_URL = os.getenv("PUBLIC_WEBAPP_URL") or os.getenv("WEBAPP_URL")
LOG_FILE_PATH = os.getenv("LOG_FILE_PATH", "logs/clusivai-bot.log")
LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", "1048576"))
LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "5"))
AI_PENDING_MODEL_KEY = 'ai_pending_model_entry'
AI_CALLBACK_PREFIX = 'ai:'
AI_PROVIDER_LABELS = {
    'openrouter': 'OpenRouter',
    'nvidia': 'Nvidia',
}
AI_CAPABILITY_LABELS = {
    AI_TEXT_CAPABILITY: 'Texto',
    AI_VISION_CAPABILITY: 'Vision',
}
DEFAULT_NVIDIA_TEXT_MODEL = os.getenv('NVIDIA_TEXT_MODEL_NAME', 'stepfun-ai/step-3.5-flash')


def configure_logging():
    handlers = [logging.StreamHandler()]
    resolved_log_path = None

    if LOG_FILE_PATH:
        resolved_log_path = os.path.abspath(LOG_FILE_PATH)
        log_directory = os.path.dirname(resolved_log_path)
        if log_directory:
            os.makedirs(log_directory, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                resolved_log_path,
                maxBytes=LOG_MAX_BYTES,
                backupCount=LOG_BACKUP_COUNT,
                encoding="utf-8",
            )
        )

    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO,
        handlers=handlers,
        force=True,
    )
    logging.getLogger(__name__).info(
        "Logging configurado. Archivo de log=%s",
        resolved_log_path or "solo stdout/journald",
    )


configure_logging()


ACTIVE_REPO_ANALYSES_KEY = 'active_repo_analyses'
ACTIVE_REPO_ANALYSIS_BY_USER_KEY = 'active_repo_analysis_by_user'


def build_repo_cancel_markup(analysis_id: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancelar análisis", callback_data=f"gh_cancel:{analysis_id}")]
    ])


def get_active_repo_analyses(application):
    return application.bot_data.setdefault(ACTIVE_REPO_ANALYSES_KEY, {})


def get_active_repo_analysis_by_user(application):
    return application.bot_data.setdefault(ACTIVE_REPO_ANALYSIS_BY_USER_KEY, {})


def stop_repo_analysis_process(state):
    process = state.get('process')
    if not process:
        return

    if process.is_alive():
        process.terminate()
        process.join(timeout=1)
        if process.is_alive():
            process.kill()
            process.join(timeout=1)

    progress_queue = state.get('queue')
    if progress_queue is not None:
        try:
            progress_queue.close()
        except Exception:
            pass
        try:
            progress_queue.join_thread()
        except Exception:
            pass


def remove_repo_analysis_state(application, analysis_id: str, *, stop_process: bool):
    analyses = get_active_repo_analyses(application)
    state = analyses.pop(analysis_id, None)
    if not state:
        return None

    by_user = get_active_repo_analysis_by_user(application)
    user_id = state.get('user_id')
    if by_user.get(user_id) == analysis_id:
        by_user.pop(user_id, None)

    if stop_process:
        stop_repo_analysis_process(state)

    return state


async def update_repo_status_message(bot, state, text, reply_markup=None):
    if state.get('last_status_text') == text and state.get('last_status_has_markup') == bool(reply_markup):
        return

    try:
        await bot.edit_message_text(
            chat_id=state['chat_id'],
            message_id=state['status_message_id'],
            text=text,
            reply_markup=reply_markup,
        )
        state['last_status_text'] = text
        state['last_status_has_markup'] = bool(reply_markup)
    except Exception as exc:
        logging.warning("No se pudo actualizar el estado del análisis %s: %s", state['analysis_id'], exc)


async def finish_repo_analysis(context: ContextTypes.DEFAULT_TYPE, analysis_id: str, result: dict):
    application = context.application
    state = remove_repo_analysis_state(application, analysis_id, stop_process=True)
    if not state:
        return

    status = result.get('status')
    if status == 'completed':
        await update_repo_status_message(context.bot, state, "✅ Análisis del repositorio finalizado.")

        response_text = result.get('response_text') or "⚠️ El análisis terminó sin contenido para mostrar."
        if len(response_text) > 4096:
            for chunk in split_message(response_text, 4096):
                await context.bot.send_message(chat_id=state['chat_id'], text=chunk)
        else:
            await context.bot.send_message(chat_id=state['chat_id'], text=response_text)

        user_data = context.application.user_data[state['user_id']]
        user_data.setdefault('history', [])

        extra_text = state['user_text'].replace(state['url'], '').strip() if state.get('user_text') else ''
        followup_text = extra_text if extra_text and len(extra_text) > 2 else 'Explícame de qué trata.'
        repo_data = result.get('repo_data') or {}
        final_analysis = result.get('final_analysis') or response_text

        user_data['history'].append({
            "role": "user",
            "content": f"[Compartí un repositorio de GitHub: {repo_data.get('url', state['url'])}]. {followup_text}"
        })
        user_data['history'].append({
            "role": "assistant",
            "content": json.dumps({
                "action": "REPO_ANALYSIS",
                "url": repo_data.get('url', state['url']),
                "repo": repo_data.get('slug', ''),
                "summary": (repo_data.get('summary') or '')[:1000],
                "reply": final_analysis[:2000]
            }, ensure_ascii=False)
        })

        if len(user_data['history']) > 16:
            user_data['history'] = user_data['history'][-16:]

        logging.info("Repositorio GitHub procesado exitosamente para usuario %s", state['user_id'])
        return

    error_message = result.get('error_message') or (
        "No pude analizar ese repositorio de GitHub en este momento. Intenta de nuevo más tarde."
    )
    await update_repo_status_message(context.bot, state, f"❌ {error_message}")


async def poll_repo_analysis_updates(context: ContextTypes.DEFAULT_TYPE):
    analyses = list(get_active_repo_analyses(context.application).items())

    for analysis_id, state in analyses:
        progress_queue = state.get('queue')
        if progress_queue is None:
            continue

        while True:
            try:
                event = progress_queue.get_nowait()
            except queue.Empty:
                break
            except Exception as exc:
                logging.error("Error leyendo cola del análisis %s: %s", analysis_id, exc, exc_info=True)
                await finish_repo_analysis(
                    context,
                    analysis_id,
                    {
                        'status': 'failed',
                        'error_message': (
                            "No pude continuar el análisis del repositorio. Intenta de nuevo más tarde."
                        ),
                    },
                )
                break

            if event.get('type') == 'progress':
                await update_repo_status_message(
                    context.bot,
                    state,
                    event.get('text', '⏳ Analizando el repositorio...'),
                    reply_markup=build_repo_cancel_markup(analysis_id),
                )
            elif event.get('type') == 'result':
                await finish_repo_analysis(context, analysis_id, event)
                break

        current_state = get_active_repo_analyses(context.application).get(analysis_id)
        if not current_state:
            continue

        process = current_state.get('process')
        if process and not process.is_alive() and current_state.get('status') != 'running':
            remove_repo_analysis_state(context.application, analysis_id, stop_process=True)
        elif process and not process.is_alive():
            await finish_repo_analysis(
                context,
                analysis_id,
                {
                    'status': 'failed',
                    'error_message': (
                        "El análisis del repositorio terminó de forma inesperada. Intenta de nuevo."
                    ),
                },
            )


def extract_youtube_url(text: str):
    """Detecta y extrae una URL de YouTube de un texto."""
    if not text:
        return None

    pattern = r'https?://(?:www\.)?(?:youtube\.com/watch\?(?:[^\s]*&)?v=[\w-]+|youtu\.be/[\w-]+)(?:\S*)?'
    match = re.search(pattern, text)
    if not match:
        return None

    youtube_url = match.group(0)
    return youtube_url if extract_youtube_video_id(youtube_url) else None


def build_webapp_url(**params):
    if not WEBAPP_URL:
        return None

    parsed_url = urllib.parse.urlsplit(WEBAPP_URL)
    merged_params = dict(urllib.parse.parse_qsl(parsed_url.query, keep_blank_values=True))

    for key, value in params.items():
        if value is not None:
            merged_params[key] = str(value)

    return urllib.parse.urlunsplit((
        parsed_url.scheme,
        parsed_url.netloc,
        parsed_url.path,
        urllib.parse.urlencode(merged_params),
        parsed_url.fragment,
    ))


if not WEBAPP_URL:
    logging.warning("No PUBLIC_WEBAPP_URL/WEBAPP_URL configured; Telegram WebApp buttons will be disabled")


def parse_note_category_and_content(raw_text):
    """Parsea el formato `/nota categoria | contenido` de forma retrocompatible."""
    content = (raw_text or '').strip()
    if not content:
        return None, ''

    if '|' not in content:
        return None, content

    raw_category, raw_content = content.split('|', 1)
    category = normalize_note_category(raw_category)
    parsed_content = raw_content.strip()

    if category and parsed_content:
        return category, parsed_content

    return None, content


def sanitize_history_for_model(history):
    """Convierte entradas complejas del historial a texto estable para el modelo."""
    sanitized = []

    for entry in history or []:
        if not isinstance(entry, dict):
            continue

        role = entry.get("role", "")
        content = entry.get("content", "")

        if role == "assistant" and isinstance(content, str):
            stripped = content.strip()
            if stripped.startswith("{") and stripped.endswith("}"):
                try:
                    parsed = json.loads(stripped)
                    action = parsed.get("action", "")
                    reply = parsed.get("reply", "")

                    if action == "ALERT":
                        content = (
                            f"[Alerta enviada - ID: {parsed.get('id', '?')} | "
                            f"Mensaje: {parsed.get('message', '')}]"
                        )
                    elif action in ("VIDEO_ANALYSIS", "YOUTUBE_ANALYSIS", "REPO_ANALYSIS"):
                        summary = str(parsed.get("summary", "") or reply or "")[:300]
                        content = (
                            f"[Analisis completado: {parsed.get('url', '')}. "
                            f"Resumen: {summary}]"
                        )
                    elif reply:
                        content = str(reply)
                    else:
                        content = f"[Accion realizada: {action or 'UNKNOWN'}]"
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass

        if not isinstance(content, str):
            try:
                content = json.dumps(content, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                content = str(content)

        sanitized.append({"role": role, "content": content})

    return sanitized


def build_user_brain_error_message(failure):
    failure = failure or {}
    failure_kind = failure.get("kind", "")
    failure_status = failure.get("status_code")

    if failure_kind == "missing_api_key":
        return "Error de configuracion del bot. Contacta al administrador."
    if failure_status in (401, 403):
        return "Hay un problema de autenticacion con el servicio de IA. Contacta al administrador."
    if failure_status == 402:
        return "El servicio de IA no tiene creditos disponibles en este momento. Intenta mas tarde."
    if failure_kind == "timeout":
        return "El servicio de IA tardo demasiado en responder. Intenta de nuevo en unos segundos."
    if failure_kind == "network_error":
        return "Hay un problema de conexion con el servicio de IA. Intenta de nuevo en un momento."
    if failure_status == 429:
        return "El servicio de IA esta temporalmente saturado. Espera unos segundos e intenta de nuevo."
    if failure_status == 503:
        return "El servicio de IA no esta disponible temporalmente. Intenta de nuevo en unos segundos."
    if failure_kind == "missing_dependency":
        return "Falta una dependencia interna del servicio de IA. Contacta al administrador."
    if failure_kind in ("invalid_json_response", "invalid_openrouter_payload", "invalid_provider_payload"):
        return "No pude interpretar la respuesta del servicio de IA. Intenta reformular tu mensaje."
    return "Lo siento, tuve un problema temporal con mi conexion cerebral. Intenta de nuevo en unos segundos."


def get_ai_capability_label(capability):
    return AI_CAPABILITY_LABELS.get(capability, capability.title())


def get_ai_provider_label(provider):
    return AI_PROVIDER_LABELS.get(provider, provider.title())


def truncate_ai_model_name(model_name, limit=42):
    if len(model_name) <= limit:
        return model_name
    return f"{model_name[:limit - 1]}…"


def clear_pending_ai_model_entry(context):
    context.user_data.pop(AI_PENDING_MODEL_KEY, None)


def seed_ai_catalog_defaults():
    ensure_default_ai_settings(get_default_ai_settings())
    save_ai_model('nvidia', AI_TEXT_CAPABILITY, DEFAULT_NVIDIA_TEXT_MODEL)


def build_ai_status_text(notice=None):
    configs = get_all_ai_configurations()
    text_config = configs[AI_TEXT_CAPABILITY]
    vision_config = configs[AI_VISION_CAPABILITY]

    lines = [
        "⚙️ Configuración global de IA",
        "",
        f"Texto: {get_ai_provider_label(text_config['provider'])} · {text_config['model_name']}",
        f"Visión: {get_ai_provider_label(vision_config['provider'])} · {vision_config['model_name']}",
    ]

    if notice:
        lines.extend(["", notice])

    return "\n".join(lines)


def build_ai_main_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Configurar texto", callback_data=f"{AI_CALLBACK_PREFIX}scope:{AI_TEXT_CAPABILITY}")],
        [InlineKeyboardButton("🖼️ Configurar visión", callback_data=f"{AI_CALLBACK_PREFIX}scope:{AI_VISION_CAPABILITY}")],
        [InlineKeyboardButton("🔄 Actualizar", callback_data=f"{AI_CALLBACK_PREFIX}menu")],
    ])


def build_ai_capability_text(capability, notice=None):
    configs = get_all_ai_configurations()
    config = configs[capability]
    lines = [
        f"⚙️ Configuración de {get_ai_capability_label(capability)}",
        "",
        f"Proveedor activo: {get_ai_provider_label(config['provider'])}",
        f"Modelo activo: {config['model_name']}",
        "",
        "Elige el proveedor para ver y activar modelos guardados.",
    ]

    if notice:
        lines.extend(["", notice])

    return "\n".join(lines)


def build_ai_capability_markup(capability):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("OpenRouter", callback_data=f"{AI_CALLBACK_PREFIX}provider:{capability}:openrouter"),
            InlineKeyboardButton("Nvidia", callback_data=f"{AI_CALLBACK_PREFIX}provider:{capability}:nvidia"),
        ],
        [InlineKeyboardButton("🏠 Menú", callback_data=f"{AI_CALLBACK_PREFIX}menu")],
    ])


def build_ai_model_picker_text(capability, provider, notice=None):
    configs = get_all_ai_configurations()
    current_config = configs[capability]
    saved_models = get_saved_ai_models(capability=capability, provider=provider, limit=20)

    lines = [
        f"{get_ai_capability_label(capability)} · {get_ai_provider_label(provider)}",
        "",
        f"Activo ahora: {get_ai_provider_label(current_config['provider'])} · {current_config['model_name']}",
    ]

    if saved_models:
        lines.append(f"Modelos guardados: {len(saved_models)}")
    else:
        lines.append("Todavía no hay modelos guardados para este proveedor.")

    lines.append("Pulsa un modelo para activarlo o agrega uno manualmente.")

    if notice:
        lines.extend(["", notice])

    return "\n".join(lines)


def build_ai_model_picker_markup(capability, provider):
    configs = get_all_ai_configurations()
    current_config = configs[capability]
    saved_models = get_saved_ai_models(capability=capability, provider=provider, limit=20)
    rows = []

    for model in saved_models:
        is_active = (
            current_config['provider'] == provider
            and current_config['model_name'] == model['model_name']
        )
        prefix = "✅ " if is_active else ""
        rows.append([
            InlineKeyboardButton(
                f"{prefix}{truncate_ai_model_name(model['model_name'])}",
                callback_data=f"{AI_CALLBACK_PREFIX}activate:{capability}:{model['id']}",
            )
        ])

    rows.append([
        InlineKeyboardButton(
            "➕ Agregar modelo",
            callback_data=f"{AI_CALLBACK_PREFIX}add:{capability}:{provider}",
        )
    ])
    rows.append([
        InlineKeyboardButton("⬅️ Proveedores", callback_data=f"{AI_CALLBACK_PREFIX}scope:{capability}"),
        InlineKeyboardButton("🏠 Menú", callback_data=f"{AI_CALLBACK_PREFIX}menu"),
    ])
    return InlineKeyboardMarkup(rows)


async def send_ai_screen(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup=None):
    if update.callback_query:
        await query_safe_edit_message(update, context, text, reply_markup=reply_markup)
        return

    await update.effective_message.reply_text(text, reply_markup=reply_markup)


async def show_ai_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, notice=None):
    await send_ai_screen(
        update,
        context,
        build_ai_status_text(notice=notice),
        reply_markup=build_ai_main_markup(),
    )


async def show_ai_capability_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, capability: str, notice=None):
    await send_ai_screen(
        update,
        context,
        build_ai_capability_text(capability, notice=notice),
        reply_markup=build_ai_capability_markup(capability),
    )


async def show_ai_model_picker(update: Update, context: ContextTypes.DEFAULT_TYPE, capability: str, provider: str, notice=None):
    await send_ai_screen(
        update,
        context,
        build_ai_model_picker_text(capability, provider, notice=notice),
        reply_markup=build_ai_model_picker_markup(capability, provider),
    )


def build_ai_pending_input_markup(capability, provider):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Volver", callback_data=f"{AI_CALLBACK_PREFIX}provider:{capability}:{provider}")],
        [InlineKeyboardButton("🏠 Menú", callback_data=f"{AI_CALLBACK_PREFIX}menu")],
    ])


async def ai_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    seed_ai_catalog_defaults()

    clear_pending_ai_model_entry(context)
    await show_ai_main_menu(update, context)


async def handle_pending_ai_model_entry(update: Update, context: ContextTypes.DEFAULT_TYPE, user_text: str, user_id: int):
    pending = context.user_data.get(AI_PENDING_MODEL_KEY)
    if not pending:
        return False

    model_name = (user_text or '').strip()
    capability = pending['capability']
    provider = pending['provider']

    if not model_name:
        await update.effective_message.reply_text(
            "Escríbeme el nombre exacto del modelo o envía 'cancelar'."
        )
        return True

    if model_name.lower() in {'cancelar', '/cancelar'}:
        clear_pending_ai_model_entry(context)
        await show_ai_model_picker(update, context, capability, provider, notice="Operación cancelada.")
        return True

    try:
        save_ai_model(provider, capability, model_name)
        activate_ai_model(capability, provider, model_name)
    except ValueError as exc:
        await update.effective_message.reply_text(f"❌ {exc}")
        return True

    clear_pending_ai_model_entry(context)
    await show_ai_model_picker(
        update,
        context,
        capability,
        provider,
        notice=(
            f"✅ {get_ai_capability_label(capability)} ahora usa "
            f"{get_ai_provider_label(provider)} · {model_name}"
        ),
    )
    return True


async def ai_settings_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = (query.data or '').split(':')
    if len(parts) < 2:
        await query.answer("Acción no válida.", show_alert=True)
        return

    action = parts[1]
    if action != 'add':
        clear_pending_ai_model_entry(context)

    if action == 'menu':
        await show_ai_main_menu(update, context)
        return

    if action == 'scope' and len(parts) == 3:
        await show_ai_capability_menu(update, context, parts[2])
        return

    if action == 'provider' and len(parts) == 4:
        await show_ai_model_picker(update, context, parts[2], parts[3])
        return

    if action == 'activate' and len(parts) == 4:
        capability = parts[2]
        model = get_ai_model_by_id(parts[3])
        if not model:
            await query.answer("Ese modelo ya no existe.", show_alert=True)
            await show_ai_capability_menu(update, context, capability, notice="El modelo seleccionado ya no está disponible.")
            return

        if model['capability'] != capability:
            await query.answer("El modelo no corresponde a esa capacidad.", show_alert=True)
            return

        activate_ai_model(capability, model['provider'], model['model_name'])
        await show_ai_model_picker(
            update,
            context,
            capability,
            model['provider'],
            notice=(
                f"✅ {get_ai_capability_label(capability)} ahora usa "
                f"{get_ai_provider_label(model['provider'])} · {model['model_name']}"
            ),
        )
        return

    if action == 'add' and len(parts) == 4:
        capability = parts[2]
        provider = parts[3]
        context.user_data[AI_PENDING_MODEL_KEY] = {
            'capability': capability,
            'provider': provider,
        }
        await send_ai_screen(
            update,
            context,
            (
                f"Envía el nombre exacto del modelo para {get_ai_capability_label(capability)} en "
                f"{get_ai_provider_label(provider)}.\n\n"
                "El siguiente mensaje de texto se guardará en el catálogo y quedará activo. "
                "Si quieres cancelar, envía 'cancelar'."
            ),
            reply_markup=build_ai_pending_input_markup(capability, provider),
        )
        return

    await query.answer("Acción no reconocida.", show_alert=True)


def get_provider_env_key(provider):
    return 'NVIDIA_API_KEY' if provider == 'nvidia' else 'OPENROUTER_API_KEY'


def validate_ai_configuration():
    """Valida la configuración activa de IA al iniciar el bot."""
    configs = get_all_ai_configurations()
    text_config = configs[AI_TEXT_CAPABILITY]
    vision_config = configs[AI_VISION_CAPABILITY]

    logging.info(
        "Configuración IA activa | texto=%s/%s | vision=%s/%s",
        text_config['provider'],
        text_config['model_name'],
        vision_config['provider'],
        vision_config['model_name'],
    )

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

    validation_text = request_ai_text(
        [{"role": "user", "content": "di solo: ok"}],
        timeout=15,
        max_tokens=5,
        log_context="startup/text_validation",
        capability=AI_TEXT_CAPABILITY,
    )
    if validation_text is not None:
        logging.info("Conexión con el proveedor activo de texto verificada correctamente")
        return True

    failure = get_last_brain_failure() or {}
    status_code = failure.get('status_code')
    if failure.get('kind') in {'timeout', 'network_error'} or status_code in {429, 503}:
        logging.warning(
            "No se pudo validar en caliente el proveedor activo de texto, pero el bot continuará. failure=%s",
            json.dumps(failure, ensure_ascii=False, default=str),
        )
        return True

    logging.error(
        "Falló la validación del proveedor activo de texto. failure=%s",
        json.dumps(failure, ensure_ascii=False, default=str),
    )
    return False

# --- REVISOR DE RECORDATORIOS (Bogotá Time) ---
async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    tz_bogota = pytz.timezone('America/Bogota')
    now = datetime.now(tz_bogota)
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    
    conn = get_connection()
    cursor = conn.cursor()
    
    # Buscamos tareas pendientes cuya fecha ya pasó - INCLUIMOS image_file_id
    cursor.execute('SELECT id, user_id, message, recurrence, image_file_id FROM reminders WHERE remind_at <= ? AND status = "pending"', (now_str,))
    due_reminders = cursor.fetchall()
    
    for rem in due_reminders:
        rem_id, user_id, msg, recurrence, image_file_id = rem
        try:
            alert_text = f"⏰ ¡ALERTA (ID: {rem_id})!:\n📌 {msg}"
            
            # Botón para abrir la Web App de reprogramación
            webapp_url_with_params = build_webapp_url(user_id=user_id, id=rem_id, message=msg)
            if webapp_url_with_params:
                keyboard = [[InlineKeyboardButton("⏳ Reprogramar", web_app=WebAppInfo(url=webapp_url_with_params))]]
                reply_markup = InlineKeyboardMarkup(keyboard)
            else:
                reply_markup = None

            # ENVÍO DE ALERTA: Si hay imagen, enviar foto; si no, enviar mensaje
            if image_file_id:
                await context.bot.send_photo(
                    chat_id=user_id,
                    photo=image_file_id,
                    caption=alert_text,
                    reply_markup=reply_markup
                )
                logging.info(f"Alerta con foto enviada al usuario {user_id} para recordatorio {rem_id}")
            else:
                await context.bot.send_message(
                    chat_id=user_id, 
                    text=alert_text,
                    reply_markup=reply_markup
                )
                logging.info(f"Alerta de texto enviada al usuario {user_id} para recordatorio {rem_id}")
            
            if recurrence:
                # Calcular la siguiente ocurrencia
                try:
                    # Parsear la regla RRULE
                    rule = rrule.rrulestr(recurrence, dtstart=now.replace(tzinfo=None))
                    next_occurrence = rule.after(now.replace(tzinfo=None))
                    
                    if next_occurrence:
                        new_date_str = next_occurrence.strftime("%Y-%m-%d %H:%M:%S")
                        cursor.execute('UPDATE reminders SET remind_at = ? WHERE id = ?', (new_date_str, rem_id))
                        logging.info(f"Recordatorio recurrente {rem_id} reprogramado para {new_date_str}")
                    else:
                        cursor.execute('UPDATE reminders SET status = "sent" WHERE id = ?', (rem_id,))
                except Exception as ex:
                    logging.error(f"Error calculando recurrencia para {rem_id}: {ex}")
                    cursor.execute('UPDATE reminders SET status = "sent" WHERE id = ?', (rem_id,))
            else:
                cursor.execute('UPDATE reminders SET status = "sent" WHERE id = ?', (rem_id,))
            
            # Registrar el alerta en el historial del usuario para que la IA tenga contexto
            # Usamos context.application.user_data para acceder al historial fuera de un MessageHandler
            if user_id in context.application.user_data:
                user_data = context.application.user_data[user_id]
                if 'history' not in user_data:
                    user_data['history'] = []
                
                # Agregamos un mensaje ficticio del asistente que describe la alerta
                # Esto permite que la IA vea el ID y el mensaje enviado en el historial
                user_data['history'].append({
                    "role": "assistant", 
                    "content": json.dumps({
                        "action": "ALERT", 
                        "id": rem_id, 
                        "message": msg,
                        "reply": alert_text
                    }, ensure_ascii=False)
                })
                
                # Limitar historial
                if len(user_data['history']) > 16:
                    user_data['history'] = user_data['history'][-16:]
                    
        except Exception as e:
            logging.error(f"Error enviando mensaje: {e}")
            
    conn.commit()
    conn.close()

# --- JOB: RESUMEN DIARIO ---
async def send_daily_summaries(context: ContextTypes.DEFAULT_TYPE):
    """Envía el listado de recordatorios de hoy a los usuarios con la función activa."""
    users = get_users_with_daily_summary()
    tz_bogota = pytz.timezone('America/Bogota')
    now = datetime.now(tz_bogota)
    
    # El job se programa para Mon-Fri, pero validamos por si acaso
    if now.weekday() >= 5: # 5=Saturday, 6=Sunday
        return

    for user_id, summary_time in users:
        try:
            reminders = get_today_reminders(user_id)
            if not reminders:
                msg = "☀️ ¡Buenos días! Para hoy no tienes recordatorios programados. ¡Que tengas un excelente día!"
            else:
                msg = "☀️ *¡Buenos días! Aquí tienes tus recordatorios para hoy:*\n\n"
                for r_id, r_msg, r_time in reminders:
                    # Formatear la hora
                    dt = datetime.strptime(r_time, "%Y-%m-%d %H:%M:%S")
                    time_str = dt.strftime("%H:%M")
                    msg += f"• `{time_str}`: {r_msg}\n"
            
            await context.bot.send_message(chat_id=user_id, text=msg, parse_mode="Markdown")
            logging.info(f"Resumen diario enviado a usuario {user_id}")
            
        except Exception as e:
            logging.error(f"Error enviando resumen diario a {user_id}: {e}")

# --- FUNCIÓN AUXILIAR: Descargar imagen de Telegram y convertir a base64 ---
async def download_telegram_file_to_base64(bot, file_id: str, mime_type: str | None = None) -> str | None:
    """Descarga un archivo de imagen de Telegram y lo retorna en formato data URI."""
    try:
        file = await bot.get_file(file_id)
        image_content = await file.download_as_bytearray()

        resolved_mime_type = mime_type or 'image/jpeg'
        image_base64 = base64.b64encode(image_content).decode('utf-8')

        return f"data:{resolved_mime_type};base64,{image_base64}"
    except Exception as e:
        logging.error(f"Error descargando imagen {file_id}: {e}")
        return None


async def download_image_to_base64(update: Update) -> str | None:
    """Descarga la imagen del mensaje actual y la convierte a base64."""
    photo = update.message.photo[-1] if update.message.photo else None
    mime_type = 'image/jpeg'

    if not photo:
        document = update.message.document
        if not document or not document.mime_type or not document.mime_type.startswith('image/'):
            return None

        photo = document
        mime_type = document.mime_type or 'image/jpeg'

    return await download_telegram_file_to_base64(update.message.bot, photo.file_id, mime_type=mime_type)

# --- PROCESAMIENTO DE VIDEOS DE X.COM ---
async def process_x_video(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, user_text: str):
    """Pipeline completo: descarga audio → transcribe → analiza → responde.
    
    Args:
        update: Update de Telegram
        context: Contexto del bot
        url: URL del tweet/post de X.com
        user_text: Texto completo del mensaje del usuario
    """
    user_id = update.effective_user.id
    audio_path = None
    
    logging.info(f"Procesando video de X.com para usuario {user_id}: {url}")
    
    # Inicializar historial si no existe
    if 'history' not in context.user_data:
        context.user_data['history'] = []
    
    history = context.user_data.get('history', [])
    
    try:
        # ── PASO 1: Descargar audio ──
        status_msg = await update.effective_message.reply_text("⏳ Descargando audio del video de X...")
        
        audio_result, info_or_error = download_audio(url)
        
        if audio_result is None:
            # info_or_error contiene el mensaje de error
            await status_msg.edit_text(f"❌ {info_or_error}")
            return
        
        audio_path = audio_result
        video_info = info_or_error  # En caso de éxito, es el dict con info del video
        
        # Mostrar info del video si está disponible
        duration_str = ""
        if video_info.get('duration'):
            mins = int(video_info['duration']) // 60
            secs = int(video_info['duration']) % 60
            duration_str = f" ({mins}:{secs:02d})"
        
        # ── PASO 2: Transcribir audio ──
        await status_msg.edit_text(f"🎙️ Transcribiendo audio{duration_str}...")
        
        transcript, error = transcribe_audio(audio_path)
        
        if transcript is None:
            await status_msg.edit_text(f"❌ {error}")
            return
        
        # ── PASO 3: Analizar con LLM ──
        await status_msg.edit_text("🧠 Analizando contenido del video...")
        
        # Extraer instrucción del usuario (quitar la URL del texto)
        user_instruction = user_text.replace(url, '').strip()
        # Limpiar instrucciones vacías o solo con espacios/signos
        if user_instruction and len(user_instruction.strip('., ')) < 3:
            user_instruction = None
        
        summary = process_video_summary(transcript, user_instruction or None, history)
        
        # ── PASO 4: Enviar resultado ──
        await status_msg.delete()
        
        if summary:
            # Construir respuesta final
            header = "🎬 **Análisis del video de X.com**\n"
            if video_info.get('uploader') and video_info['uploader'] != 'Desconocido':
                header += f"👤 _{video_info['uploader']}_"
                if duration_str:
                    header += f" • ⏱️ {duration_str.strip(' ()')}"
                header += "\n"
            header += "\n"
            
            response_text = header + summary
        else:
            # Fallback: si el LLM falla, enviar la transcripción directamente
            response_text = (
                "⚠️ No pude generar el análisis automático. "
                "Aquí tienes la transcripción del video:\n\n"
                f"📝 _{transcript[:3000]}_"
            )
            if len(transcript) > 3000:
                response_text += "\n\n_(Transcripción truncada)_"
        
        # Enviar respuesta (manejar mensajes largos)
        if len(response_text) > 4096:
            # Dividir en chunks respetando el límite de Telegram
            chunks = split_message(response_text, 4096)
            for chunk in chunks:
                await update.effective_message.reply_text(chunk, parse_mode="Markdown")
        else:
            try:
                await update.effective_message.reply_text(response_text, parse_mode="Markdown")
            except Exception:
                # Si falla el Markdown, enviar sin formato
                await update.effective_message.reply_text(response_text.replace('*', '').replace('_', ''))
        
        # ── Actualizar historial de conversación ──
        # Guardar contexto del video para que el usuario pueda hacer preguntas de seguimiento
        context.user_data['history'].append({
            "role": "user", 
            "content": f"[Compartí un video de X.com: {url}]. {user_instruction or 'Analízalo.'}"
        })
        
        # Guardar el transcript en el historial (truncado) para preguntas de seguimiento
        assistant_context = {
            "action": "VIDEO_ANALYSIS",
            "url": url,
            "transcript": transcript[:2000],  # Truncar para no llenar el historial
            "summary": (summary or "No disponible")[:1000],
            "reply": summary or "Transcripción enviada directamente."
        }
        context.user_data['history'].append({
            "role": "assistant", 
            "content": json.dumps(assistant_context, ensure_ascii=False)
        })
        
        # Podar historial
        max_history_length = 16
        if len(context.user_data['history']) > max_history_length:
            context.user_data['history'] = context.user_data['history'][-max_history_length:]
        
        logging.info(f"Video de X.com procesado exitosamente para usuario {user_id}")
        
    except Exception as e:
        logging.error(f"Error procesando video de X.com para usuario {user_id}: {e}", exc_info=True)
        if update.effective_message:
            try:
                await update.effective_message.reply_text(
                    "❌ Hubo un error inesperado procesando el video. Intenta de nuevo."
                )
            except Exception:
                pass
    
    finally:
        # Siempre limpiar archivos temporales
        if audio_path:
            cleanup_audio(audio_path)


async def process_youtube_video(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, user_text: str):
    """Pipeline: obtiene transcripcion de YouTube, la analiza y responde al usuario."""
    user_id = update.effective_user.id

    logging.info(f"Procesando video de YouTube para usuario {user_id}: {url}")

    if 'history' not in context.user_data:
        context.user_data['history'] = []

    history = context.user_data.get('history', [])

    try:
        video_id = extract_youtube_video_id(url)
        if not video_id:
            await update.effective_message.reply_text("❌ No pude extraer el ID del video desde la URL proporcionada.")
            return

        status_msg = await update.effective_message.reply_text(
            "🔎 Consultando subtitulos disponibles en YouTube..."
        )

        languages, error = fetch_youtube_available_languages(video_id)
        if not languages:
            await status_msg.edit_text(f"❌ {error}")
            return

        selected_lang = select_youtube_transcript_language(languages)
        if not selected_lang:
            await status_msg.edit_text("❌ No pude determinar automaticamente el idioma de los subtitulos.")
            return

        await status_msg.edit_text(
            f"📄 Obteniendo transcripcion del video de YouTube en {selected_lang}..."
        )

        transcript, error = fetch_youtube_transcript_by_lang(url, selected_lang)
        if transcript is None:
            transcript, error = get_youtube_transcript(url, languages=languages)

        if transcript is None:
            await status_msg.edit_text(f"❌ {error}")
            return

        await status_msg.edit_text("🧠 Analizando el contenido del video...")

        user_instruction = user_text.replace(url, '').strip() or None
        if user_instruction and len(user_instruction.strip('., ')) < 3:
            user_instruction = None

        summary = process_video_summary(transcript, user_instruction, history, video_source="YouTube")

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

        context.user_data['history'].append({
            "role": "user",
            "content": f"[Comparti un video de YouTube: {url}]. {user_instruction or 'Analizalo.'}"
        })
        context.user_data['history'].append({
            "role": "assistant",
            "content": json.dumps({
                "action": "YOUTUBE_ANALYSIS",
                "url": url,
                "language": selected_lang,
                "transcript": transcript[:2000],
                "summary": (summary or "")[:1000],
                "reply": summary or "Transcripcion enviada directamente."
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


async def process_github_repository(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, user_text: str):
    """Inicia un análisis de repositorio GitHub en background y lo deja cancelable."""
    user_id = update.effective_user.id
    application = context.application
    active_by_user = get_active_repo_analysis_by_user(application)
    existing_analysis_id = active_by_user.get(user_id)

    if existing_analysis_id:
        existing_state = get_active_repo_analyses(application).get(existing_analysis_id)
        if existing_state:
            await query_safe_edit_message(
                update,
                context,
                (
                    "⏳ Ya tienes un análisis de repositorio en curso. "
                    "Cancélalo primero o espera a que termine."
                ),
                reply_markup=build_repo_cancel_markup(existing_analysis_id),
            )
            return

        active_by_user.pop(user_id, None)

    analysis_id = uuid.uuid4().hex[:12]
    progress_queue = multiprocessing.Queue()
    history = list(context.user_data.get('history', []))
    status_message = update.callback_query.message
    state = {
        'analysis_id': analysis_id,
        'user_id': user_id,
        'chat_id': update.effective_chat.id,
        'status_message_id': status_message.message_id,
        'url': url,
        'user_text': user_text or '',
        'queue': progress_queue,
        'status': 'running',
        'last_status_text': None,
        'last_status_has_markup': False,
    }

    process = multiprocessing.Process(
        target=run_repository_analysis_worker,
        args=(url, history, progress_queue),
        daemon=True,
    )
    state['process'] = process

    get_active_repo_analyses(application)[analysis_id] = state
    active_by_user[user_id] = analysis_id

    try:
        process.start()
    except Exception:
        remove_repo_analysis_state(application, analysis_id, stop_process=True)
        await query_safe_edit_message(
            update,
            context,
            "❌ No pude iniciar el análisis del repositorio en este momento. Intenta de nuevo más tarde.",
        )
        logging.error("No se pudo iniciar el proceso de análisis GitHub %s", analysis_id, exc_info=True)
        return

    logging.info("Análisis GitHub %s iniciado para usuario %s: %s", analysis_id, user_id, url)

    await update_repo_status_message(
        context.bot,
        state,
        "⏳ Obteniendo el código del repositorio con GitIngest...",
        reply_markup=build_repo_cancel_markup(analysis_id),
    )


async def query_safe_edit_message(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup=None):
    query = update.callback_query
    try:
        await query.edit_message_text(text, reply_markup=reply_markup)
    except Exception:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=text,
            reply_markup=reply_markup,
        )


async def cancel_github_repository_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE, analysis_id: str):
    state = get_active_repo_analyses(context.application).get(analysis_id)
    if not state:
        await query_safe_edit_message(
            update,
            context,
            "⚠️ Ese análisis ya no está activo.",
        )
        return

    if state['user_id'] != update.effective_user.id:
        await query_safe_edit_message(
            update,
            context,
            "❌ No puedes cancelar el análisis de otro usuario.",
            reply_markup=build_repo_cancel_markup(analysis_id),
        )
        return

    state = remove_repo_analysis_state(context.application, analysis_id, stop_process=True)
    if not state:
        await query_safe_edit_message(
            update,
            context,
            "⚠️ Ese análisis ya no está activo.",
        )
        return

    state['status'] = 'cancelled'
    await update_repo_status_message(
        context.bot,
        state,
        "❌ Análisis del repositorio cancelado por el usuario.",
    )
    logging.info("Análisis GitHub %s cancelado por usuario %s", analysis_id, state['user_id'])


def split_message(text, max_length=4096):
    """Divide un texto largo en chunks que respeten el límite de Telegram.
    
    Intenta cortar en saltos de línea para no romper oraciones.
    """
    if len(text) <= max_length:
        return [text]
    
    chunks = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break
        
        # Buscar el último salto de línea dentro del límite
        cut_point = text.rfind('\n', 0, max_length)
        if cut_point == -1 or cut_point < max_length // 2:
            # Si no hay salto de línea conveniente, cortar en espacio
            cut_point = text.rfind(' ', 0, max_length)
        if cut_point == -1:
            cut_point = max_length
        
        chunks.append(text[:cut_point])
        text = text[cut_point:].lstrip()
    
    return chunks

# --- MANEJADOR DE MENSAJES ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Intentar obtener el texto del mensaje o de la leyenda (caption) de una imagen/archivo
    user_text = update.message.text or update.message.caption
    user_id = update.effective_user.id

    if context.user_data.get(AI_PENDING_MODEL_KEY):
        if not user_text:
            await update.effective_message.reply_text(
                "Estoy esperando el nombre exacto del modelo en texto o la palabra 'cancelar'."
            )
            return

        if await handle_pending_ai_model_entry(update, context, user_text, user_id):
            return
    
    if user_text:
        youtube_url = extract_youtube_url(user_text)
        if youtube_url:
            logging.info(f"Enlace de YouTube detectado para usuario {user_id}: {youtube_url}")

            msg_id = update.message.message_id
            if 'youtube_urls' not in context.user_data:
                context.user_data['youtube_urls'] = {}
            context.user_data['youtube_urls'][str(msg_id)] = {
                'url': youtube_url,
                'text': user_text,
            }

            keyboard = [
                [
                    InlineKeyboardButton("🎬 Analizar Video", callback_data=f"yt_analyze:{msg_id}"),
                    InlineKeyboardButton("⏰ Recordatorio", callback_data=f"yt_reminder:{msg_id}"),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.message.reply_text(
                "📺 Detecté un enlace de YouTube. ¿Qué deseas hacer?",
                reply_markup=reply_markup,
            )
            return

        github_url = extract_github_repo_url(user_text)
        if github_url:
            logging.info(f"Enlace de GitHub detectado para usuario {user_id}: {github_url}")

            msg_id = update.message.message_id
            if 'github_urls' not in context.user_data:
                context.user_data['github_urls'] = {}
            context.user_data['github_urls'][str(msg_id)] = {
                'url': github_url,
                'text': user_text
            }

            keyboard = [
                [
                    InlineKeyboardButton("🧠 Analizar repositorio", callback_data=f"gh_analyze:{msg_id}"),
                    InlineKeyboardButton("⏰ Recordatorio", callback_data=f"gh_reminder:{msg_id}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.message.reply_text(
                "🐙 He detectado un repositorio de GitHub. ¿Qué te gustaría hacer?",
                reply_markup=reply_markup
            )
            return

        x_url = extract_x_url(user_text)
        if x_url:
            logging.info(f"Enlace de Twitter/X detectado para usuario {user_id}: {x_url}")
            
            # Guardar la URL para usarla en el callback (callback_data tiene límite de 64 bytes)
            # Usamos un ID de mensaje o timestamp para evitar colisiones
            msg_id = update.message.message_id
            if 'x_urls' not in context.user_data:
                context.user_data['x_urls'] = {}
            context.user_data['x_urls'][str(msg_id)] = {
                'url': x_url,
                'text': user_text
            }
            
            keyboard = [
                [
                    InlineKeyboardButton("🎬 Analizar Video", callback_data=f"x_video:{msg_id}"),
                    InlineKeyboardButton("⏰ Recordatorio", callback_data=f"x_reminder:{msg_id}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                "🐦 He detectado un enlace de Twitter/X. ¿Qué te gustaría hacer?",
                reply_markup=reply_markup
            )
            return

    await process_normal_message(update, context, user_text, user_id)

async def process_normal_message(update: Update, context: ContextTypes.DEFAULT_TYPE, user_text: str, user_id: int):
    """Procesamiento normal de mensajes (IA, recordatorios, notas, etc.)"""
    
    # Verificar si hay una imagen adjunta
    msg = update.effective_message
    has_image = msg and (msg.photo or (msg.document and msg.document.mime_type and msg.document.mime_type.startswith('image/')))
    
    if not user_text and not has_image:
        # Si no hay texto ni imagen, salir
        return
    
    # 1. CAPTURA DE IMAGEN: Guardar el file_id sin enviar a IA
    if has_image:
        # Obtener el file_id de la foto (tomamos la de mayor resolución)
        if msg.photo:
            photo_id = msg.photo[-1].file_id
            image_mime_type = 'image/jpeg'
        else:
            photo_id = msg.document.file_id
            image_mime_type = msg.document.mime_type or 'image/jpeg'
        
        context.user_data['pending_image_id'] = photo_id
        context.user_data['pending_image_mime_type'] = image_mime_type
        logging.info(f"Imagen recibida para usuario {user_id}: {photo_id}")
        
        # Si no hay caption de texto, solicitar que escriba qué quiere hacer
        if not user_text:
            await msg.reply_text("📸 Imagen recibida y guardada. Ahora escríbeme qué quieres hacer con ella.\n\nEjemplo: 'Recuérdame esto mañana a las 8am'")
            return
    
    # 2. RECUPERAR IMAGE_ID PENDIENTE (si existe de un mensaje anterior)
    image_to_save = context.user_data.get('pending_image_id')
    image_mime_type = context.user_data.get('pending_image_mime_type') or 'image/jpeg'
    
    logging.info(f"Mensaje recibido de usuario {user_id}: {user_text} (con imagen_id: {bool(image_to_save)})")
    
    # Usar el chat efectivo para acciones que no dependen de un mensaje específico
    chat = update.effective_chat
    await chat.send_action("typing")
    
    # Inicializar historial si no existe
    if 'history' not in context.user_data:
        context.user_data['history'] = []
        logging.info(f"Historial inicializado para usuario {user_id}")
    
    # Recuperar historial de conversación del usuario
    raw_history = context.user_data.get('history', [])
    history = sanitize_history_for_model(raw_history)
    
    # Obtener recordatorios activos para contexto
    active_reminders = get_user_reminders(user_id)
    
    # 3. PROCESAR TEXTO (si hay imagen pendiente, enriquecer el texto para que la IA lo sepa)
    text_to_process = user_text
    if image_to_save:
        text_to_process = f"{user_text}\n[📸 El usuario adjuntó una imagen a este mensaje]"
    
    try:
        if image_to_save:
            image_base64 = await download_telegram_file_to_base64(
                update.effective_bot,
                image_to_save,
                mime_type=image_mime_type,
            )
            if image_base64:
                res = process_vision_input(
                    text_to_process,
                    image_base64,
                    history=history,
                    active_reminders=active_reminders,
                )
            else:
                logging.warning(
                    "No se pudo descargar la imagen pendiente %s para el usuario %s; usando fallback de texto",
                    image_to_save,
                    user_id,
                )
                res = process_user_input(text_to_process, history=history, active_reminders=active_reminders)
        else:
            res = process_user_input(text_to_process, history=history, active_reminders=active_reminders)
        
        if not res:
            failure = get_last_brain_failure()
            logging.error(
                "BRAIN_FAILURE | usuario=%s | texto_len=%s | history_len=%s | failure=%s",
                user_id,
                len(text_to_process or ""),
                len(history),
                json.dumps(failure, ensure_ascii=False, default=str) if failure else "None",
            )
            logging.error(
                "BRAIN_FAILURE_CONTEXT | usuario=%s | texto_preview=%s | history_preview=%s",
                user_id,
                (text_to_process or "")[:200],
                json.dumps(history[-4:], ensure_ascii=False, default=str)[:1000],
            )

            await update.effective_message.reply_text(
                build_user_brain_error_message(failure)
            )

            if is_transient_brain_failure(failure):
                logging.warning(
                    "Conservando historial para usuario %s porque el fallo fue transitorio",
                    user_id,
                )
            else:
                context.user_data['history'] = []
            return
        
        logging.info(f"Respuesta de IA para usuario {user_id}: {res}")

        action = res.get("action")
        reply_message = None
        
        if action == "CREATE":
            recurrence = res.get("recurrence")
            date_str = res.get("date")
            message = res.get("message")

            # Validar que tengamos una fecha para el recordatorio
            if not date_str:
                # No intentamos guardar en base de datos para evitar errores de integridad
                await update.effective_message.reply_text(
                    "Necesito saber cuándo quieres que te recuerde esto. "
                    "Por ejemplo: \"mañana a las 9am\" o \"el viernes a las 18:00\"."
                )
                return

            # Asegurar que el mensaje no esté vacío
            if not message or not str(message).strip():
                message = "Revisar enlace compartido"
            
            # Recuperar imagen pendiente (si existe)
            image_file_id = context.user_data.get('pending_image_id')
            
            # Guardar recordatorio con imagen
            add_reminder(user_id, message, date_str, recurrence, image_file_id)
            
            # Limpiar imagen pendiente después de guardar
            if 'pending_image_id' in context.user_data:
                del context.user_data['pending_image_id']
            if 'pending_image_mime_type' in context.user_data:
                del context.user_data['pending_image_mime_type']
            
            msg_recurrence = f"\n🔁 Recurrencia: {recurrence}" if recurrence else ""
            emoji = "🖼️" if image_file_id else "✅"
            
            # Formatear la fecha para mostrar el día de la semana
            try:
                dt_obj = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
                # Crear diccionario de días en español
                dias = {0: "Lunes", 1: "Martes", 2: "Miércoles", 3: "Jueves", 4: "Viernes", 5: "Sábado", 6: "Domingo"}
                dia_semana = dias[dt_obj.weekday()]
                fecha_formateada = f"{dia_semana} {date_str}"
            except Exception:
                fecha_formateada = date_str
            
            reply_message = f"{emoji} ¡Perfecto! He guardado tu recordatorio:\n\n📍 {message}\n📅 {fecha_formateada}{msg_recurrence}"
            
        elif action == "LIST":
            # Cambiamos para mostrar solo el botón del calendario, no la lista larga de texto
            reply_message = "📅 *Tus recordatorios:*\n\nHaz clic abajo para verlos en el calendario interactivo."
            
            # Botón para abrir la Web App en modo calendario
            webapp_calendar_url = build_webapp_url(user_id=user_id, mode="calendar")
            if webapp_calendar_url:
                keyboard = [[InlineKeyboardButton("📅 Ver en Calendario", web_app=WebAppInfo(url=webapp_calendar_url))]]
                reply_markup = InlineKeyboardMarkup(keyboard)
            else:
                reply_markup = None
                
        elif action == "DELETE":
            search_identifier = res.get("message")
            deleted_count = delete_reminder_by_text(user_id, search_identifier)
            if deleted_count > 0:
                reply_message = f"🗑️ He eliminado {deleted_count} recordatorio(s) relacionado(s) con '{search_identifier}'."
            else:
                reply_message = f"No encontré ningún recordatorio activo que coincida con '{search_identifier}'."
            
        elif action == "UPDATE":
            reminder_id = res.get("id")
            new_message = res.get("message")
            new_date = res.get("date")
            
            logging.info(f"UPDATE request - User: {user_id}, ID: {reminder_id}, Message: {new_message}, Date: {new_date}")
            
            # Validar que el ID esté presente
            if reminder_id is None:
                reply_message = "❌ No pude identificar qué recordatorio deseas modificar. ¿Cuál es el ID?"
                logging.warning(f"UPDATE falló: ID no proporcionado para usuario {user_id}")
            # Validar que al menos un campo a cambiar esté presente
            elif new_message is None and new_date is None:
                reply_message = "❌ Necesito saber qué quieres cambiar (la descripción, la fecha/hora, o ambas)."
                logging.warning(f"UPDATE falló: Sin campos a actualizar para usuario {user_id}")
            else:
                # Asegurar que reminder_id sea integer
                try:
                    reminder_id = int(reminder_id)
                except (ValueError, TypeError) as e:
                    reply_message = f"❌ El ID '{reminder_id}' no es un número válido."
                    logging.error(f"UPDATE falló: ID inválido '{reminder_id}' para usuario {user_id}: {e}")
                else:
                    # Llamar a la función de actualización
                    try:
                        success = update_reminder_by_id(user_id, reminder_id, new_message, new_date)
                        if success:
                            changes = []
                            if new_message:
                                changes.append(f"descripción: {new_message}")
                            if new_date:
                                changes.append(f"fecha/hora: {new_date}")
                            reply_message = f"✏️ ¡Listo! He actualizado el recordatorio #{reminder_id}:\n- {' y '.join(changes)}"
                            logging.info(f"UPDATE exitoso: Recordatorio #{reminder_id} actualizado para usuario {user_id}")
                        else:
                            reply_message = f"❌ No encontré un recordatorio activo con ID {reminder_id}."
                            logging.warning(f"UPDATE falló: Recordatorio #{reminder_id} no encontrado para usuario {user_id}")
                    except Exception as e:
                        reply_message = f"❌ Error al actualizar el recordatorio: {str(e)}"
                        logging.error(f"UPDATE error en database para usuario {user_id}: {e}", exc_info=True)
            
        elif action == "CONSULTAR_NOTAS":
            # Recuperar notas del usuario y hacer segunda llamada al LLM
            user_notes = get_notes_by_user(user_id)
            notes_response = process_notes_query(user_text, user_notes, history)
            reply_message = notes_response if notes_response else "No pude consultar tus notas en este momento."
            
        elif action == "CHAT":
            # Respuesta directa de la IA (incluyendo preguntas como ¿qué hora es?)
            reply_message = res.get("reply")
            
        elif action == "SET_SETTING":
            setting_name = res.get("setting_name")
            value = res.get("value")
            
            if setting_name == "daily_summary":
                set_daily_summary(user_id, enabled=value)
                state = "activado" if value else "desactivado"
                reply_message = f"🔔 He {state} tu resumen diario de lunes a viernes."
            elif setting_name == "daily_summary_time":
                # Asumimos que si cambia la hora, quiere activarlo también
                set_daily_summary(user_id, enabled=True, time=value)
                reply_message = f"🕒 Listo, ahora recibirás tu resumen diario a las {value}."
            else:
                reply_message = "Entendido, he guardado ese ajuste."
            
        else:
            reply_message = "No estoy seguro de qué hacer. ¿Puedes repetirlo?"
        
        # Enviar respuesta
        if reply_message:
            if action == "LIST":
                await update.effective_message.reply_text(reply_message, parse_mode="Markdown", reply_markup=reply_markup)
            else:
                await update.effective_message.reply_text(reply_message)
        
        # Actualizar historial con el nuevo mensaje y respuesta
        context.user_data['history'].append({"role": "user", "content": user_text})
        context.user_data['history'].append({"role": "assistant", "content": json.dumps(res, ensure_ascii=False)})
        
        # Pruning: mantener solo los últimos 6-8 mensajes (12-16 elementos con rol)
        max_history_length = 16
        if len(context.user_data['history']) > max_history_length:
            context.user_data['history'] = context.user_data['history'][-max_history_length:]
            logging.info(f"Historial podado para usuario {user_id}, nuevo tamaño: {len(context.user_data['history'])}")
    
    except Exception as e:
        logging.error(f"Error en process_normal_message para usuario {user_id}: {e}", exc_info=True)
        try:
            if update.effective_message:
                await update.effective_message.reply_text("Hubo un error procesando tu solicitud.")
            else:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="Hubo un error procesando tu solicitud."
                )
        except Exception:
            # Fallback si incluso el envío del mensaje de error falla
            try:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="Hubo un error procesando tu solicitud."
                )
            except Exception:
                pass
        # Limpiar historial ante error
        context.user_data['history'] = []

async def x_link_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja las opciones elegidas para enlaces de Twitter/X."""
    query = update.callback_query
    await query.answer()
    
    try:
        data = query.data
        user_id = update.effective_user.id
        
        if ":" not in data:
            return
            
        action, msg_id = data.split(":", 1)

        if action == "gh_cancel":
            await cancel_github_repository_analysis(update, context, msg_id)
            return
        
        if action.startswith("x_"):
            link_data = context.user_data.get('x_urls', {}).get(msg_id)
        elif action.startswith("gh_"):
            link_data = context.user_data.get('github_urls', {}).get(msg_id)
        elif action.startswith("yt_"):
            link_data = context.user_data.get('youtube_urls', {}).get(msg_id)
        else:
            link_data = None

        if not link_data:
            await query.edit_message_text("❌ Lo siento, la información de este enlace ya no está disponible.")
            return
            
        url = link_data['url']
        user_text = link_data['text']
        
        logging.info(f"Callback detectado: {action} para el mensaje {msg_id}")
        
        if action == "x_video":
            # Editar el mensaje para indicar que se está procesando
            await query.edit_message_text("⏳ Iniciando análisis del video...")
            await process_x_video(update, context, url, user_text)
        elif action == "x_reminder":
            # Editar el mensaje para indicar que se está procesando el recordatorio
            await query.edit_message_text("⏳ Preparando tu recordatorio...")
            
            # Enriquecer el texto para que la IA sepa que la intención es un recordatorio.
            # Incluimos una instrucción más clara con contexto de fecha.
            instruction = (
                f"Quiero agendar un recordatorio para revisar este enlace: {url}. "
                "Si no indico claramente cuándo, pregúntame para cuándo quiero el recordatorio."
            )
            
            # Extraer texto adicional del usuario (sin la URL) para dar más contexto
            extra_text = user_text.replace(url, '').strip()
            if extra_text and len(extra_text) > 2:
                instruction = f"Quiero agendar un recordatorio: {extra_text}. Enlace: {url}"
            
            await process_normal_message(update, context, instruction, user_id)
        elif action == "gh_analyze":
            await process_github_repository(update, context, url, user_text)
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
    except Exception as e:
        logging.error(f"Error en x_link_callback_handler: {e}", exc_info=True)
        try:
            await query.edit_message_text("❌ Ocurrió un error al procesar tu solicitud.")
        except Exception:
            # Si el mensaje ya fue editado o eliminado, intentar enviar uno nuevo
            try:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="❌ Ocurrió un error al procesar tu solicitud."
                )
            except Exception:
                pass


async def post_init(application):
    """Configura el botón de menú después de que la aplicación haya iniciado."""
    from telegram import MenuButtonWebApp
    try:
        # Configurar el botón de menú para abrir la Web App en modo calendario
        url = build_webapp_url(mode="calendar")
        if not url:
            logging.warning("No webapp URL configured; skipping Telegram menu button setup")
            return

        await application.bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="Calendar", web_app=WebAppInfo(url=url))
        )
        logging.info(f"Botón de menú configurado con URL: {url}")
    except Exception as e:
        logging.error(f"Error configurando el botón de menú: {e}")

# --- HANDLER PARA COMANDO /nota ---
# --- HANDLER PARA /nota CON FOTO ---
async def nota_photo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Guarda una nota con imagen cuando el usuario envía una foto con caption /nota."""
    user_id = update.effective_user.id
    caption = update.message.caption or ""

    # Extraer texto después de /nota
    parts = caption.split(None, 1)
    raw_note_text = parts[1].strip() if len(parts) > 1 else ""
    category, content = parse_note_category_and_content(raw_note_text)

    # Obtener file_id de la imagen
    image_file_id = None
    if update.message.photo:
        image_file_id = update.message.photo[-1].file_id
    elif update.message.document and update.message.document.mime_type and update.message.document.mime_type.startswith('image/'):
        image_file_id = update.message.document.file_id

    if not image_file_id:
        await update.message.reply_text("⚠️ No pude detectar la imagen. Intenta de nuevo.")
        return

    # Si no hay texto, usar placeholder
    if not content:
        content = "📸 Imagen"

    create_note(user_id, content, image_file_id, category)
    saved_category = category or UNCATEGORIZED_LABEL
    await update.message.reply_text(f"✅ Nota con imagen guardada en la categoría: {saved_category}.")
    logging.info(f"Nota con imagen guardada para usuario {user_id}: {content[:50]}... cat={saved_category} (img: {image_file_id[:20]}...)")

# --- HANDLER PARA COMANDO /nota ---
async def nota_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Guarda una nota persistente directamente sin pasar por brain.py."""
    user_id = update.effective_user.id
    # Extraer texto después de /nota
    text = update.message.text
    raw_note_text = text.split(None, 1)[1] if len(text.split(None, 1)) > 1 else ""
    category, content = parse_note_category_and_content(raw_note_text)

    # Verificar si hay una imagen pendiente de un mensaje anterior
    image_file_id = context.user_data.get('pending_image_id')

    if not content.strip() and not image_file_id:
        await update.message.reply_text(
            "⚠️ Por favor, escribe lo que deseas guardar después del comando.\n"
            "Ejemplo: /nota La clave del wifi es ABC123\n"
            "Con categoría: /nota Trabajo | Preparar informe\n"
            "También puedes enviar una foto con /nota como leyenda."
        )
        return

    # Si no hay texto pero sí imagen pendiente, usar placeholder
    if not content.strip():
        content = "📸 Imagen"

    create_note(user_id, content.strip(), image_file_id, category)

    # Limpiar imagen pendiente después de guardar
    if 'pending_image_id' in context.user_data:
        del context.user_data['pending_image_id']
    if 'pending_image_mime_type' in context.user_data:
        del context.user_data['pending_image_mime_type']

    emoji = "🖼️" if image_file_id else "✅"
    saved_category = category or UNCATEGORIZED_LABEL
    await update.message.reply_text(f"{emoji} Nota guardada correctamente en la categoría: {saved_category}.")
    logging.info(f"Nota guardada para usuario {user_id}: {content[:50]}... cat={saved_category} (img: {bool(image_file_id)})")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 ¡Hola! Soy Clusivai. Puedo chatear contigo y gestionar tus recordatorios. ¡Pruébame!")

if __name__ == '__main__':
    # Verificar variables de entorno
    telegram_token = os.getenv("TELEGRAM_TOKEN")
    if not telegram_token:
        logging.error("ERROR: TELEGRAM_TOKEN no está configurado en las variables de entorno")
        exit(1)
    
    logging.info(f"TELEGRAM_TOKEN configurado: {telegram_token[:10]}...")
    
    # Inicializar DB
    from database import init_db
    init_db()
    seed_ai_catalog_defaults()
    logging.info("Base de datos inicializada correctamente")
    validate_ai_configuration()
    
    application = ApplicationBuilder().token(telegram_token).post_init(post_init).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler(["ai", "modelo"], ai_command))
    application.add_handler(CommandHandler("nota", nota_command))
    # Nuevo: fotos/documentos-imagen con caption /nota
    application.add_handler(MessageHandler(
        (filters.PHOTO | filters.Document.IMAGE) & filters.CaptionRegex(r'^/nota'),
        nota_photo_command
    ))
    application.add_handler(CallbackQueryHandler(ai_settings_callback_handler, pattern=r"^ai:"))
    application.add_handler(CallbackQueryHandler(x_link_callback_handler, pattern=r"^(x_|gh_|yt_)"))
    application.add_handler(MessageHandler((filters.TEXT | filters.PHOTO | filters.Document.ALL) & (~filters.COMMAND), handle_message))
    
    # Programar el revisor cada 60 segundos
    job_queue = application.job_queue
    job_queue.run_repeating(check_reminders, interval=60, first=10)
    job_queue.run_repeating(poll_repo_analysis_updates, interval=1, first=1)
    
    # Programar resumen diario a las 7:45 AM Bogotá (Mon-Fri)
    from datetime import time
    tz_bogota = pytz.timezone('America/Bogota')
    daily_time = time(7, 45, 0, tzinfo=tz_bogota)
    job_queue.run_daily(send_daily_summaries, daily_time, days=(0, 1, 2, 3, 4))
    
    logging.info("Bot Clusivai encendido y sincronizado con Bogotá.")
    application.run_polling()
