import json
import math
import os
from urllib.request import Request, urlopen


def vectors(kind, texts, timeout):
    address = os.environ["EMBEDDING_URL"].rstrip("/") + "/embed"
    payload = json.dumps({"kind": kind, "texts": texts}).encode()
    request = Request(address, data=payload, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        result = json.load(response)
    embeddings = result["vectors"]
    if len(embeddings) != len(texts) or not all(
        len(vector) == 384 and all(math.isfinite(value) for value in vector)
        for vector in embeddings
    ):
        raise ValueError("Сервис вернул некорректные эмбеддинги")
    return result["revision"], embeddings


def vector_literal(vector):
    return "[" + ",".join(map(str, vector)) + "]"
