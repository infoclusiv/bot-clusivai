"""repo_handler.py
Herramientas para detectar enlaces de repositorios GitHub y preparar su contenido
para análisis con GitIngest y el LLM.
"""

import logging
import os
import re
from contextlib import contextmanager
from urllib.parse import urlsplit

try:
    from gitingest import ingest_async
    from gitingest.utils.exceptions import InvalidGitHubTokenError
except ImportError:  # pragma: no cover - depende del entorno de despliegue
    ingest_async = None

    class InvalidGitHubTokenError(ValueError):
        pass


logger = logging.getLogger(__name__)

GITHUB_TOKEN_PATTERN = re.compile(
    r'^(?:gh[pousr]_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9]{22}_[A-Za-z0-9]{59})$'
)

GITHUB_REPO_PATTERN = re.compile(
    r'https?://(?:www\.)?github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[^\s)]*)?',
    re.IGNORECASE,
)

DEFAULT_MAX_FILE_SIZE = int(os.getenv("GITINGEST_MAX_FILE_SIZE", "200000"))
DEFAULT_REPO_CHUNK_MAX_CHARS = int(os.getenv("REPO_CHUNK_MAX_CHARS", "24000"))
DEFAULT_REPO_CHUNK_MAX_FILES = int(os.getenv("REPO_CHUNK_MAX_FILES", "16"))
DEFAULT_EXCLUDE_PATTERNS = {
    ".git/*",
    "__pycache__/*",
    "*.pyc",
    "*.pyo",
    "*.lock",
    "node_modules/*",
    "dist/*",
    "build/*",
    "coverage/*",
    "vendor/*",
    "*.min.js",
    "*.min.css",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.webp",
    "*.ico",
    "*.pdf",
    "*.zip",
    "*.tar",
    "*.gz",
}


class GitHubRepositoryError(Exception):
    """Error base para fallos durante el análisis de repositorios GitHub."""

    default_user_message = (
        "No pude analizar ese repositorio de GitHub en este momento. Intenta de nuevo más tarde."
    )

    def __init__(self, message=None, *, user_message=None):
        super().__init__(message or user_message or self.default_user_message)
        self.user_message = user_message or self.default_user_message


class InvalidGitHubRepoUrlError(GitHubRepositoryError):
    default_user_message = "Ese enlace no parece apuntar a un repositorio de GitHub válido."


class GitHubRepoAccessError(GitHubRepositoryError):
    default_user_message = (
        "No pude acceder a ese repositorio. Verifica que exista, sea público o que el token configurado sea válido."
    )


class GitHubRepoAuthError(GitHubRepositoryError):
    default_user_message = (
        "La configuración de acceso a GitHub es inválida. Corrige GITHUB_TOKEN o elimínalo para analizar repos públicos."
    )


class GitHubRepoNetworkError(GitHubRepositoryError):
    default_user_message = (
        "No pude conectarme a GitHub o descargar el repositorio en este momento. Intenta de nuevo en unos minutos."
    )


class GitHubRepoDependencyError(GitHubRepositoryError):
    default_user_message = (
        "El entorno del bot no tiene todo lo necesario para analizar repositorios GitHub."
    )


class GitHubRepoContentError(GitHubRepositoryError):
    default_user_message = "No encontré contenido utilizable para analizar en ese repositorio."


def _get_valid_github_token():
    """Retorna un token válido o None, ignorando valores malformados."""
    token = (os.getenv("GITHUB_TOKEN") or "").strip()
    if not token:
        return None

    if not GITHUB_TOKEN_PATTERN.fullmatch(token):
        logger.warning(
            "GITHUB_TOKEN configurado pero con formato inválido; se ignorará para permitir repos públicos"
        )
        return None

    return token


@contextmanager
def _scoped_github_token(token):
    """Controla qué token ve GitIngest durante una llamada puntual."""
    previous_token = os.environ.get("GITHUB_TOKEN")

    if token:
        os.environ["GITHUB_TOKEN"] = token
    else:
        os.environ.pop("GITHUB_TOKEN", None)

    try:
        yield
    finally:
        if previous_token is None:
            os.environ.pop("GITHUB_TOKEN", None)
        else:
            os.environ["GITHUB_TOKEN"] = previous_token


