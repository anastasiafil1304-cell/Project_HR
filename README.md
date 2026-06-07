# Project HR

Веб-приложение Project HR для HR-скрининга кандидатов.

## Обзор процесса

Для быстрого просмотра полного цикла запустите приложение и откройте обзор процесса:

```text
http://localhost:5000/demo
```

Обзор процесса автоматически:
- создаёт HR-пользователя;
- создаёт рабочий пример вакансии Python Backend Developer;
- добавляет вопросы по Python, SQL, Docker, тестированию и коммуникации;
- добавляет результаты двух кандидатов с разными оценками;
- авторизует HR-аккаунт и открывает общий отчёт.

Данные для входа:

```text
Email: demo.hr@project.local
Пароль: Demo12345
```

Рабочий маршрут:
1. Открыть главную страницу и объяснить назначение системы.
2. Открыть обзор процесса и общий отчёт по кандидатам.
3. Открыть отчёт сильного кандидата и посмотреть оценку по вопросам.
4. Вернуться в `Панель HR`, проверить ссылку кандидата и статистику.
5. Открыть `Создать вакансию`, вставить пример и запустить генерацию нового теста.

Приложение больше не зависит от внешних CSS/JS CDN для основных экранов: интерфейс находится в `static/css/styles.css`, поэтому его можно показывать без интернета.

Система позволяет:
- зарегистрировать HR-пользователя и войти в панель управления;
- создать вакансию по текстовому описанию;
- автоматически извлечь навыки и сгенерировать вопросы;
- дать кандидату ссылку на прохождение опроса;
- оценить ответы кандидата и собрать итоговый отчет;
- просматривать результаты по каждой вакансии.

## Как работает приложение

1. HR регистрируется и входит в систему.
2. В панели создается новая вакансия через описание текста вакансии.
3. Приложение пытается через Ollama извлечь навыки и сформировать вопросы.
4. Если Ollama недоступна, используются встроенные fallback-механизмы:
	- навыки определяются по ключевым словам;
	- вопросы создаются по шаблону;
	- итоговый отчет формируется без LLM.
5. Кандидат открывает ссылку формата /test/<vacancy_id> и отвечает на вопросы.
6. Ответы оцениваются, сохраняются в PostgreSQL и отображаются на странице результата.
7. HR может посмотреть общий отчет по всем кандидатам на вакансию или провести ручную оценку.

## Зависимости среды

Для полной работы нужны:
- Python 3.10+ рекомендуется;
- PostgreSQL для основного режима или встроенный SQLite fallback для быстрого локального запуска;
- Ollama для LLM-функций;
- модель Ollama, указанная в OLLAMA_MODEL, по умолчанию mistral.

На Windows папка ai2 в этом репозитории не подходит как готовое окружение: это Linux-style virtualenv. Для Windows создайте отдельное окружение.

## Переменные окружения

Создайте файл .env по образцу из .env.example.

Используются параметры:
- SECRET_KEY
- PUBLIC_BASE_URL
- APP_HOST
- APP_PORT
- OPEN_BROWSER
- DB_ENGINE
- OLLAMA_ENABLED
- OLLAMA_MODEL
- DB_NAME
- DB_USER
- DB_PASSWORD
- DB_HOST
- DB_PORT
- SQLITE_PATH

Если переменные не заданы, приложение использует значения по умолчанию:
- APP_HOST=127.0.0.1
- APP_PORT=5000
- OPEN_BROWSER=1
- DB_ENGINE=auto

`PUBLIC_BASE_URL` задаёт внешний адрес для ссылок кандидатов. Для удалённого показа пример:

```text
PUBLIC_BASE_URL=http://185.154.75.113:5000
```
- OLLAMA_ENABLED=1
- OLLAMA_MODEL=mistral
- DB_NAME=vacmatch
- DB_USER=vacmatch_user
- DB_PASSWORD=vacmatchvacmatch
- DB_HOST=localhost
- DB_PORT=5432
- SQLITE_PATH=vacmatch.db

Режимы БД:
- DB_ENGINE=auto: сначала PostgreSQL, при недоступности автоматический переход на SQLite;
- DB_ENGINE=postgres: использовать только PostgreSQL;
- DB_ENGINE=sqlite: использовать только локальный файл SQLite.

## Запуск

