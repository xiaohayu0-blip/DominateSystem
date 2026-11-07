from functools import lru_cache

from .config import settings

BGE_MODEL = "BAAI/bge-small-zh-v1.5"
BGE_DIMENSIONS = 512


@lru_cache
def bge_model(allow_download: bool = False):
    try:
        from fastembed import TextEmbedding
        return TextEmbedding(
            model_name=BGE_MODEL,
            cache_dir=str(settings().bge_cache_dir),
            providers=["CPUExecutionProvider"],
            threads=2,
            local_files_only=not allow_download,
        )
    except Exception as exc:
        raise RuntimeError("BGE 初始化失败。请安装 bge 依赖并运行 python -m app.cli bge-index。") from exc


def embed_passages(passages: list[str], *, allow_download: bool = False) -> list[list[float]]:
    if not passages:
        return []
    return [vector.tolist() for vector in bge_model(allow_download).passage_embed(passages, batch_size=16)]


def embed_query(text: str) -> list[float]:
    return next(bge_model().query_embed(text)).tolist()
