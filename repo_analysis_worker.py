import asyncio
import logging

from brain import process_repository_chunk, synthesize_repository_analysis
from repo_handler import GitHubRepositoryError, ingest_github_repository, split_repository_content


logger = logging.getLogger(__name__)


def _emit(progress_queue, event_type, **payload):
    progress_queue.put({"type": event_type, **payload})


def run_repository_analysis_worker(url, history, progress_queue):
    """Ejecuta el análisis completo del repositorio en un proceso aislado."""
    try:
        _emit(
            progress_queue,
            "progress",
            text="⏳ Obteniendo el código del repositorio con GitIngest...",
        )

        repo_data = asyncio.run(ingest_github_repository(url))
        repo_chunks = split_repository_content(repo_data["content"])

        if not repo_chunks:
            _emit(
                progress_queue,
                "result",
                status="failed",
                error_message="No encontré contenido utilizable para analizar en ese repositorio.",
            )
            return

        partial_analyses = []
        total_chunks = len(repo_chunks)

        for index, chunk in enumerate(repo_chunks, start=1):
            _emit(
                progress_queue,
                "progress",
                text=f"🧠 Analizando el repositorio ({index}/{total_chunks})...",
            )

            partial_analysis = process_repository_chunk(
                repo_data["slug"],
                repo_data["summary"],
                repo_data["tree"],
                chunk,
                index,
                total_chunks,
                history,
            )

            if partial_analysis:
                partial_analyses.append(partial_analysis)
            else:
                partial_analyses.append(
                    f"No se pudo obtener un análisis confiable para la parte {index} del repositorio."
                )

        synthesis_inputs = partial_analyses
        if len(partial_analyses) > 6:
            condensed_analyses = []
            batch_size = 4
            total_batches = (len(partial_analyses) + batch_size - 1) // batch_size

            for batch_index, start in enumerate(range(0, len(partial_analyses), batch_size), start=1):
                _emit(
                    progress_queue,
                    "progress",
                    text=(
                        "🧩 Consolidando hallazgos intermedios "
                        f"({batch_index}/{total_batches})..."
                    ),
                )
                batch = partial_analyses[start:start + batch_size]
                condensed_analysis = synthesize_repository_analysis(
                    repo_data["slug"],
                    repo_data["summary"],
                    repo_data["tree"],
                    batch,
                    history,
                )
                condensed_analyses.append(condensed_analysis or "\n\n".join(batch[:2]))

            synthesis_inputs = condensed_analyses

        _emit(
            progress_queue,
            "progress",
            text="🧩 Consolidando la explicación final del repositorio...",
        )

        final_analysis = synthesize_repository_analysis(
            repo_data["slug"],
            repo_data["summary"],
            repo_data["tree"],
            synthesis_inputs,
            history,
        )

        if final_analysis:
            response_text = (
                "🐙 Análisis del repositorio GitHub\n"
                f"📦 {repo_data['slug']}\n"
                f"🔗 {repo_data['url']}\n\n"
                f"{final_analysis}"
            )
        else:
            response_text = (
                "⚠️ No pude consolidar una explicación final completa del repositorio. "
                "Te comparto los hallazgos parciales obtenidos:\n\n"
                + "\n\n".join(partial_analyses[:6])
            )

        _emit(
            progress_queue,
            "result",
            status="completed",
            repo_data={
                "url": repo_data["url"],
                "slug": repo_data["slug"],
                "summary": repo_data["summary"],
            },
            final_analysis=final_analysis,
            response_text=response_text,
        )
    except GitHubRepositoryError as exc:
        logger.warning("Error controlado analizando repositorio GitHub: %s", exc, exc_info=True)
        _emit(
            progress_queue,
            "result",
            status="failed",
            error_message=exc.user_message,
        )
    except Exception as exc:
        logger.error("Error inesperado en worker de análisis GitHub: %s", exc, exc_info=True)
        _emit(
            progress_queue,
            "result",
            status="failed",
            error_message=(
                "No pude analizar ese repositorio de GitHub en este momento. "
                "Intenta de nuevo más tarde."
            ),
        )