### Windows PowerShell

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python app.py
```

### Linux/macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python app.py
```

После запуска приложение доступно по адресу:

http://localhost:5000

Для локального старта PostgreSQL теперь необязателен: если сервер базы не найден, приложение создаст локальный файл SQLite и продолжит работу.

## Подготовка PostgreSQL

Пример минимальной настройки:

```sql
CREATE DATABASE vacmatch;
CREATE USER vacmatch_user WITH PASSWORD 'vacmatchvacmatch';
GRANT ALL PRIVILEGES ON DATABASE vacmatch TO vacmatch_user;
```

Таблицы создаются автоматически при успешном подключении приложения к базе.

## Подготовка Ollama

Установите Ollama, затем выполните:

```bash
ollama pull mistral
ollama serve
```

Если нужен более лёгкий вариант, укажите в .env:

```env
OLLAMA_MODEL=tinyllama
```

И скачайте именно эту модель:

```bash
ollama pull tinyllama
ollama serve
```

Без Ollama приложение все равно запускается, но AI-функции работают в упрощенном режиме.

## Сборка для любого компьютера через Docker

Самый переносимый вариант запуска этого проекта: Docker Compose.

Требования:
- Docker Desktop или Docker Engine;
- свободный порт 5000;
- опционально Ollama на хосте или в отдельном контейнере, если нужен полноценный LLM-режим.

Запуск:

```bash
docker compose up --build
```

После сборки будут подняты:
- контейнер web с Flask-приложением;
- контейнер db с PostgreSQL 16.

Адрес приложения:

http://localhost:5000

Остановка:

```bash
docker compose down
```

Остановка с удалением тома базы:

```bash
docker compose down -v
```

Если нужен Ollama, можно пробросить адрес сервиса через переменную окружения OLLAMA_HOST и доработать compose под отдельный контейнер. В текущей сборке приложение стабильно работает и без Ollama за счет fallback-логики.

## Быстрый запуск на Windows

В корне проекта добавлены готовые скрипты:

- start.bat — открывает launcher UI;
- start_windows.ps1 — backend-скрипт подготовки окружения и ручного запуска.

Рекомендуемый вариант:

```bat
start.bat
```

Логика работы:
- по умолчанию открывается desktop launcher UI;
- из UI можно запустить сервер локально, через Docker или в онлайн-режиме через Cloudflare Tunnel, открыть приложение в браузере и включить или выключить Ollama для тестов;
- если для локального запуска нужен proxy, start.bat пытается определить его автоматически;
- при локальном запуске автоматически создается .venv, проверяются зависимости, и pip запускается только если чего-то не хватает;
- если найден старый или несовместимый .venv, скрипт пересоздает его на поддерживаемом Python 64-bit;
- если найден конфликтный proxy для pip, стартовый скрипт очищает proxy-переменные окружения перед установкой пакетов;
- если файла .env нет, он создается из .env.example.

Режимы локального запуска:
- единый рекомендуемый запуск: start.bat;
- launcher UI через PowerShell: powershell -ExecutionPolicy Bypass -File .\start_windows.ps1 -ShowLauncher;
- обычный локальный режим: powershell -ExecutionPolicy Bypass -File .\start_windows.ps1 -ForceLocal;
- Docker-режим: powershell -ExecutionPolicy Bypass -File .\start_windows.ps1 -ForceDocker;
- proxy-режим: powershell -ExecutionPolicy Bypass -File .\start_windows.ps1 -ForceLocal -UseProxy;
- proxy-режим с явным адресом: powershell -ExecutionPolicy Bypass -File .\start_windows.ps1 -ForceLocal -UseProxy -ProxyUrl http://127.0.0.1:10801.

### Онлайн-доступ через launcher UI

В launcher UI добавлен режим `Онлайн`. Он поднимает локальный сервер и публикует его во внешний интернет через Cloudflare Quick Tunnel.

Что нужно:
- установленный `cloudflared`;
- свободный порт 5000;
- запущенный launcher UI.

Установка на Windows:

```powershell
winget install Cloudflare.cloudflared
```

Как использовать:
- открыть `start.bat`;
- выбрать режим `Онлайн`;
- нажать `Запустить сервер`;
- дождаться строки `Онлайн-ссылка: https://...trycloudflare.com`;
- открыть эту ссылку на телефоне или отправить ее другому человеку.

