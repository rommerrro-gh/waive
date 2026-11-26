import os
import shutil
from pathlib import Path
from urllib.request import urlopen

MODEL = "intfloat/multilingual-e5-small"
FILES = (
    "model.safetensors", "config.json", "tokenizer.json", "tokenizer_config.json",
    "special_tokens_map.json", "sentence_bert_config.json", "modules.json",
    "1_Pooling/config.json", "sentencepiece.bpe.model",
)


def download():
    revision = os.environ["MODEL_REVISION"]
    target = Path("/models/e5")
    if target.exists():
        if (target / "revision.txt").read_text().strip() == revision:
            return
        raise RuntimeError("В томе уже есть другая версия модели")
    temporary = target.with_name("e5-download")
    temporary.mkdir(exist_ok=True)
    for file in FILES:
        output = temporary / file
        output.parent.mkdir(exist_ok=True, parents=True)
        with urlopen(f"https://huggingface.co/{MODEL}/resolve/{revision}/{file}", timeout=120) as source:
            with output.open("wb") as destination:
                shutil.copyfileobj(source, destination)
    (temporary / "revision.txt").write_text(revision + "\n")
    temporary.rename(target)


if __name__ == "__main__":
    download()
