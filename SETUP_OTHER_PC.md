# Установка проекта на другой компьютер

## 1. Что нужно установить

Есть два варианта запуска проекта.

### Вариант A. Локальный запуск на Windows

Нужно установить:

- Python 3.12 64-bit;
- PostgreSQL 16 или совместимую версию, если нужен именно PostgreSQL;
- опционально Ollama, если нужен AI-режим без fallback;
- Git, если проект будет копироваться через репозиторий.

Для быстрого локального старта PostgreSQL теперь необязателен: приложение умеет автоматически перейти на SQLite.

### Вариант B. Запуск через Docker

Нужно установить:

- Docker Desktop;
- опционально Ollama, если нужен полноценный AI-режим вне fallback.

Если нужен максимально простой перенос, предпочтителен Docker-вариант.

## 2. Какие файлы должны быть на другом компьютере

Нужно перенести всю папку проекта, включая:

- app.py;
- templates;
- requirements.txt;
- .env.example;
- start.bat;
- docker-compose.yml;
- Dockerfile;
- postgres_setup.sql.

Папки .venv и ai2 переносить не нужно.

## 3. Локальная установка на Windows

### Шаг 1. Установить Python

Установить Python 3.12 64-bit.

При установке желательно включить:

- Add python.exe to PATH;
- py launcher.

### Шаг 2. Установить PostgreSQL при необходимости

Если нужен полноценный режим на PostgreSQL, установить PostgreSQL 16.

При установке запомнить:

- пользователя администратора PostgreSQL;
- пароль администратора PostgreSQL;
- порт 5432.

### Шаг 3. Создать базу данных проекта при использовании PostgreSQL

После установки PostgreSQL открыть psql или pgAdmin и выполнить SQL из файла postgres_setup.sql.

Если используется psql, пример команды:

```powershell
psql -U postgres -f postgres_setup.sql
```

Скрипт создаст:

- пользователя vacmatch_user;
- базу vacmatch;
- права доступа для проекта.

### Шаг 4. Подготовить .env

Если .env еще не существует, можно просто скопировать шаблон:

```powershell
Copy-Item .env.example .env
```

По умолчанию проект ожидает:

- DB_ENGINE=auto
- DB_NAME=vacmatch
- DB_USER=vacmatch_user
- DB_PASSWORD=vacmatchvacmatch
- DB_HOST=localhost
- DB_PORT=5432
- SQLITE_PATH=vacmatch.db

Если PostgreSQL не установлен, оставьте DB_ENGINE=auto или укажите DB_ENGINE=sqlite.

### Шаг 5. Запустить проект

Запускать одной командой:

```bat
start.bat
```

Скрипт сам:

- создаст .venv при необходимости;
- проверит зависимости;
- установит пакеты, если их нет;
- автоматически включит SQLite fallback, если PostgreSQL недоступен;
- запустит приложение.

### Шаг 6. Проверить работу

Открыть в браузере:

- http://127.0.0.1:5000
- http://127.0.0.1:5000/docs
- http://127.0.0.1:5000/health

## 4. Запуск через Docker

### Шаг 1. Установить Docker Desktop

После установки убедиться, что команда docker доступна.

### Шаг 2. Открыть папку проекта

Перейти в корень проекта.

### Шаг 3. Выполнить запуск

```powershell
docker compose up --build
```

Будут подняты:

- контейнер web с приложением;
- контейнер db с PostgreSQL.

### Шаг 4. Проверить работу

Открыть:

- http://127.0.0.1:5000
- http://127.0.0.1:5000/docs
- http://127.0.0.1:5000/health

## 5. Установка Ollama

Ollama нужна только для полноценной AI-генерации и оценки. Без нее приложение работает в fallback-режиме.

Если Ollama нужна, установить ее и затем выполнить:

```powershell
ollama pull mistral
ollama serve
```

Модель можно выбирать через .env:

```env
OLLAMA_MODEL=mistral
```

или:

```env
OLLAMA_MODEL=tinyllama
```

Если выбрана tinyllama, сначала скачайте её:

```powershell
ollama pull tinyllama
ollama serve
```

## 6. Что установить обязательно

### Для локального запуска

Обязательно:

- Python 3.12 64-bit.

Необязательно:

- PostgreSQL;
- Ollama.

### Для Docker-запуска

Обязательно:

- Docker Desktop.

Необязательно:

- Ollama.

## 7. Быстрая памятка

### Самый простой локальный вариант

1. Установить Python 3.12 64-bit.
2. При желании установить PostgreSQL и выполнить postgres_setup.sql.
3. Запустить start.bat.

### Самый простой переносимый вариант

1. Установить Docker Desktop.
2. Выполнить docker compose up --build.

## 8. Что делать, если не запускается

- Если ошибка про Flask или другие пакеты: запустить start.bat еще раз.
- Если ошибка про PostgreSQL: либо проверить, что сервер PostgreSQL запущен и база создана, либо переключиться на SQLite через DB_ENGINE=sqlite.
- Если ошибка про proxy: использовать start.bat или start_windows.ps1 с флагами -ForceLocal -UseProxy и при необходимости -ProxyUrl.
- Если Ollama не установлена: приложение все равно может работать без нее.