# Waive

Поиск по каталогу Softline. Первый этап включает потоковый импорт XML, снимки каталога PostgreSQL, полнотекстовый поиск, подбор по артикулу и веб-интерфейс.

## Локальный запуск

1. Скопируйте `.env.example` в `.env` и задайте пароль. В `DATABASE_URL` используйте тот же пароль, кодируя специальные символы по правилам URL.
2. Запустите PostgreSQL, примените миграцию и загрузите каталог:

```sh
docker compose up -d db
docker compose run --rm api alembic upgrade head
docker compose run --rm api python -m app.import_catalog /catalog/Mindbox.xml.gz
docker compose up -d api
```

3. Откройте <http://localhost:8000>. Проверка готовности -- `GET /ready`. Поиск -- `GET /search?q=...`.

Импорт принимает обычный XML и gzip. Он строит снимок и индекс в одной транзакции. При ошибке активный снимок остаётся доступным. Повторная загрузка того же содержимого не создаёт копию. Для локального Compose используется одна роль БД. Перед внешним размещением нужно разделить права сервиса и импорта, настроить HTTPS и проверить нагрузку.

## Подключение смыслового поиска

Для смыслового поиска используем E5-small. Загрузка занимает около 0,49 ГБ без контейнера и зависимостей. Загружайте модель только на согласованном хосте. Том `waive-model-cache` хранит веса отдельно, контейнер читает их без сети. Укажите известную ревизию:

```sh
export MODEL_REVISION=fd1525a9fd15316a2d503bf26ab031a61d056e98
docker volume create waive-model-cache
docker run --rm -e MODEL_REVISION -v waive-model-cache:/models \
  -v "$PWD/inference/download.py:/download.py:ro" python:3.12-slim python /download.py
docker compose -f compose.yaml -f compose.semantic.yaml up -d --build inference api
docker compose -f compose.yaml -f compose.semantic.yaml run --rm api python -m app.index_semantic
```

Перед индексацией дождитесь запуска inference. Проверить режим можно через `GET /search?q=...&semantic=true` и поле `search_mode`. Режим `semantic=false` подходит для сравнения. При отсутствии готового индекса или таймауте модели API обслуживает запрос в текстовом режиме. Новый снимок сразу доступен для текстового поиска. Смысловой режим включается для него после полной индексации. В текущем виде поиск по векторам точный, индекс HNSW можно проверить по мере роста каталога.