Ограничения режима:
- ссылка временная и меняется при каждом новом запуске;
- для постоянного домена нужен отдельный Cloudflare Tunnel с авторизацией;
- если `cloudflared` не установлен, launcher покажет ошибку и команду установки.

## Перенос на другой компьютер

Для быстрого переноса проекта подготовлены отдельные файлы:

- SETUP_OTHER_PC.md — полная пошаговая инструкция по установке на другом компьютере;
- postgres_setup.sql — SQL-скрипт для создания пользователя и базы данных PostgreSQL под проект;
- .env.example — шаблон переменных окружения;
- start.bat — единая точка запуска приложения.

Рекомендуемый порядок:

1. Установить зависимости по инструкции из SETUP_OTHER_PC.md.
2. При необходимости создать базу через postgres_setup.sql.
3. Проверить или заполнить .env.
4. Запустить проект через start.bat.

Важно:
- Docker-режим не требует локальной установки PostgreSQL;
- локальный режим требует доступный Python, а PostgreSQL нужен только если вы хотите явно использовать PostgreSQL вместо SQLite fallback;
- Ollama для локального или контейнерного запуска необязательна, приложение может работать в fallback-режиме.

Если при локальном запуске появляются предупреждения вида Retry или ReadTimeoutError для pypi.org, это обычно означает медленное или нестабильное соединение с PyPI. Само предупреждение не критично, если после него установка продолжается. Проблемой считается только финальная ошибка pip. В стартовом скрипте уже увеличены timeout и retries, чтобы снизить вероятность такого сбоя.

Если pip падает с ProxyError, проверьте системный proxy Windows командой:

```powershell
netsh winhttp show proxy
```

Если там указан локальный proxy вроде 127.0.0.1:10801, а такой proxy у вас реально не работает, сбросьте его:

```powershell
netsh winhttp reset proxy
```

После этого повторите запуск через start.bat или через start_windows.ps1 с флагом -ForceLocal.

Если proxy вам действительно нужен для выхода в сеть, используйте start_windows.ps1 с флагами -ForceLocal -UseProxy. Скрипт возьмет proxy из аргумента -ProxyUrl, из HTTPS_PROXY/HTTP_PROXY или из системного WinHTTP proxy.

Если локальный .venv был когда-то создан на Python 3.8 32-bit, стартовый скрипт пересоздаст его автоматически на более подходящем Python, например 3.12 64-bit. При ошибке установки зависимостей приложение больше не запускается поверх неполного окружения.

## Быстрая проверка работоспособности

После запуска откройте:

- http://localhost:5000/
- http://localhost:5000/docs
- http://localhost:5000/health

Маршрут /health возвращает JSON со статусами базы данных и Ollama.

Ожидаемые состояния:
- status=ok: база данных доступна;
- database_engine=sqlite: локальный fallback активирован;
- database_engine=postgres|status=ok: Docker или внешний PostgreSQL работают штатно;
- status=degraded: сервер поднят, но выбранная база данных недоступна;
- ollama=disabled: модель вручную отключена для тестов через UI или .env;
- ollama=unavailable: LLM-функции работают через fallback.

## Что было проверено

По состоянию на текущую проверку:
- синтаксических ошибок в app.py не найдено;
- проект был завязан на внешний PostgreSQL и Ollama сильнее, чем нужно для локального старта;
- добавлен автоматический SQLite fallback для Windows и других локальных запусков без PostgreSQL;
- папка ai2 содержит не Windows-совместимое окружение.
- добавлена Docker-сборка, которая убирает зависимость от локальной установки Python и PostgreSQL.

## Основные маршруты

- GET /
- GET /docs
- GET /health
- GET, POST /login
- GET, POST /signup
- GET /dashboard
- GET, POST /vacancy/new
- GET, POST /test/<vacancy_id>
- GET /test/result/<session_id>
- GET /vacancy/<vacancy_id>/result
- GET, POST /vacancy/<vacancy_id>/evaluate
- POST /vacancy/<vacancy_id>/delete

## Ограничения

- SQLite fallback рассчитан на локальный запуск и рабочий пример, а PostgreSQL остаётся основным вариантом для полноценной среды;
- без Ollama качество генерации и итоговых summary ниже, хотя приложение не падает;
- README фиксирует фактическое состояние проекта и зависимостей на момент проверки.