def _classify_ingest_error(exc):
    """Traduce errores de GitIngest a errores de dominio más claros."""
    message = str(exc).strip() or exc.__class__.__name__
    lower_message = message.lower()

    if isinstance(exc, InvalidGitHubTokenError):
        return GitHubRepoAuthError(message)

    if "repository not found" in lower_message or "not found" in lower_message:
        return GitHubRepoAccessError(message)

    if "invalid github token" in lower_message or "token format" in lower_message:
        return GitHubRepoAuthError(message)

    if "timeout" in lower_message or "timed out" in lower_message:
        return GitHubRepoNetworkError(message)

    if "http status" in lower_message or "connection" in lower_message or "network" in lower_message:
        return GitHubRepoNetworkError(message)

    if "git" in lower_message and (
        "not installed" in lower_message or "not found" in lower_message or "required" in lower_message
    ):
        return GitHubRepoDependencyError(message)

    return GitHubRepositoryError(message)


def extract_github_repo_url(text):
    """Detecta y normaliza un enlace de repositorio GitHub dentro de un texto."""
    if not text:
        return None

    match = GITHUB_REPO_PATTERN.search(text)
    if not match:
        return None

    return normalize_github_repo_url(match.group(0))


def normalize_github_repo_url(url):
    """Convierte distintos enlaces GitHub al formato base https://github.com/owner/repo."""
    if not url:
        return None

    try:
        parsed = urlsplit(url.strip())
    except Exception:
        return None

    if parsed.netloc.lower() not in {"github.com", "www.github.com"}:
        return None

    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) < 2:
        return None

    owner = path_parts[0]
    repo = path_parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]

    if not owner or not repo:
        return None

    return f"https://github.com/{owner}/{repo}"


def get_repo_slug(url):
    """Retorna owner/repo a partir de una URL normalizada de GitHub."""
    normalized_url = normalize_github_repo_url(url)
    if not normalized_url:
        return None

    parsed = urlsplit(normalized_url)
    path_parts = [part for part in parsed.path.split("/") if part]
    return f"{path_parts[0]}/{path_parts[1]}"


async def ingest_github_repository(repo_url):
    """Obtiene el digest completo de un repositorio público usando GitIngest."""
    if ingest_async is None:
        raise GitHubRepoDependencyError(
            "GitIngest no está instalado en el entorno del bot. Ejecuta: pip install gitingest"
        )

    normalized_url = normalize_github_repo_url(repo_url)
    if not normalized_url:
        raise InvalidGitHubRepoUrlError("El enlace de GitHub no parece apuntar a un repositorio válido.")

    token = _get_valid_github_token()

    logger.info("Iniciando ingestión del repositorio GitHub: %s", normalized_url)
    try:
        with _scoped_github_token(token):
            summary, tree, content = await ingest_async(
                normalized_url,
                max_file_size=DEFAULT_MAX_FILE_SIZE,
                exclude_patterns=DEFAULT_EXCLUDE_PATTERNS,
                token=token,
            )
    except FileNotFoundError as exc:
        raise GitHubRepoDependencyError("Git no está disponible en el entorno del bot.") from exc
    except Exception as exc:
        classified_error = _classify_ingest_error(exc)
        logger.error(
            "Error DETALLADO ingestión %s: tipo=%s msg=%s",
            normalized_url,
            type(exc).__name__,
            str(exc),
            exc_info=True,
        )
        raise classified_error from exc

    if not content or not content.strip():
        raise GitHubRepoContentError("GitIngest no devolvió contenido utilizable para este repositorio.")

    return {
        "url": normalized_url,
        "slug": get_repo_slug(normalized_url),
        "summary": summary.strip(),
        "tree": tree.strip(),
        "content": content.strip(),
    }


def split_repository_content(
    content,
    max_chars=DEFAULT_REPO_CHUNK_MAX_CHARS,
    max_files_per_chunk=DEFAULT_REPO_CHUNK_MAX_FILES,
):
    """Divide el digest en partes manejables, intentando respetar los límites por archivo."""
    if not content:
        return []

    chunks = []
    current_lines = []
    current_length = 0
    current_file_count = 0

    for line in content.splitlines(keepends=True):
        is_new_file = line.startswith("FILE:")

        if current_lines and is_new_file and (
            current_length + len(line) > max_chars or current_file_count >= max_files_per_chunk
        ):
            chunks.append("".join(current_lines).strip())
            current_lines = []
            current_length = 0
            current_file_count = 0

        current_lines.append(line)
        current_length += len(line)

        if is_new_file:
            current_file_count += 1

        if current_length >= max_chars and not is_new_file:
            chunks.append("".join(current_lines).strip())
            current_lines = []
            current_length = 0
            current_file_count = 0

    if current_lines:
        chunks.append("".join(current_lines).strip())

    return [chunk for chunk in chunks if chunk]