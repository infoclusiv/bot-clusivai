import os
import logging
import pytz
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters, CommandHandler

from brain import process_user_input
from database import add_reminder, get_user_reminders, get_connection, delete_reminder_by_text

load_dotenv()

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- REVISOR DE RECORDATORIOS (Bogotá Time) ---
async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    tz_bogota = pytz.timezone('America/Bogota')
    now_str = datetime.now(tz_bogota).strftime("%Y-%m-%d %H:%M:%S")
    
    conn = get_connection()
    cursor = conn.cursor()
    
    # Buscamos tareas pendientes cuya fecha ya pasó
    cursor.execute('SELECT id, user_id, message FROM reminders WHERE remind_at <= ? AND status = "pending"', (now_str,))
    due_reminders = cursor.fetchall()
    
    for rem in due_reminders:
        rem_id, user_id, msg = rem
        try:
            await context.bot.send_message(
                chat_id=user_id, 
                text=f"⏰ ¡HOLA! Tienes este recordatorio pendiente:\n\n📌 {msg}"
            )
            cursor.execute('UPDATE reminders SET status = "sent" WHERE id = ?', (rem_id,))
        except Exception as e:
            logging.error(f"Error enviando mensaje: {e}")
            
    conn.commit()
    conn.close()

# --- MANEJADOR DE MENSAJES ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    user_id = update.effective_user.id
    
    await update.message.reply_chat_action("typing")
    
    # La IA analiza el texto
    res = process_user_input(user_text)
    
    if not res:
        await update.message.reply_text("Lo siento, tuve un problema con mi conexión cerebral.")
        return

    action = res.get("action")
    
    if action == "CREATE":
        add_reminder(user_id, res.get("message"), res.get("date"))
        await update.message.reply_text(f"✅ ¡Perfecto! He guardado tu recordatorio:\n\n📍 {res.get('message')}\n📅 {res.get('date')}")
        
    elif action == "LIST":
        reminders = get_user_reminders(user_id)
        if not reminders:
            await update.message.reply_text("No tienes recordatorios activos.")
        else:
            txt = "📝 *Tus recordatorios:*\n\n"
            for r in reminders:
                txt += f"• `{r[0]}`: {r[1]} _({r[2]})_\n"
            await update.message.reply_text(txt, parse_mode="Markdown")
            
    elif action == "DELETE":
        search_identifier = res.get("message")
        try:
            deleted_count = delete_reminder_by_text(user_id, search_identifier)
            if deleted_count > 0:
                await update.message.reply_text(f"🗑️ He eliminado {deleted_count} recordatorio(s) relacionado(s) con '{search_identifier}'.")
            else:
                await update.message.reply_text(f"No encontré ningún recordatorio activo que coincida con '{search_identifier}'.")
        except Exception as e:
            logging.error(f"Error al borrar recordatorio: {e}")
            await update.message.reply_text("Hubo un error al intentar borrar el recordatorio.")
        
    elif action == "CHAT":
        # Respuesta directa de la IA (incluyendo preguntas como ¿qué hora es?)
        await update.message.reply_text(res.get("reply"))
        
    else:
        await update.message.reply_text("No estoy seguro de qué hacer. ¿Puedes repetirlo?")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 ¡Hola! Soy Clusivai. Puedo chatear contigo y gestionar tus recordatorios. ¡Pruébame!")

if __name__ == '__main__':
    # Inicializar DB
    from database import init_db
    init_db()
    
    application = ApplicationBuilder().token(os.getenv("TELEGRAM_TOKEN")).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    
    # Programar el revisor cada 60 segundos
    job_queue = application.job_queue
    job_queue.run_repeating(check_reminders, interval=60, first=10)
    
    print("Bot Clusivai encendido y sincronizado con Bogotá.")
    application.run_polling()
