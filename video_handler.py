"""
video_handler.py
Módulo para descargar audio de videos de X.com, transcribir con Groq Whisper
y preparar el contenido para análisis.
"""

import os
import re
import logging
import tempfile
import shutil
import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Límite de archivo para Groq Whisper API (25 MB)
MAX_AUDIO_SIZE_BYTES = 25 * 1024 * 1024


def extract_x_url(text):
    """Detecta y extrae una URL de X.com o Twitter.com de un texto.
    
    Args:
        text: Texto del mensaje del usuario.
    
    Returns:
        La URL encontrada (str) o None si no hay coincidencia.
    """
    if not text:
        return None
    # Patrón: https://x.com/usuario/status/ID o https://twitter.com/usuario/status/ID
    # También captura parámetros opcionales como ?s=20&t=xxx
    pattern = r'https?://(?:www\.)?(?:x\.com|twitter\.com)/\w+/status/\d+(?:\S*)?'
    match = re.search(pattern, text)
    return match.group(0) if match else None


def download_audio(url):
    """Descarga el audio de un video de X.com usando yt-dlp.
    
    Args:
        url: URL del tweet/post con video.
    
    Returns:
        Tupla (audio_path, video_info) en caso de éxito.
        Tupla (None, error_message) en caso de error.
    """
    try:
        import yt_dlp
    except ImportError:
        logger.error("yt-dlp no está instalado. Ejecuta: pip install yt-dlp")
        return None, "Error interno: yt-dlp no está instalado."
    
    # Crear directorio temporal para la descarga
    temp_dir = tempfile.mkdtemp(prefix="clusivai_audio_")
    output_template = os.path.join(temp_dir, '%(id)s.%(ext)s')
    
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': output_template,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '64',  # Calidad baja para mantener archivos pequeños
        }],
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        # Ayuda con algunos videos de X.com que requieren API alternativa
        'extractor_args': {'twitter': {'api': ['syndication']}},
        # Timeout de red
        'socket_timeout': 30,
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            logger.info(f"Descargando audio de: {url}")
            info = ydl.extract_info(url, download=True)
            
            if info is None:
                return None, "No se pudo obtener información del video."
            
            # Obtener el nombre de archivo que yt-dlp generó
            # prepare_filename da el nombre ANTES del postprocesador,
            # así que cambiamos la extensión a .mp3
            original_filename = ydl.prepare_filename(info)
            audio_path = os.path.splitext(original_filename)[0] + '.mp3'
            
            # Verificar que el archivo existe
            if not os.path.exists(audio_path):
                # Buscar cualquier archivo de audio en el directorio temporal
                for f in os.listdir(temp_dir):
                    if f.endswith(('.mp3', '.m4a', '.wav', '.ogg', '.opus', '.webm')):
                        audio_path = os.path.join(temp_dir, f)
                        break
                else:
                    return None, "No se pudo extraer el audio del video. ¿El tweet tiene video?"
            
            # Verificar tamaño del archivo
            file_size = os.path.getsize(audio_path)
            logger.info(f"Audio descargado: {audio_path} ({file_size / 1024:.1f} KB)")
            
            if file_size > MAX_AUDIO_SIZE_BYTES:
                cleanup_audio(audio_path)
                size_mb = file_size / (1024 * 1024)
                return None, f"El audio del video es demasiado grande ({size_mb:.1f} MB). El límite es 25 MB."
            
            if file_size < 1000:  # Menos de 1 KB probablemente es un error
                cleanup_audio(audio_path)
                return None, "El video parece no tener audio o es demasiado corto."
            
            # Extraer información útil del video
            video_info = {
                'title': info.get('title', 'Sin título'),
                'uploader': info.get('uploader', 'Desconocido'),
                'duration': info.get('duration', 0),
                'id': info.get('id', 'unknown'),
            }
            
            return audio_path, video_info
            
    except Exception as e:
        error_str = str(e).lower()
        logger.error(f"Error descargando audio de {url}: {e}")
        
        # Limpiar directorio temporal en caso de error
        if os.path.isdir(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
        
        # Mensajes de error amigables según el tipo de error
        if 'private' in error_str or 'protected' in error_str:
            return None, "No puedo acceder a este video. Parece ser de una cuenta privada o protegida."
        elif 'not found' in error_str or '404' in error_str:
            return None, "No encontré el video. ¿El tweet fue eliminado o el enlace es incorrecto?"
        elif 'no video' in error_str:
            return None, "Este tweet no parece contener un video."
        elif 'ffmpeg' in error_str:
            return None, "Error interno: FFmpeg no está instalado en el servidor."
        else:
            return None, f"Error al descargar el video: {str(e)[:150]}"


def transcribe_audio(audio_path):
    """Transcribe un archivo de audio usando la API de Groq (Whisper).
    
    Args:
        audio_path: Ruta al archivo de audio (.mp3).
    
    Returns:
        Tupla (transcript_text, None) en caso de éxito.
        Tupla (None, error_message) en caso de error.
    """
    if not GROQ_API_KEY:
        logger.error("GROQ_API_KEY no está configurado en las variables de entorno")
        return None, "Error de configuración: GROQ_API_KEY no está definido."
    
    url = "https://api.groq.com/openai/v1/audio/transcriptions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}"
    }
    
    try:
        file_size = os.path.getsize(audio_path)
        logger.info(f"Transcribiendo audio: {audio_path} ({file_size / 1024:.1f} KB)")
        
        with open(audio_path, 'rb') as audio_file:
            files = {
                'file': (os.path.basename(audio_path), audio_file, 'audio/mpeg')
            }
            data = {
                'model': 'whisper-large-v3-turbo',
                # No especificamos 'language' para permitir autodetección
                'response_format': 'json',
            }
            
            response = requests.post(
                url, 
                headers=headers, 
                files=files, 
                data=data, 
                timeout=120  # 2 minutos timeout para archivos grandes
            )
        
        if response.status_code == 413:
            return None, "El archivo de audio es demasiado grande para la API de transcripción."
        
        if response.status_code == 429:
            return None, "Se excedió el límite de la API de transcripción. Intenta de nuevo en unos minutos."
        
        if response.status_code != 200:
            logger.error(f"Groq API error: {response.status_code} - {response.text[:300]}")
            return None, f"Error en la transcripción (código {response.status_code}). Intenta de nuevo."
        
        result = response.json()
        transcript = result.get('text', '').strip()
        
        if not transcript:
            return None, "La transcripción está vacía. El video podría no tener audio hablado."
        
        logger.info(f"Transcripción exitosa: {len(transcript)} caracteres")
        return transcript, None
        
    except requests.exceptions.Timeout:
        logger.error("Timeout al transcribir audio (120s)")
        return None, "La transcripción tardó demasiado. Intenta con un video más corto."
    except requests.exceptions.ConnectionError:
        logger.error("Error de conexión con Groq API")
        return None, "Error de conexión con el servicio de transcripción."
    except Exception as e:
        logger.error(f"Error inesperado transcribiendo audio: {e}", exc_info=True)
        return None, f"Error inesperado al transcribir: {str(e)[:100]}"


def cleanup_audio(audio_path):
    """Elimina el archivo de audio temporal y su directorio.
    
    Args:
        audio_path: Ruta al archivo de audio a eliminar.
    """
    try:
        if audio_path and os.path.exists(audio_path):
            dir_path = os.path.dirname(audio_path)
            os.remove(audio_path)
            logger.info(f"Archivo de audio eliminado: {audio_path}")
            
            # También eliminar el directorio temporal si está en /tmp/
            if os.path.isdir(dir_path) and dir_path.startswith(tempfile.gettempdir()):
                shutil.rmtree(dir_path, ignore_errors=True)
                logger.info(f"Directorio temporal eliminado: {dir_path}")
    except Exception as e:
        logger.warning(f"Error limpiando archivo de audio {audio_path}: {e}")
