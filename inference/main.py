import os
import time
from contextlib import asynccontextmanager
from threading import BoundedSemaphore
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, StringConstraints
from sentence_transformers import SentenceTransformer
import torch

REVISION = os.environ["MODEL_REVISION"]
capacity = BoundedSemaphore(1)
Text = Annotated[str, StringConstraints(min_length=1, max_length=4000)]


@asynccontextmanager
async def lifespan(app):
    torch.set_num_threads(int(os.environ.get("TORCH_THREADS", "2")))
    model_dir = Path(os.environ["MODEL_DIR"])
    if (model_dir / "revision.txt").read_text().strip() != REVISION:
        raise RuntimeError("Версия файлов модели не соответствует конфигурации")
    model = SentenceTransformer(str(model_dir), device="cpu", local_files_only=True)
    model.max_seq_length = 96
    app.state.model = model
    yield


app = FastAPI(title="Waive · Эмбеддинги", lifespan=lifespan)


class Batch(BaseModel):
    kind: Literal["query", "passage"]
    texts: list[Text] = Field(min_length=1, max_length=32)


@app.get("/health")
def health():
    return {"revision": REVISION}


@app.post("/embed")
def embed(batch: Batch):
    if not capacity.acquire(blocking=False):
        raise HTTPException(503, "Модель занята")
    start = time.monotonic()
    try:
        result = app.state.model.encode(
            [f"{batch.kind}: {text}" for text in batch.texts],
            normalize_embeddings=True, show_progress_bar=False,
        )
    finally:
        capacity.release()
    return {"revision": REVISION, "vectors": result.tolist(), "elapsed_ms": (time.monotonic() - start) * 1000}
