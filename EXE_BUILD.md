# Сборка Windows EXE

Для этого проекта exe должен запускать локальный веб-сервер и открывать браузер.

Текущий exe открывает launcher UI. Уже из него можно:

- запустить локальный сервер;
- запустить Docker-режим, если установлен Docker Desktop;
- запустить онлайн-режим через Cloudflare Tunnel, если установлен cloudflared;
- остановить сервер;
- открыть приложение в браузере;
- включить или выключить Ollama для тестов;
- выбрать имя модели, например `mistral` или `tinyllama`.

Для быстрого старта на Windows предпочтителен формат onedir, а не onefile. Onefile медленнее, потому что при каждом запуске распаковывает приложение во временную папку.

## Что уже подготовлено

- шаблоны и openapi.yaml корректно подхватываются в PyInstaller-сборке;
- локальная база SQLite по умолчанию хранится в `%LOCALAPPDATA%\VacMatchAI\vacmatch.db`, а не внутри exe;
- при запуске exe автоматически открывается браузер на `http://127.0.0.1:5000`;
- если PostgreSQL и Ollama отсутствуют, приложение всё равно работает через fallback.

## Как собрать

1. Установить зависимости проекта.
2. Выполнить:

```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller==6.16.0 waitress==3.0.0
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onedir --windowed --name ProjectHR --add-data "templates;templates" --add-data "openapi.yaml;." --hidden-import waitress --hidden-import psycopg --hidden-import psycopg_binary launcher.py
```

3. Готовый файл появится здесь:

```text
dist\ProjectHR\ProjectHR.exe
```

## Что положить рядом с exe

Для базового режима ничего обязательного рядом класть не нужно.

Опционально можно положить рядом файл `.env`, если нужно:

- сменить `SECRET_KEY`;
- поменять хост через `APP_HOST`;
- выключить Ollama через `OLLAMA_ENABLED=0`;
- выбрать модель Ollama через `OLLAMA_MODEL`, например `mistral` или `tinyllama`;
- включить PostgreSQL через `DB_ENGINE=postgres`;
- поменять порт через `APP_PORT`;
- отключить автооткрытие браузера через `OPEN_BROWSER=0`.

## Что нужно на другом компьютере

Для запуска собранного exe не нужны:

- Python;
- PostgreSQL, если подходит SQLite fallback;
- Ollama, если устраивает fallback-режим без LLM.

Нужны только:

- Windows x64;
- свободный порт 5000;
- разрешение на запись в `%LOCALAPPDATA%\VacMatchAI`.

Для онлайн-режима дополнительно нужен:

- установленный `cloudflared`;
- доступ в интернет для создания временной ссылки `trycloudflare.com`.

## Ограничения

- exe, собранный на Windows, рассчитан на Windows, а не на Linux/macOS;
- truly universal один exe для всех ОС не существует, для каждой ОС нужна отдельная сборка;
- если нужен PostgreSQL-режим на другом ПК, там должен быть доступен сам сервер PostgreSQL;
- если нужен полноценный AI-режим, на другом ПК должна быть установлена Ollama с моделью, указанной в `OLLAMA_MODEL`, например `mistral` или `tinyllama`.