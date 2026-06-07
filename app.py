from concurrent.futures import ThreadPoolExecutor
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file
import os
import sqlite3
import re
import sys
from functools import wraps
from datetime import datetime
import importlib
import json
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix

try:
    import psycopg
    from psycopg import OperationalError as PsycopgOperationalError
except ImportError:
    psycopg = None

    class PsycopgOperationalError(Exception):
        pass

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args, **kwargs):
        return False

OLLAMA_IMPORT_FAILED = False
OLLAMA_CLIENT = None

# Настройка пула потоков
executor = ThreadPoolExecutor(max_workers=4)


def get_resource_root():
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def get_runtime_root():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def get_data_root():
    if getattr(sys, 'frozen', False):
        local_app_data = os.getenv('LOCALAPPDATA') or os.path.expanduser('~')
        data_root = os.path.join(local_app_data, 'VacMatchAI')
        os.makedirs(data_root, exist_ok=True)
        return data_root
    return get_runtime_root()


def resource_path(*parts):
    return os.path.join(get_resource_root(), *parts)


def runtime_path(*parts):
    return os.path.join(get_runtime_root(), *parts)


def data_path(*parts):
    return os.path.join(get_data_root(), *parts)


def env_flag(name, default='0'):
    return os.getenv(name, default).strip().lower() in {'1', 'true', 'yes', 'on'}

# Загрузка переменных окружения
load_dotenv(runtime_path('.env'))

app = Flask(__name__, template_folder=resource_path('templates'), static_folder=resource_path('static'))
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)
app.secret_key = os.getenv('SECRET_KEY', 'default-secret-key')
DB_ENGINE = os.getenv('DB_ENGINE', 'auto').lower()
SQLITE_PATH = os.getenv('SQLITE_PATH', data_path('vacmatch.db'))
ACTIVE_DB_ENGINE = 'sqlite'

DB_CONFIG = {
    'dbname': os.getenv('DB_NAME', 'vacmatch'),
    'user': os.getenv('DB_USER', 'vacmatch_user'),
    'password': os.getenv('DB_PASSWORD', 'vacmatchvacmatch'),
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': os.getenv('DB_PORT', '5432')
}

OLLAMA_MODEL = os.getenv('OLLAMA_MODEL', 'mistral')
OLLAMA_ENABLED = env_flag('OLLAMA_ENABLED', '1')
OLLAMA_TIMEOUT = float(os.getenv('OLLAMA_TIMEOUT', '6'))
MAX_ANSWER_LENGTH = int(os.getenv('MAX_ANSWER_LENGTH', '2500'))
DB_READY = False


def normalize_public_base_url(value):
    value = (value or '').strip().rstrip('/')
    if not value:
        return ''
    if not re.match(r'^https?://', value, re.IGNORECASE):
        value = f'http://{value}'
    return value.rstrip('/')


PUBLIC_BASE_URL = normalize_public_base_url(os.getenv('PUBLIC_BASE_URL', ''))


def public_url_for(endpoint, **values):
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL}{url_for(endpoint, **values)}"
    return url_for(endpoint, _external=True, **values)


def candidate_test_public_url(vacancy_id):
    return public_url_for('candidate_test', vacancy_id=vacancy_id)

DEMO_EMAIL = 'demo.hr@project.local'
DEMO_PASSWORD = 'Demo12345'
DEMO_VACANCY_TITLE = 'Python Backend Developer'
DEMO_VACANCY_TEXT = '''Middle Python backend developer

Задачи:
- разработка REST API для HR-сервиса;
- интеграция с PostgreSQL и Redis;
- контейнеризация приложения через Docker;
- написание unit и integration тестов;
- участие в code review и декомпозиции задач.

Требования:
- уверенный Python 3;
- Flask или FastAPI;
- SQL и оптимизация запросов;
- Git, Docker, базовое понимание CI/CD;
- умение объяснять технические решения простым языком.
'''

DEMO_QUESTIONS = [
    ('Python', 'Опишите, как вы проектируете структуру Flask-приложения для REST API и где размещаете бизнес-логику.', 5),
    ('SQL и PostgreSQL', 'Как бы вы нашли и оптимизировали медленный SQL-запрос в PostgreSQL?', 5),
    ('Docker', 'Расскажите, как вы контейнеризуете backend-приложение и какие параметры выносите в переменные окружения.', 4),
    ('Тестирование', 'Какие виды тестов вы написали бы для сервиса регистрации и авторизации пользователей?', 4),
    ('Коммуникация', 'Как вы объясните нетехническому заказчику причину задержки задачи и предложите план решения?', 3),
]

DEMO_CANDIDATES = [
    {
        'name': 'Анна Сергеева',
        'answers': [
            ('Я разделяю Flask-проект на routes, services, repositories и templates. Валидация и бизнес-правила находятся в service-слое, а работа с базой данных изолирована в repository-слое. Это упрощает тестирование и поддержку REST API.', 0.92),
            ('Сначала смотрю EXPLAIN ANALYZE, проверяю индексы, фильтры, сортировки и количество строк. Затем добавляю индекс, переписываю JOIN или выношу тяжёлую агрегацию. После изменений сравниваю план и время выполнения.', 0.88),
            ('Создаю Dockerfile, фиксирую зависимости, пробрасываю порт и настраиваю переменные окружения через .env или docker compose. Секреты не храню в коде, а состояние базы выношу в volume.', 0.86),
            ('Покрыл бы unit-тестами валидацию и хеширование пароля, integration-тестами signup/login и smoke-тестом полный пользовательский сценарий. Для негативных кейсов проверил бы неверный пароль и повтор email.', 0.9),
            ('Я коротко объясню причину, покажу влияние на срок и предложу варианты: урезать объём, добавить ресурс или перенести часть задач. Важно дать заказчику понятный выбор и обновлённый план.', 0.84),
        ],
    },
    {
        'name': 'Игорь Петров',
        'answers': [
            ('Обычно пишу всё в одном файле, если проект небольшой. Потом можно разделить, если станет сложно.', 0.42),
            ('Я бы попробовал добавить индекс и посмотреть, стало ли быстрее. С EXPLAIN работал мало.', 0.36),
            ('Docker запускал через готовые инструкции, сам Dockerfile писал редко.', 0.28),
            ('Проверил бы вручную регистрацию и вход. Автоматические тесты писал только для простых функций.', 0.3),
            ('Сказал бы, что задача сложнее, чем ожидалось, и нужно больше времени.', 0.38),
        ],
    },
]

SHOWCASE_GENERATION_SPECS = [
    {
        'key': 'python',
        'title': 'Python Backend Developer',
        'profile': 'balanced',
        'description': 'Backend-разработка REST API, работа с базой данных, контейнеризация и тестирование.',
        'text': DEMO_VACANCY_TEXT,
    },
    {
        'key': 'frontend',
        'title': 'Frontend Developer React/TypeScript',
        'profile': 'case',
        'description': 'Клиентские интерфейсы, формы, таблицы, адаптивная верстка и интеграция с REST API.',
        'text': '''Frontend Developer React/TypeScript

Задачи:
- разработка личного кабинета и административных интерфейсов;
- работа с React, TypeScript, формами, таблицами и фильтрами;
- интеграция с REST API;
- оптимизация скорости загрузки и удобства интерфейса;
- участие в дизайн-ревью и исправлении UI-ошибок.

Требования:
- React, TypeScript, HTML, CSS;
- понимание состояния приложения и клиентской маршрутизации;
- опыт адаптивной верстки;
- умение тестировать интерфейс и объяснять технические решения.
''',
    },
    {
        'key': 'qa',
        'title': 'QA Engineer',
        'profile': 'junior',
        'description': 'Проверка пользовательских сценариев, тест-кейсы, баг-репорты и регресс.',
        'text': '''QA Engineer

Задачи:
- тестирование веб-приложений и пользовательских сценариев;
- составление чек-листов и тест-кейсов;
- оформление баг-репортов;
- проверка исправлений и регресс-тестирование;
- взаимодействие с разработчиками и аналитиками.

Требования:
- понимание видов тестирования;
- опыт работы с DevTools, Postman или аналогами;
- базовое понимание SQL;
- внимательность, структурное мышление и аккуратная коммуникация.
''',
    },
    {
        'key': 'data',
        'title': 'Data Analyst',
        'profile': 'case',
        'description': 'SQL, отчёты, BI-дашборды, качество данных и понятные выводы для бизнеса.',
        'text': '''Data Analyst

Задачи:
- сбор и обработка данных из разных источников;
- построение отчётов и дашбордов;
- анализ продуктовых и бизнес-метрик;
- поиск причин отклонений в данных;
- подготовка понятных выводов для руководителя.

Требования:
- SQL, Excel, базовая статистика;
- Power BI, Tableau или аналогичные BI-инструменты;
- умение проверять качество данных;
- способность объяснять выводы простым языком.
''',
    },
    {
        'key': 'devops',
        'title': 'DevOps Engineer',
        'profile': 'senior',
        'description': 'CI/CD, Docker, Linux, мониторинг, релизы и надёжность сервисов.',
        'text': '''DevOps Engineer

Задачи:
- настройка CI/CD для веб-сервисов;
- контейнеризация приложений через Docker и docker compose;
- настройка Linux-серверов, nginx и переменных окружения;
- мониторинг доступности и логирование ошибок;
- безопасный выпуск релизов и откат изменений.

Требования:
- Linux, Bash, Docker, GitLab CI или GitHub Actions;
- понимание сетей, портов и reverse proxy;
- опыт диагностики инцидентов;
- умение описывать риски и план восстановления.
''',
    },
]

SHOWCASE_EVALUATION_EXAMPLES = [
    {
        'title': 'Сильный ответ',
        'tone': 'good',
        'question': 'Как бы вы нашли и оптимизировали медленный SQL-запрос в PostgreSQL?',
        'answer': 'Сначала посмотрю EXPLAIN ANALYZE, проверю индексы, фильтры и объём строк. Затем сравню план выполнения, добавлю нужный индекс или перепишу JOIN. После изменения измерю время запроса и проверю, что результат не изменился.',
    },
    {
        'title': 'Слабый ответ',
        'tone': 'weak',
        'question': 'Как бы вы нашли и оптимизировали медленный SQL-запрос в PostgreSQL?',
        'answer': 'Попробую добавить индекс и посмотреть, стало ли лучше. Если не получится, спрошу у более опытного разработчика.',
    },
]


def get_ollama_client():
    global OLLAMA_CLIENT, OLLAMA_IMPORT_FAILED

    if not OLLAMA_ENABLED:
        return None

    if OLLAMA_CLIENT is not None:
        return OLLAMA_CLIENT

    if OLLAMA_IMPORT_FAILED:
        return None

    try:
        ollama_module = importlib.import_module('ollama')
        if hasattr(ollama_module, 'Client'):
            OLLAMA_CLIENT = ollama_module.Client(timeout=OLLAMA_TIMEOUT)
        else:
            OLLAMA_CLIENT = ollama_module
        return OLLAMA_CLIENT
    except ImportError:
        OLLAMA_IMPORT_FAILED = True
        return None

POSTGRES_SCHEMA = [
    '''
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            email VARCHAR(100) UNIQUE NOT NULL,
            password VARCHAR(200) NOT NULL
        );
    ''',
    '''
        CREATE TABLE IF NOT EXISTS vacancy (
            id SERIAL PRIMARY KEY,
            title VARCHAR(256),
            text TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status VARCHAR(64) DEFAULT 'В работе'
        );
    ''',
    '''
        CREATE TABLE IF NOT EXISTS question (
            id SERIAL PRIMARY KEY,
            vacancy_id INTEGER REFERENCES vacancy(id) ON DELETE CASCADE,
            skill VARCHAR(128),
            question TEXT,
            importance INTEGER
        );
    ''',
    '''
        CREATE TABLE IF NOT EXISTS test_session (
            id SERIAL PRIMARY KEY,
            vacancy_id INTEGER REFERENCES vacancy(id),
            full_name VARCHAR(200),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            summary TEXT
        );
    ''',
    '''
        CREATE TABLE IF NOT EXISTS answer (
            id SERIAL PRIMARY KEY,
            question_id INTEGER REFERENCES question(id),
            session_id INTEGER REFERENCES test_session(id),
            answer TEXT,
            score FLOAT
        );
    ''',
]

SQLITE_SCHEMA = [
    '''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        );
    ''',
    '''
        CREATE TABLE IF NOT EXISTS vacancy (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            text TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'В работе'
        );
    ''',
    '''
        CREATE TABLE IF NOT EXISTS question (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vacancy_id INTEGER REFERENCES vacancy(id) ON DELETE CASCADE,
            skill TEXT,
            question TEXT,
            importance INTEGER
        );
    ''',
    '''
        CREATE TABLE IF NOT EXISTS test_session (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vacancy_id INTEGER REFERENCES vacancy(id) ON DELETE CASCADE,
            full_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            summary TEXT
        );
    ''',
    '''
        CREATE TABLE IF NOT EXISTS answer (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id INTEGER REFERENCES question(id) ON DELETE CASCADE,
            session_id INTEGER REFERENCES test_session(id) ON DELETE CASCADE,
            answer TEXT,
            score REAL
        );
    ''',
]


class SQLiteCursorWrapper:
    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, query, params=None):
        sqlite_query = query.replace('%s', '?')
        if params is None:
            self._cursor.execute(sqlite_query)
        else:
            self._cursor.execute(sqlite_query, params)
        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def close(self):
        self._cursor.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


class SQLiteConnectionWrapper:
    def __init__(self, database_path):
        self._conn = sqlite3.connect(database_path, check_same_thread=False)
        self._conn.execute('PRAGMA foreign_keys = ON')

    def cursor(self):
        return SQLiteCursorWrapper(self._conn.cursor())

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def __enter__(self):
        self._conn.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self._conn.__exit__(exc_type, exc_val, exc_tb)


def get_db_connection(engine=None):
    selected_engine = (engine or ACTIVE_DB_ENGINE).lower()
    if selected_engine in ('postgres', 'postgresql'):
        if psycopg is None:
            raise RuntimeError('psycopg is not installed')
        return psycopg.connect(**DB_CONFIG)
    return SQLiteConnectionWrapper(SQLITE_PATH)


def build_password_hash(password):
    return generate_password_hash(password, method='pbkdf2:sha256')


def is_unique_violation(error):
    if isinstance(error, sqlite3.IntegrityError):
        return 'unique' in str(error).lower()

    sqlstate = getattr(error, 'sqlstate', None)
    if sqlstate == '23505':
        return True

    return 'duplicate key value' in str(error).lower()


def initialize_schema(connection, schema_statements):
    with connection:
        with connection.cursor() as cur:
            for statement in schema_statements:
                cur.execute(statement)


def repair_score_range(connection):
    with connection:
        with connection.cursor() as cur:
            cur.execute('UPDATE answer SET score = 0 WHERE score < 0')
            cur.execute('UPDATE answer SET score = 1 WHERE score > 1')


STOP_WORDS = {
    'и', 'в', 'во', 'на', 'с', 'со', 'по', 'под', 'за', 'из', 'у', 'к', 'ко', 'от', 'до', 'для',
    'что', 'как', 'какой', 'какая', 'какие', 'зачем', 'почему', 'где', 'когда', 'ли', 'это',
    'есть', 'про', 'об', 'обо', 'the', 'a', 'an', 'to', 'of', 'for', 'in', 'on', 'and', 'or',
    'you', 'your', 'with', 'is', 'are', 'do', 'does', 'know', 'about'
}

PROFESSIONAL_RELEVANCE_STEMS = {
    'python', 'питон', 'django', 'flask', 'fastapi', 'pytest', 'asyncio', 'pandas',
    'sql', 'postgres', 'mysql', 'sqlite', 'баз', 'данн', 'запрос', 'таблиц', 'индекс', 'транзакц',
    'backend', 'бекенд', 'бэкенд', 'сервер', 'api', 'rest', 'graphql', 'http', 'endpoint',
    'docker', 'container', 'контейнер', 'git', 'commit', 'branch', 'merge', 'ci', 'cd', 'deploy', 'депло',
    'код', 'скрипт', 'модул', 'сервис', 'архитектур', 'алгоритм', 'лог', 'ошибк', 'debug', 'дебаг',
    'тест', 'провер', 'реализ', 'разработ', 'настро', 'исправ', 'оптимиз', 'автоматиз',
    'проект', 'задач', 'результат', 'ответствен', 'метрик', 'пользовател', 'клиент', 'команд',
    'писал', 'парсер', 'отчет', 'отчёт', 'бот', 'учет', 'учёт', 'заявк', 'интеграц'
}

OFF_TOPIC_STEMS = {
    'каш', 'куша', 'еда', 'суп', 'борщ', 'картош', 'чай', 'кофе',
    'спал', 'гуля', 'смотрел', 'мультик', 'песн', 'шутк', 'бред'
}

ACTION_RELEVANCE_STEMS = {
    'анализ', 'провер', 'сравн', 'созд', 'настро', 'раздел', 'проектир', 'измер',
    'исправ', 'оптимиз', 'перепиш', 'добав', 'валид', 'логир', 'тестир', 'декомпоз',
    'объясн', 'соглас', 'документ', 'контейнериз', 'интегрир', 'монитор'
}

RESULT_RELEVANCE_STEMS = {
    'результат', 'метрик', 'скорост', 'надёж', 'качест', 'срок', 'рис', 'план',
    'производител', 'безопас', 'поддерж', 'откат', 'проверка', 'вывод'
}

STRUCTURE_MARKERS = [
    'сначала', 'затем', 'после', 'после этого', 'на первом шаге', 'в итоге',
    'проверю', 'измерю', 'сравню', 'объясню', 'зафиксирую'
]

TECHNICAL_MARKERS = {
    'api', 'rest', 'sql', 'postgresql', 'postgres', 'index', 'join', 'explain', 'docker',
    'compose', 'linux', 'nginx', 'ci/cd', 'git', 'react', 'typescript', 'pytest', 'unit',
    'integration', 'devtools', 'postman', 'dashboard', 'bi', 'excel', 'redis', 'jwt'
}


def tokenize_text(value):
    return re.findall(r'[a-zA-Zа-яА-Я0-9+#.-]+', (value or '').lower())


def keyword_tokens(value):
    tokens = []
    for token in tokenize_text(value):
        normalized = token.strip('.,:;!?()[]{}"\'`')
        if len(normalized) > 2 and normalized not in STOP_WORDS:
            tokens.append(normalized)
    return tokens


def looks_like_gibberish(answer, answer_tokens):
    compact = re.sub(r'\s+', '', (answer or '').lower())
    if len(set(compact)) <= 3 and len(compact) >= 8:
        return True

    if re.search(r'(.)\1{5,}', compact):
        return True

    if answer_tokens:
        unique_ratio = len(set(answer_tokens)) / len(answer_tokens)
        if len(answer_tokens) >= 4 and unique_ratio < 0.45:
            return True
        if len(answer_tokens) >= 60 and unique_ratio < 0.45:
            return True

    alphabetic_tokens = [token for token in answer_tokens if re.search(r'[a-zA-Zа-яА-Я]', token)]
    if alphabetic_tokens and all(len(token) <= 2 for token in alphabetic_tokens):
        return True

    return False


def count_stem_matches(tokens, stems):
    matched = set()
    for token in tokens:
        for stem in stems:
            if token == stem or token.startswith(stem):
                matched.add(token)
                break
    return len(matched)


def count_exact_marker_matches(tokens, markers):
    token_set = set(tokens)
    return sum(1 for marker in markers if marker in token_set)


def count_phrase_matches(text, phrases):
    normalized = normalize_skill_text(text)
    return sum(1 for phrase in phrases if phrase in normalized)


def count_soft_overlap(answer_tokens, question_tokens):
    answer_set = set(answer_tokens)
    question_set = set(question_tokens)
    exact = answer_set & question_set
    overlap = len(exact)
    used_answers = set(exact)

    for question_token in question_set - exact:
        if len(question_token) < 5:
            continue
        prefix = question_token[:5]
        for answer_token in answer_set - used_answers:
            if len(answer_token) >= 5 and answer_token.startswith(prefix):
                overlap += 1
                used_answers.add(answer_token)
                break

    return overlap


def answer_quality_gate(question, answer):
    normalized_answer = (answer or '').strip()
    if len(normalized_answer) < 10:
        return 0.0, 0.0

    answer_tokens = keyword_tokens(normalized_answer)
    question_tokens = keyword_tokens(question)

    if len(answer_tokens) < 3:
        return 0.0, 0.0

    if looks_like_gibberish(normalized_answer, answer_tokens):
        return 0.0, 0.0

    alpha_chars = [char for char in normalized_answer.lower() if char.isalpha()]
    if len(alpha_chars) >= 20:
        vowels = set('aeiouy' + '\u0430\u0435\u0451\u0438\u043e\u0443\u044b\u044d\u044e\u044f')
        vowel_ratio = sum(1 for char in alpha_chars if char in vowels) / len(alpha_chars)
        if vowel_ratio < 0.16:
            return 0.0, 0.0

    answer_set = set(answer_tokens)
    question_set = set(question_tokens)
    overlap = answer_set & question_set
    new_terms = answer_set - question_set
    soft_overlap = count_soft_overlap(answer_tokens, question_tokens)
    professional_hits = count_stem_matches(answer_tokens, PROFESSIONAL_RELEVANCE_STEMS)
    off_topic_hits = count_stem_matches(answer_tokens, OFF_TOPIC_STEMS)

    if off_topic_hits and professional_hits <= 1:
        return 0.0, 0.0

    if question_set and soft_overlap == 0 and professional_hits == 0:
        return 0.0, 0.0

    if question_set and soft_overlap <= 1 and professional_hits <= 1 and len(answer_tokens) < 12:
        return 0.0, 0.0

    if question_set and overlap and len(new_terms) < 3 and len(overlap) / max(1, len(answer_set)) >= 0.6:
        return 0.05, 0.15

    max_score = 1.0
    if len(normalized_answer) > MAX_ANSWER_LENGTH:
        max_score = 0.35

    if len(answer_tokens) >= 80 and len(answer_set) / len(answer_tokens) < 0.7:
        max_score = min(max_score, 0.25)

    return None, max_score


def count_relevant_overlap(answer_tokens, question_tokens):
    answer_set = set(answer_tokens)
    question_set = set(question_tokens)
    exact = answer_set & question_set
    overlap = len(exact)
    used_answers = set(exact)

    for question_token in question_set - exact:
        if len(question_token) < 5:
            continue
        prefix = question_token[:5]
        for answer_token in answer_set - used_answers:
            if len(answer_token) >= 5 and answer_token.startswith(prefix):
                overlap += 1
                used_answers.add(answer_token)
                break

    return overlap


def fallback_evaluate_answer(question, answer):
    normalized_answer = (answer or '').strip()
    forced_score, max_score = answer_quality_gate(question, normalized_answer)
    if forced_score is not None:
        return forced_score

    answer_tokens = keyword_tokens(normalized_answer)
    question_tokens = keyword_tokens(question)
    normalized_lower = normalize_skill_text(normalized_answer)

    overlap = count_relevant_overlap(answer_tokens, question_tokens)
    overlap_ratio = overlap / max(1, len(set(question_tokens))) if question_tokens else 0
    professional_hits = count_stem_matches(answer_tokens, PROFESSIONAL_RELEVANCE_STEMS)
    action_hits = count_stem_matches(answer_tokens, ACTION_RELEVANCE_STEMS)
    result_hits = count_stem_matches(answer_tokens, RESULT_RELEVANCE_STEMS)
    technical_hits = count_exact_marker_matches(answer_tokens, TECHNICAL_MARKERS)
    structure_hits = count_phrase_matches(normalized_lower, STRUCTURE_MARKERS)

    score = 0.12
    score += min(0.2, len(answer_tokens) * 0.014)

    if question_tokens:
        score += min(0.28, overlap_ratio * 0.6)
        if overlap == 0:
            score -= 0.18
    else:
        score += 0.15

    score += min(0.2, professional_hits * 0.035)
    score += min(0.14, action_hits * 0.04)
    score += min(0.1, result_hits * 0.04)
    score += min(0.1, technical_hits * 0.035)
    score += min(0.08, structure_hits * 0.03)

    if any(token.isdigit() for token in answer_tokens):
        score += 0.05

    if len(answer_tokens) >= 14 and action_hits >= 2:
        score += 0.05
    if len(answer_tokens) >= 24 and result_hits >= 1:
        score += 0.06
    if len(answer_tokens) >= 34 and technical_hits >= 2:
        score += 0.05

    if len(answer_tokens) < 8:
        max_score = min(max_score, 0.45)
    if professional_hits <= 1 and action_hits <= 1:
        max_score = min(max_score, 0.55)
    if technical_hits == 0 and overlap <= 1 and len(answer_tokens) < 18:
        max_score = min(max_score, 0.42)
    if score >= 0.75 and result_hits == 0 and structure_hits == 0:
        score = min(score, 0.72)

    return clamp_score(min(max_score, score))


def format_display_date(value):
    if not value:
        return 'неизвестно'
    if hasattr(value, 'strftime'):
        return value.strftime('%d.%m.%Y')
    try:
        return datetime.fromisoformat(str(value)).strftime('%d.%m.%Y')
    except ValueError:
        return str(value)

def fallback_extract_skills(text):
    skill_map = {
        'python': 'Python',
        'sql': 'SQL',
        'postgresql': 'PostgreSQL',
        'postgres': 'PostgreSQL',
        'flask': 'Flask',
        'django': 'Django',
        'fastapi': 'FastAPI',
        'docker': 'Docker',
        'git': 'Git',
        'linux': 'Linux',
        'javascript': 'JavaScript',
        'typescript': 'TypeScript',
        'java': 'Java',
        'c++': 'C++',
        'qa': 'QA',
        'pytest': 'Pytest',
        'rest': 'REST API'
    }
    lower_text = text.lower()
    skills = [value for key, value in skill_map.items() if key in lower_text]
    if skills:
        return skills[:5]
    return ['Профильный опыт', 'Работа с данными', 'Коммуникация']

def infer_competency_label(question):
    text = normalize_skill_text(question)
    labels = [
        ('SQL и работа с данными', ['sql', 'postgresql', 'запрос', 'данн']),
        ('архитектура backend', ['flask', 'api', 'backend', 'бизнес-логик']),
        ('контейнеризация', ['docker', 'контейнер', 'окружен']),
        ('тестирование', ['тест', 'регистрац', 'авторизац']),
        ('коммуникация', ['заказчик', 'объясн', 'задержк', 'план']),
        ('качество интерфейса', ['интерфейс', 'react', 'frontend', 'верстк']),
        ('аналитика', ['метрик', 'дашборд', 'bi', 'отчёт']),
        ('эксплуатация', ['linux', 'ci/cd', 'nginx', 'релиз', 'монитор']),
    ]
    for label, markers in labels:
        if any(marker in text for marker in markers):
            return label
    return 'профильная компетенция'


def fallback_candidate_summary(answers, score):
    score = clamp_score(score)
    strong_answers = [infer_competency_label(question) for question, _, answer_score, _ in answers if clamp_score(answer_score) >= 0.75]
    weak_answers = [infer_competency_label(question) for question, _, answer_score, _ in answers if clamp_score(answer_score) < 0.45]
    if score >= 0.78:
        verdict = 'кандидат уверенно подходит для следующего этапа.'
        recommendation = 'пригласить на техническое интервью и проверить глубину опыта на реальном кейсе.'
    elif score >= 0.55:
        verdict = 'кандидат частично соответствует роли, но требует уточнений.'
        recommendation = 'провести короткое интервью по слабым зонам перед следующим этапом.'
    else:
        verdict = 'кандидату требуется дополнительная проверка перед продолжением отбора.'
        recommendation = 'не принимать решение без дополнительного интервью и практического задания.'

    strengths = ', '.join(dict.fromkeys(strong_answers[:3])) if strong_answers else 'сильные компетенции пока не подтверждены'
    weaknesses = ', '.join(dict.fromkeys(weak_answers[:3])) if weak_answers else 'критичных провалов не выявлено'
    return (
        f"Итоговая оценка: {round(score, 2)}\n"
        f"Вывод: {verdict}\n"
        f"Сильные стороны: {strengths}.\n"
        f"Зоны риска: {weaknesses}.\n"
        f"Рекомендация: {recommendation}"
    )

def get_ollama_status():
    if not OLLAMA_ENABLED:
        return 'disabled'

    ollama_client = get_ollama_client()
    if ollama_client is None:
        return 'not-installed'
    try:
        response = ollama_client.list()
        models = response.get('models', []) if isinstance(response, dict) else getattr(response, 'models', [])
        model_names = []
        for model in models:
            if isinstance(model, dict):
                name = model.get('model') or model.get('name')
            else:
                name = getattr(model, 'model', None) or getattr(model, 'name', None)
            if name:
                model_names.append(name)

        configured = OLLAMA_MODEL.strip()
        configured_latest = f'{configured}:latest'
        model_found = any(
            name == configured
            or name == configured_latest
            or (':' not in configured and name.split(':', 1)[0] == configured)
            for name in model_names
        )
        if not model_found:
            return 'model-missing'
        return 'ok'
    except Exception:
        return 'unavailable'

# ========== ИНИЦИАЛИЗАЦИЯ БД ==========
def init_db():
    global ACTIVE_DB_ENGINE, DB_READY

    preferred_engine = 'postgres' if DB_ENGINE in ('auto', 'postgres', 'postgresql') else 'sqlite'
    attempts = [preferred_engine]
    if DB_ENGINE == 'auto' and preferred_engine != 'sqlite':
        attempts.append('sqlite')

    last_error = None
    for engine in attempts:
        conn = None
        try:
            conn = get_db_connection(engine)
            schema = POSTGRES_SCHEMA if engine == 'postgres' else SQLITE_SCHEMA
            initialize_schema(conn, schema)
            repair_score_range(conn)
            ACTIVE_DB_ENGINE = engine
            DB_READY = True
            print(f"[База данных]: используется {engine}")
            return
        except (PsycopgOperationalError, RuntimeError, sqlite3.Error) as error:
            last_error = error
        finally:
            if conn is not None:
                conn.close()

    DB_READY = False
    ACTIVE_DB_ENGINE = 'sqlite'
    print(f"[Ошибка подключения к БД]: {last_error}")

init_db()

# ========== ЗАЩИТА ==========
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash("Войдите в систему", "error")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

FALLBACK_SKILL_RULES = [
    ('Python', 5, ['python', 'питон', 'django', 'flask', 'fastapi', 'pytest', 'asyncio', 'pandas']),
    ('JavaScript/TypeScript', 5, ['javascript', 'typescript', 'js', 'ts', 'react', 'vue', 'angular', 'node.js', 'nodejs', 'frontend', 'фронтенд']),
    ('Java', 5, ['java', 'spring', 'spring boot', 'hibernate', 'jvm']),
    ('C#/.NET', 5, ['c#', '.net', 'dotnet', 'asp.net', 'entity framework']),
    ('C++', 5, ['c++', 'cpp', 'qt', 'stl']),
    ('SQL и базы данных', 5, ['sql', 'postgresql', 'postgres', 'mysql', 'sqlite', 'oracle', 'ms sql', 'база данных', 'базы данных', 'субд']),
    ('API и интеграции', 4, ['api', 'rest', 'rest api', 'graphql', 'grpc', 'интеграции', 'webhook', 'http']),
    ('Docker и контейнеризация', 4, ['docker', 'compose', 'docker-compose', 'container', 'контейнер', 'kubernetes', 'k8s']),
    ('DevOps/CI-CD', 4, ['ci/cd', 'cicd', 'gitlab ci', 'github actions', 'jenkins', 'deploy', 'деплой', 'nginx']),
    ('Linux и администрирование', 4, ['linux', 'bash', 'shell', 'ubuntu', 'debian', 'centos', 'администрирование']),
    ('Git и командная работа', 3, ['git', 'github', 'gitlab', 'merge request', 'pull request', 'code review']),
    ('Тестирование и качество', 5, ['qa', 'test', 'тестирование', 'pytest', 'unittest', 'selenium', 'playwright', 'автотест']),
    ('Безопасность', 4, ['security', 'безопасность', 'auth', 'jwt', 'oauth', 'шифрование', 'уязвимость']),
    ('Аналитика данных', 4, ['аналитик', 'analytics', 'bi', 'power bi', 'tableau', 'excel', 'метрики', 'дашборд']),
    ('ML/AI', 4, ['machine learning', 'ml', 'ai', 'нейросеть', 'llm', 'nlp', 'computer vision', 'модель']),
    ('HR-процессы', 4, ['hr', 'кадры', 'подбор', 'рекрутинг', 'адаптация', 'персонал', 'собеседование']),
    ('Коммуникация', 3, ['коммуникация', 'переговоры', 'клиент', 'заказчик', 'презентация', 'документация']),
    ('Управление задачами', 3, ['scrum', 'agile', 'kanban', 'jira', 'трекер', 'планирование', 'оценка задач']),
]


def normalize_skill_text(text):
    return re.sub(r'\s+', ' ', (text or '').lower())


def add_unique_skill(skills, seen, name, importance, source_count):
    if name in seen:
        seen[name]['importance'] = max(seen[name]['importance'], importance)
        seen[name]['source_count'] += source_count
        return
    item = {'name': name, 'importance': importance, 'source_count': source_count}
    skills.append(item)
    seen[name] = item


def fallback_extract_skills(text):
    lower_text = normalize_skill_text(text)
    skills = []
    seen = {}

    for skill_name, importance, markers in FALLBACK_SKILL_RULES:
        matches = 0
        for marker in markers:
            pattern = r'(?<![\w+#.-])' + re.escape(marker.lower()) + r'(?![\w+#.-])'
            if re.search(pattern, lower_text):
                matches += 1
        if matches:
            add_unique_skill(skills, seen, skill_name, importance, matches)

    role_rules = [
        (['backend', 'бекенд', 'серверн'], 'Backend-разработка', 5),
        (['frontend', 'фронтенд', 'интерфейс'], 'Frontend-разработка', 5),
        (['fullstack', 'full-stack', 'фулстек'], 'Fullstack-разработка', 5),
        (['архитект', 'architecture', 'проектирование системы'], 'Архитектура решений', 5),
        (['тимлид', 'team lead', 'руководитель разработки'], 'Лидерство и наставничество', 4),
    ]
    for markers, skill_name, importance in role_rules:
        matches = sum(1 for marker in markers if marker in lower_text)
        if matches:
            add_unique_skill(skills, seen, skill_name, importance, matches)

    if not skills:
        return [
            {'name': 'Профильный опыт', 'importance': 4},
            {'name': 'Решение практических задач', 'importance': 4},
            {'name': 'Коммуникация', 'importance': 3},
        ]

    skills.sort(key=lambda item: (item['importance'], item['source_count']), reverse=True)
    return [{'name': item['name'], 'importance': item['importance']} for item in skills[:7]]


QUESTION_PROFILE_LABELS = {
    'balanced': 'смешанный профиль: опыт, практические решения и коммуникация',
    'case': 'практические кейсы: диагностика, выбор решения и проверка результата',
    'junior': 'junior/middle: базовые знания, типовые задачи и понятность объяснения',
    'senior': 'senior/lead: архитектура, риски, trade-offs, качество и наставничество',
}

QUESTION_PROFILE_TEMPLATES = {
    'balanced': [
        'Опишите реальный проект или задачу по направлению «{skill}». Какой был результат и ваша зона ответственности?',
        'Представьте, что в задаче по направлению «{skill}» появилась нестабильная ошибка. Как вы будете диагностировать причину и проверять исправление?',
        'Какие типичные ошибки в направлении «{skill}» вы встречали? Как вы их предотвращаете?',
        'Как бы вы объяснили выбор подхода или инструмента для задачи в области «{skill}»: какие плюсы, ограничения и альтернативы учли бы?',
        'Расскажите о ситуации, где вам пришлось улучшать качество, скорость или надежность решения в области «{skill}».',
    ],
    'case': [
        'В проекте возникла проблема в области «{skill}». Какие первые три шага диагностики вы выполните и какие данные соберёте?',
        'Предложите решение практической задачи в области «{skill}»: как построите план, проверите результат и снизите риск ошибки?',
        'Опишите кейс, где неверное использование направления «{skill}» могло привести к сбою. Как бы вы нашли и исправили причину?',
        'Как вы будете сравнивать два варианта решения задачи в области «{skill}»: какие критерии и метрики возьмёте?',
        'Что вы сделаете, если решение в области «{skill}» работает локально, но ломается в рабочей среде?',
    ],
    'junior': [
        'Объясните основные принципы направления «{skill}» простыми словами и приведите пример задачи, где это применяется.',
        'Какие базовые ошибки новичков в направлении «{skill}» вы знаете и как будете их избегать?',
        'Опишите небольшой учебный или рабочий пример, где вы применяли «{skill}». Что именно сделали сами?',
        'Какие вопросы вы зададите наставнику, если задача в области «{skill}» непонятна или требований не хватает?',
        'Как вы проверите, что выполненная вами задача в области «{skill}» работает корректно?',
    ],
    'senior': [
        'Как вы спроектируете решение в области «{skill}», чтобы оно масштабировалось и оставалось поддерживаемым?',
        'Какие риски, ограничения и компромиссы вы учитываете при выборе подхода в области «{skill}»?',
        'Как вы организуете ревью, стандарты и передачу знаний команде по направлению «{skill}»?',
        'Опишите случай, где вам пришлось менять архитектурное решение в области «{skill}». Что стало причиной и результатом?',
        'Какие метрики качества и надежности вы будете отслеживать для решения в области «{skill}»?',
    ],
}


def normalize_question_profile(value):
    return value if value in QUESTION_PROFILE_LABELS else 'balanced'


def clamp_importance(value, default=3):
    try:
        return max(1, min(5, int(value)))
    except (TypeError, ValueError):
        return default


def clamp_score(value, default=0.0):
    try:
        return round(max(0.0, min(1.0, float(value))), 2)
    except (TypeError, ValueError):
        return default


def fallback_question_for_skill(skill, index, question_profile='balanced'):
    skill_name = skill['name'] if isinstance(skill, dict) else str(skill)
    skill_context = {
        'SQL и базы данных': 'SQL и базы данных',
        'Backend-разработка': 'backend-разработка',
        'Frontend-разработка': 'frontend-разработка',
        'Fullstack-разработка': 'fullstack-разработка',
        'Docker и контейнеризация': 'Docker и контейнеризация',
        'API и интеграции': 'API и интеграции',
        'Git и командная работа': 'Git и командная работа',
        'Тестирование и качество': 'тестирование и контроль качества',
        'Linux и администрирование': 'Linux и администрирование',
        'Управление задачами': 'управление задачами',
        'HR-процессы': 'HR-процессы',
        'Аналитика данных': 'аналитика данных',
        'ML/AI': 'ML/AI',
        'Безопасность': 'безопасность приложения',
        'Архитектура решений': 'архитектура решений',
        'Лидерство и наставничество': 'лидерство и наставничество',
        'Профильный опыт': 'профильный опыт',
        'Решение практических задач': 'решение практических задач',
        'Практический опыт': 'практический опыт',
    }.get(skill_name, skill_name)
    importance = clamp_importance(skill.get('importance', 3)) if isinstance(skill, dict) else 3
    profile = normalize_question_profile(question_profile)
    templates = QUESTION_PROFILE_TEMPLATES[profile]
    return {
        'skill': skill_name,
        'question': templates[index % len(templates)].format(skill=skill_context),
        'importance': importance,
    }


def fallback_generate_questions(skills, question_profile='balanced'):
    normalized_skills = []
    for skill in skills:
        if isinstance(skill, dict):
            name = skill.get('name') or skill.get('skill')
            importance = clamp_importance(skill.get('importance', 3))
        else:
            name = str(skill)
            importance = 3
        if name:
            normalized_skills.append({'name': name, 'importance': importance})

    questions = [
        fallback_question_for_skill(skill, index, question_profile)
        for index, skill in enumerate(normalized_skills[:7])
    ]
    for extra_skill in [{'name': 'Практический опыт', 'importance': 4}, {'name': 'Коммуникация', 'importance': 3}]:
        if len(questions) >= 3:
            break
        questions.append(fallback_question_for_skill(extra_skill, len(questions), question_profile))
    return questions


def build_showcase_generation_variants():
    variants = []
    for spec in SHOWCASE_GENERATION_SPECS:
        skills = fallback_extract_skills(spec['text'])
        questions = fallback_generate_questions(skills, spec.get('profile', 'balanced'))[:3]
        variants.append({
            'key': spec['key'],
            'title': spec['title'],
            'description': spec['description'],
            'profile': QUESTION_PROFILE_LABELS.get(spec.get('profile'), QUESTION_PROFILE_LABELS['balanced']),
            'skills': [skill['name'] if isinstance(skill, dict) else str(skill) for skill in skills[:5]],
            'questions': questions,
        })
    return variants


def build_showcase_evaluation_examples():
    examples = []
    for item in SHOWCASE_EVALUATION_EXAMPLES:
        score = fallback_evaluate_answer(item['question'], item['answer'])
        if score >= 0.75:
            conclusion = 'Ответ раскрывает ход решения, инструменты, проверку результата и выглядит готовым для следующего этапа.'
        elif score >= 0.45:
            conclusion = 'Ответ по теме, но системе не хватает деталей, проверки результата и уверенной профессиональной аргументации.'
        else:
            conclusion = 'Ответ слишком общий: есть отдельные слова по теме, но мало действий, конкретики и подтверждения опыта.'

        examples.append({
            'title': item['title'],
            'tone': item['tone'],
            'question': item['question'],
            'answer': item['answer'],
            'score': score,
            'conclusion': conclusion,
        })
    return examples


# ========== LLM ==========
LLM_SYSTEM_PROMPT = """
Ты работаешь как аккуратный HR-техлид для системы первичного скрининга.
Не выдумывай лишних требований, не добавляй разговорный мусор, не возвращай Markdown.
Если нужен JSON, возвращай только валидный JSON без пояснений и без ```json.
Вопросы должны проверять реальный опыт, действия кандидата, результат, проверку качества и способность объяснить решение.
Оценка ответа должна учитывать смысл, конкретику, профессиональные действия, проверку результата и соответствие вопросу.
Запрещены общие вопросы вида "Что вы знаете про ...?".
""".strip()


def get_model_content(response):
    if isinstance(response, dict):
        message = response.get('message') or {}
        return message.get('content', '')
    message = getattr(response, 'message', None)
    return getattr(message, 'content', '') if message else ''


def extract_json_payload(content):
    text = (content or '').strip()
    fenced = re.search(r'```(?:json)?\s*(.*?)\s*```', text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    for left, right in [('[', ']'), ('{', '}')]:
        start = text.find(left)
        end = text.rfind(right)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError('JSON not found')


def coerce_skill_items(raw_skills, fallback_text=''):
    fallback = fallback_extract_skills(fallback_text) if fallback_text else []
    items = raw_skills if isinstance(raw_skills, list) else []
    skills = []
    seen = set()

    for item in items:
        if isinstance(item, dict):
            name = item.get('name') or item.get('skill') or item.get('title')
            importance = clamp_importance(item.get('importance', 3))
        else:
            name = str(item)
            importance = 3

        name = re.sub(r'\s+', ' ', (name or '').strip())
        if len(name) < 2:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        skills.append({'name': name[:80], 'importance': importance})

    return skills[:7] or fallback or [
        {'name': 'Профильный опыт', 'importance': 4},
        {'name': 'Решение практических задач', 'importance': 4},
        {'name': 'Коммуникация', 'importance': 3},
    ]


def question_is_usable(question):
    text = re.sub(r'\s+', ' ', (question or '').strip())
    if len(text) < 35 or len(text) > 320:
        return False
    lowered = text.lower()
    generic_patterns = [
        'что вы знаете про',
        'что вы знаете о',
        'расскажите про',
        'что такое ',
    ]
    return not any(pattern in lowered for pattern in generic_patterns)


def sanitize_question_items(raw_questions, skills, question_profile='balanced'):
    profile = normalize_question_profile(question_profile)
    normalized_skills = coerce_skill_items(skills)
    raw_items = raw_questions if isinstance(raw_questions, list) else []
    result = []
    used_questions = set()

    for index, skill in enumerate(normalized_skills):
        candidate = raw_items[index] if index < len(raw_items) and isinstance(raw_items[index], dict) else {}
        skill_name = candidate.get('skill') or skill['name']
        question = re.sub(r'\s+', ' ', str(candidate.get('question') or '').strip())
        importance = clamp_importance(candidate.get('importance', skill.get('importance', 3)))

        if not question_is_usable(question):
            fallback = fallback_question_for_skill({'name': skill['name'], 'importance': importance}, index, profile)
            skill_name = fallback['skill']
            question = fallback['question']
            importance = fallback['importance']

        key = question.lower()
        if key in used_questions:
            fallback = fallback_question_for_skill({'name': skill['name'], 'importance': importance}, index + 3, profile)
            skill_name = fallback['skill']
            question = fallback['question']
            importance = fallback['importance']

        used_questions.add(question.lower())
        result.append({
            'skill': str(skill_name).strip()[:80] or skill['name'],
            'question': question,
            'importance': importance,
        })

    for extra_skill in [{'name': 'Практический опыт', 'importance': 4}, {'name': 'Коммуникация', 'importance': 3}]:
        if len(result) >= 3:
            break
        result.append(fallback_question_for_skill(extra_skill, len(result), profile))

    return result[:7]


def parse_numeric_score(value):
    text = str(value or '').replace(',', '.')
    json_match = re.search(r'"?score"?\s*:\s*(-?\d+(?:\.\d+)?)', text)
    if json_match:
        return float(json_match.group(1))
    number_match = re.search(r'-?\d+(?:\.\d+)?', text)
    if number_match:
        return float(number_match.group(0))
    raise ValueError('Score not found')


def infer_vacancy_title(text):
    first_line = next((line.strip() for line in (text or '').splitlines() if line.strip()), '')
    first_line = re.sub(r'^(вакансия|должность|позиция)\s*[:\-]\s*', '', first_line, flags=re.IGNORECASE)
    first_line = re.sub(r'\s+', ' ', first_line).strip(' .:-')
    return first_line[:120] if len(first_line) >= 3 else 'Новая вакансия'


def extract_skills_with_llm(text):
    fallback = fallback_extract_skills(text)
    ollama_client = get_ollama_client()
    if ollama_client is None:
        return fallback

    prompt = f"""
Извлеки 4-7 ключевых навыков из описания вакансии.
Верни JSON-массив объектов:
[
  {{"name": "название навыка на русском", "importance": 1-5}}
]
Описание вакансии:
{text}
""".strip()

    try:
        response = ollama_client.chat(model=OLLAMA_MODEL, messages=[
            {'role': 'system', 'content': LLM_SYSTEM_PROMPT},
            {'role': 'user', 'content': prompt},
        ])
        parsed = extract_json_payload(get_model_content(response))
        return coerce_skill_items(parsed, text)
    except Exception as error:
        print(f"[Ошибка извлечения навыков]: {error}")
        return fallback


def generate_questions_with_llm(skills, vacancy_title='', question_profile='balanced'):
    profile = normalize_question_profile(question_profile)
    normalized_skills = coerce_skill_items(skills)
    ollama_client = get_ollama_client()
    if ollama_client is None:
        return fallback_generate_questions(normalized_skills, profile)

    prompt = f"""
Сгенерируй вопросы для первичного HR/technical screening.
Должность: {vacancy_title or 'не указана'}
Стиль вопросов: {QUESTION_PROFILE_LABELS[profile]}
Навыки:
{json.dumps(normalized_skills, ensure_ascii=False)}

Требования:
- один вопрос на каждый навык;
- вопрос на русском языке;
- вопрос должен проверять практический опыт, ход мысли, действия и результат;
- не делай слишком общие вопросы;
- не проси писать код целиком;
- важность бери из навыка или уточняй в диапазоне 1-5.

Верни только JSON-массив объектов:
[
  {{"skill": "Python", "question": "текст вопроса", "importance": 5}}
]
""".strip()

    try:
        response = ollama_client.chat(model=OLLAMA_MODEL, messages=[
            {'role': 'system', 'content': LLM_SYSTEM_PROMPT},
            {'role': 'user', 'content': prompt},
        ])
        data = extract_json_payload(get_model_content(response))
        return sanitize_question_items(data, normalized_skills, profile)
    except Exception as error:
        print(f"[Ошибка генерации вопросов]: {error}")
        return fallback_generate_questions(normalized_skills, profile)


def evaluate_answer_with_llm(question, answer):
    answer = (answer or '').strip()
    forced_score, max_score = answer_quality_gate(question, answer)
    if forced_score is not None:
        return clamp_score(forced_score)

    if len(answer) < 10:
        return 0.0

    prompt = f"""
Оцени ответ кандидата на вопрос.
Шкала:
0 = пусто, бессмыслица, бытовая тема, шутка, ответ не по вопросу или профильная часть почти не раскрыта.
0.3 = частично по теме, мало конкретики.
0.6 = по теме, есть действия и понимание, но не хватает деталей или результата.
0.8 = хороший ответ с примером, действиями и проверкой результата.
1 = сильный профессиональный ответ с контекстом, решением, рисками и итогом.

Верни только одно число от 0 до 1.

Вопрос: {question}
Ответ: {answer}
"""
    ollama_client = get_ollama_client()
    if ollama_client is None:
        return fallback_evaluate_answer(question, answer)
    try:
        response = ollama_client.chat(model=OLLAMA_MODEL, messages=[
            {'role': 'system', 'content': LLM_SYSTEM_PROMPT},
            {'role': 'user', 'content': prompt},
        ])
        score = parse_numeric_score(get_model_content(response))
        rule_score = fallback_evaluate_answer(question, answer)
        if rule_score < 0.25 and score > 0.7:
            score = 0.5
        return clamp_score(min(max_score, score))
    except Exception as e:
        print(f"Ошибка при оценке ответа: {e}")
        return fallback_evaluate_answer(question, answer)


def generate_summary_for_candidate(answers, score):
    prompt = f"""Сделай краткий HR-отчёт по кандидату.
Ответы кандидата:
{json.dumps([{'question': q, 'answer': a, 'score': s} for q, a, s, _ in answers], ensure_ascii=False)}
Общая оценка: {score}
Формат:
1. Соответствие роли.
2. Сильные стороны.
3. Зоны риска.
4. Рекомендация по следующему шагу.
"""
    ollama_client = get_ollama_client()
    if ollama_client is None:
        return fallback_candidate_summary(answers, score)
    try:
        response = ollama_client.chat(model=OLLAMA_MODEL, messages=[
            {'role': 'system', 'content': LLM_SYSTEM_PROMPT},
            {'role': 'user', 'content': prompt},
        ])
        content = get_model_content(response).strip()
        return content[:1800] if content else fallback_candidate_summary(answers, score)
    except Exception as error:
        print(f"[Ошибка генерации итогового отчёта]: {error}")
        return fallback_candidate_summary(answers, score)

# ========== ФОНОВАЯ ГЕНЕРАЦИЯ ==========
def calculate_weighted_score(answers):
    total_weight = sum(int(row[3] or 0) for row in answers) or 1
    score = sum(clamp_score(row[2]) * int(row[3] or 0) for row in answers) / total_weight
    return clamp_score(score)


def normalize_answer_scores(answers):
    return [
        (row[0], row[1], clamp_score(row[2]), row[3])
        for row in answers
    ]


def summary_needs_score_refresh(summary):
    return bool(re.search(r'(^|[\s:])-\d+(?:[.,]\d+)?', summary or ''))


def background_generate_questions(vacancy_id, text, vacancy_title='', question_profile='balanced'):
    conn = None
    try:
        skills = extract_skills_with_llm(text)
        questions = generate_questions_with_llm(skills, vacancy_title, question_profile)
        conn = get_db_connection()
        with conn:
            with conn.cursor() as cur:
                for q in questions:
                    cur.execute('''
                        INSERT INTO question (vacancy_id, skill, question, importance)
                        VALUES (%s, %s, %s, %s)
                    ''', (vacancy_id, q['skill'], q['question'], q.get('importance', 3)))
                cur.execute("UPDATE vacancy SET status = 'Готово' WHERE id = %s", (vacancy_id,))
    except Exception as e:
        print(f"[Ошибка генерации вопросов для вакансии {vacancy_id}]:", e)
        try:
            conn = get_db_connection()
            with conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE vacancy SET status = 'Ошибка' WHERE id = %s", (vacancy_id,))
        except Exception as db_error:
            print(f"[Ошибка обновления статуса вакансии {vacancy_id}]:", db_error)

def background_generate_summary(session_id):
    try:
        conn = get_db_connection()
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT q.question, a.answer, a.score, q.importance
                    FROM answer a
                    JOIN question q ON a.question_id = q.id
                    WHERE a.session_id = %s
                """, (session_id,))
                answers = normalize_answer_scores(cur.fetchall())
                weighted_score = calculate_weighted_score(answers)
                summary = generate_summary_for_candidate(answers, weighted_score)

                cur.execute("UPDATE test_session SET summary = %s WHERE id = %s", (summary, session_id))
    except Exception as e:
        print(f"[Ошибка генерации отчёта по сессии {session_id}]:", e)


def get_dashboard_data():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT id, title, created_at, status FROM vacancy ORDER BY created_at DESC')
            vacancies = []
            for row in cur.fetchall():
                vacancy_id, title, created_at, status = row
                cur.execute('SELECT COUNT(*) FROM test_session WHERE vacancy_id = %s', (vacancy_id,))
                candidates = cur.fetchone()[0] or 0
                cur.execute('SELECT COUNT(*) FROM question WHERE vacancy_id = %s', (vacancy_id,))
                question_count = cur.fetchone()[0] or 0
                cur.execute('''
                    SELECT AVG(CASE WHEN a.score < 0 THEN 0 WHEN a.score > 1 THEN 1 ELSE a.score END)
                    FROM answer a
                    JOIN test_session ts ON a.session_id = ts.id
                    WHERE ts.vacancy_id = %s
                ''', (vacancy_id,))
                avg_row = cur.fetchone()
                avg_score = avg_row[0] if avg_row and avg_row[0] is not None else None
                vacancies.append({
                    'id': vacancy_id,
                    'title': title,
                    'created_at': format_display_date(created_at),
                    'status': status,
                    'candidates': candidates,
                    'question_count': question_count,
                    'avg_score': round(float(avg_score), 2) if avg_score is not None else None,
                })

        total = len(vacancies)
        ready = sum(1 for vacancy in vacancies if vacancy['status'] == 'Готово')
        candidates = sum(int(vacancy['candidates'] or 0) for vacancy in vacancies)
        scored = [vacancy['avg_score'] for vacancy in vacancies if vacancy['avg_score'] is not None]
        stats = {
            'total': total,
            'ready': ready,
            'candidates': candidates,
            'avg_score': f"{(sum(scored) / len(scored)):.2f}" if scored else '0.00',
        }
        return vacancies, stats
    finally:
        conn.close()


def reset_demo_data(cur):
    cur.execute('''
        DELETE FROM answer
        WHERE session_id IN (
            SELECT ts.id FROM test_session ts
            JOIN vacancy v ON ts.vacancy_id = v.id
            WHERE v.title = %s
        )
    ''', (DEMO_VACANCY_TITLE,))
    cur.execute('''
        DELETE FROM test_session
        WHERE vacancy_id IN (
            SELECT id FROM vacancy WHERE title = %s
        )
    ''', (DEMO_VACANCY_TITLE,))
    cur.execute('''
        DELETE FROM question
        WHERE vacancy_id IN (
            SELECT id FROM vacancy WHERE title = %s
        )
    ''', (DEMO_VACANCY_TITLE,))
    cur.execute('DELETE FROM vacancy WHERE title = %s', (DEMO_VACANCY_TITLE,))


def seed_demo_data():
    conn = get_db_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute('SELECT id FROM users WHERE email = %s', (DEMO_EMAIL,))
                user = cur.fetchone()
                if user:
                    user_id = user[0]
                    cur.execute('UPDATE users SET name = %s WHERE id = %s', ('HR-специалист', user_id))
                else:
                    cur.execute(
                        'INSERT INTO users (name, email, password) VALUES (%s, %s, %s) RETURNING id',
                        ('HR-специалист', DEMO_EMAIL, build_password_hash(DEMO_PASSWORD)),
                    )
                    user_id = cur.fetchone()[0]

                reset_demo_data(cur)
                cur.execute(
                    'INSERT INTO vacancy (title, text, status) VALUES (%s, %s, %s) RETURNING id',
                    (DEMO_VACANCY_TITLE, DEMO_VACANCY_TEXT, 'Готово'),
                )
                vacancy_id = cur.fetchone()[0]

                question_ids = []
                for skill, question, importance in DEMO_QUESTIONS:
                    cur.execute(
                        'INSERT INTO question (vacancy_id, skill, question, importance) VALUES (%s, %s, %s, %s) RETURNING id',
                        (vacancy_id, skill, question, importance),
                    )
                    question_ids.append(cur.fetchone()[0])

                for candidate in DEMO_CANDIDATES:
                    cur.execute(
                        'INSERT INTO test_session (vacancy_id, full_name) VALUES (%s, %s) RETURNING id',
                        (vacancy_id, candidate['name']),
                    )
                    session_id = cur.fetchone()[0]
                    answers_for_summary = []
                    for question_id, (answer_text, score), (_, question_text, importance) in zip(
                        question_ids,
                        candidate['answers'],
                        DEMO_QUESTIONS,
                    ):
                        cur.execute(
                            'INSERT INTO answer (question_id, session_id, answer, score) VALUES (%s, %s, %s, %s)',
                            (question_id, session_id, answer_text, score),
                        )
                        answers_for_summary.append((question_text, answer_text, score, importance))

                    weighted_score = calculate_weighted_score(answers_for_summary)
                    summary = fallback_candidate_summary(answers_for_summary, weighted_score)
                    cur.execute('UPDATE test_session SET summary = %s WHERE id = %s', (summary, session_id))

        return user_id, vacancy_id
    finally:
        conn.close()


def get_showcase_context(vacancy_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT id, title, text, status, created_at FROM vacancy WHERE id = %s',
                (vacancy_id,),
            )
            vacancy_row = cur.fetchone()
            if not vacancy_row:
                return {
                    'vacancy': None,
                    'questions': [],
                    'results': [],
                    'vacancy_id': vacancy_id,
                    'best_result': None,
                    'review_result': None,
                    'overview_metrics': {
                        'skill_count': 0,
                        'candidate_count': 0,
                        'best_score': 0,
                        'invite_count': 0,
                    },
                    'generation_variants': build_showcase_generation_variants(),
                    'evaluation_examples': build_showcase_evaluation_examples(),
                }

            vacancy = {
                'id': vacancy_row[0],
                'title': vacancy_row[1] or 'Вакансия без названия',
                'text': vacancy_row[2] or '',
                'status': vacancy_row[3],
                'created_at': format_display_date(vacancy_row[4]),
            }

            cur.execute(
                '''
                    SELECT id, skill, question, importance
                    FROM question
                    WHERE vacancy_id = %s
                    ORDER BY id
                ''',
                (vacancy_id,),
            )
            questions = [
                {
                    'id': row[0],
                    'skill': row[1],
                    'question': row[2],
                    'importance': row[3],
                }
                for row in cur.fetchall()
            ]

            cur.execute(
                '''
                    SELECT id, full_name, created_at, summary
                    FROM test_session
                    WHERE vacancy_id = %s
                    ORDER BY id
                ''',
                (vacancy_id,),
            )
            sessions = cur.fetchall()
            results = []
            for session_id, full_name, created_at, summary in sessions:
                cur.execute(
                    '''
                        SELECT q.question, a.answer, a.score, q.importance
                        FROM answer a
                        JOIN question q ON a.question_id = q.id
                        WHERE a.session_id = %s
                        ORDER BY q.id
                    ''',
                    (session_id,),
                )
                answers = normalize_answer_scores(cur.fetchall())
                weighted_score = calculate_weighted_score(answers) if answers else 0
                results.append({
                    'session_id': session_id,
                    'name': full_name or 'Кандидат без имени',
                    'date': format_display_date(created_at),
                    'score': clamp_score(weighted_score),
                    'answer_count': len(answers),
                    'summary': summary or 'Сводка ещё формируется.',
                })

            results.sort(key=lambda item: item['score'], reverse=True)
            best_result = results[0] if results else None
            review_result = results[1] if len(results) > 1 else None
            overview_metrics = {
                'skill_count': len(questions),
                'candidate_count': len(results),
                'best_score': best_result['score'] if best_result else 0,
                'invite_count': sum(1 for item in results if item['score'] >= 0.75),
            }

            return {
                'vacancy': vacancy,
                'questions': questions,
                'results': results,
                'vacancy_id': vacancy_id,
                'best_result': best_result,
                'review_result': review_result,
                'overview_metrics': overview_metrics,
                'generation_variants': build_showcase_generation_variants(),
                'evaluation_examples': build_showcase_evaluation_examples(),
            }
    finally:
        conn.close()
# ========== МАРШРУТЫ ==========

@app.errorhandler(PsycopgOperationalError)
@app.errorhandler(sqlite3.Error)
def handle_db_unavailable(error):
    return (
        "<h1>PostgreSQL недоступен</h1>"
        "<p>Проверьте, что сервер базы данных запущен и переменные окружения DB_NAME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT заполнены корректно. Для локального запуска можно использовать SQLite fallback.</p>",
        503,
    )


@app.context_processor
def inject_public_link_helpers():
    return {
        'candidate_test_url': candidate_test_public_url,
    }


@app.route('/')
def home():
    return render_template('home.html', is_authenticated='user_id' in session)

@app.route('/health')
def health():
    db_status = 'ok'
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute('SELECT 1')
        conn.close()
    except (PsycopgOperationalError, RuntimeError, sqlite3.Error):
        db_status = 'unavailable'

    ollama_status = get_ollama_status()
    status = 'ok' if db_status == 'ok' else 'degraded'
    code = 200 if status == 'ok' else 503
    return {
        'status': status,
        'database': db_status,
        'database_engine': ACTIVE_DB_ENGINE,
        'ollama': ollama_status,
        'model': OLLAMA_MODEL,
        'db_initialized': DB_READY,
    }, code

@app.route('/docs')
def docs():
    return render_template('docs.html')


@app.route('/showcase')
def showcase():
    user_id, vacancy_id = seed_demo_data()
    session['user_id'] = user_id
    showcase_context = get_showcase_context(vacancy_id)
    return render_template('showcase.html', **showcase_context)


@app.route('/demo')
def demo():
    return redirect(url_for('showcase'))


@app.route('/dashboard')
@login_required
def dashboard():
    vacancies, stats = get_dashboard_data()
    return render_template('dashboard.html', vacancies=vacancies, stats=stats)


@app.route('/vacancy/<int:vacancy_id>/questions', methods=['GET', 'POST'])
@login_required
def vacancy_questions(vacancy_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT id, title, status FROM vacancy WHERE id = %s', (vacancy_id,))
            vacancy = cur.fetchone()
            if not vacancy:
                flash('Вакансия не найдена.', 'error')
                return redirect(url_for('dashboard'))

            if request.method == 'POST':
                skill = re.sub(r'\s+', ' ', (request.form.get('skill') or '').strip())[:128]
                question_text = re.sub(r'\s+', ' ', (request.form.get('question') or '').strip())
                importance = clamp_importance(request.form.get('importance'), 3)

                if len(question_text) < 12:
                    flash('Введите полный текст вопроса.', 'error')
                    return redirect(url_for('vacancy_questions', vacancy_id=vacancy_id))

                cur.execute(
                    'INSERT INTO question (vacancy_id, skill, question, importance) VALUES (%s, %s, %s, %s)',
                    (vacancy_id, skill or 'Вопрос HR', question_text[:1000], importance),
                )
                cur.execute("UPDATE vacancy SET status = 'Готово' WHERE id = %s", (vacancy_id,))
                conn.commit()
                flash('Вопрос добавлен в тест кандидата.', 'success')
                return redirect(url_for('vacancy_questions', vacancy_id=vacancy_id))

            cur.execute(
                '''
                    SELECT q.id, q.skill, q.question, q.importance, COUNT(a.id) AS answer_count
                    FROM question q
                    LEFT JOIN answer a ON a.question_id = q.id
                    WHERE q.vacancy_id = %s
                    GROUP BY q.id, q.skill, q.question, q.importance
                    ORDER BY q.id
                ''',
                (vacancy_id,),
            )
            questions = [
                {
                    'id': row[0],
                    'skill': row[1] or '',
                    'question': row[2] or '',
                    'importance': row[3] or 3,
                    'answer_count': row[4] or 0,
                }
                for row in cur.fetchall()
            ]
            cur.execute('SELECT COUNT(*) FROM test_session WHERE vacancy_id = %s', (vacancy_id,))
            candidate_count = cur.fetchone()[0] or 0

        return render_template(
            'vacancy_questions.html',
            vacancy={
                'id': vacancy[0],
                'title': vacancy[1] or f'Вакансия #{vacancy_id}',
                'status': vacancy[2],
            },
            questions=questions,
            candidate_count=candidate_count,
        )
    finally:
        conn.close()


@app.route('/vacancy/<int:vacancy_id>/questions/<int:question_id>/update', methods=['POST'])
@login_required
def update_vacancy_question(vacancy_id, question_id):
    skill = re.sub(r'\s+', ' ', (request.form.get('skill') or '').strip())[:128]
    question_text = re.sub(r'\s+', ' ', (request.form.get('question') or '').strip())
    importance = clamp_importance(request.form.get('importance'), 3)

    if len(question_text) < 12:
        flash('Введите полный текст вопроса.', 'error')
        return redirect(url_for('vacancy_questions', vacancy_id=vacancy_id))

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                '''
                    UPDATE question
                    SET skill = %s, question = %s, importance = %s
                    WHERE id = %s AND vacancy_id = %s
                ''',
                (skill or 'Вопрос HR', question_text[:1000], importance, question_id, vacancy_id),
            )
            conn.commit()
            flash('Вопрос обновлён.', 'success')
    finally:
        conn.close()

    return redirect(url_for('vacancy_questions', vacancy_id=vacancy_id))


@app.route('/vacancy/<int:vacancy_id>/questions/<int:question_id>/delete', methods=['POST'])
@login_required
def delete_vacancy_question(vacancy_id, question_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM answer WHERE question_id = %s', (question_id,))
            answer_count = cur.fetchone()[0] or 0
            if answer_count:
                flash('Вопрос уже есть в ответах кандидатов, поэтому его нельзя удалить без потери отчётов.', 'error')
                return redirect(url_for('vacancy_questions', vacancy_id=vacancy_id))

            cur.execute('DELETE FROM question WHERE id = %s AND vacancy_id = %s', (question_id, vacancy_id))
            cur.execute('SELECT COUNT(*) FROM question WHERE vacancy_id = %s', (vacancy_id,))
            remaining = cur.fetchone()[0] or 0
            cur.execute(
                "UPDATE vacancy SET status = %s WHERE id = %s",
                ('Готово' if remaining else 'В работе', vacancy_id),
            )
            conn.commit()
            flash('Вопрос удалён из теста.', 'success')
    finally:
        conn.close()

    return redirect(url_for('vacancy_questions', vacancy_id=vacancy_id))


@app.route('/vacancy/new', methods=['GET', 'POST'])
@login_required
def new_vacancy():
    if request.method == 'POST':
        text = request.form['text'].strip()
        title = (request.form.get('title') or '').strip() or infer_vacancy_title(text)
        title = title[:120]
        question_profile = normalize_question_profile(request.form.get('question_profile'))
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute('INSERT INTO vacancy (title, text) VALUES (%s, %s) RETURNING id', (title, text))
                vid = cur.fetchone()[0]
                conn.commit()
                executor.submit(background_generate_questions, vid, text, title, question_profile)
                flash('Вакансия создаётся. Вопросы будут сгенерированы позже.', 'info')
                return redirect(url_for('dashboard'))
        finally:
            conn.close()
    return render_template('vacancy_new.html')

@app.route('/test/<int:vacancy_id>', methods=['GET', 'POST'])
def candidate_test(vacancy_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT id, title, status FROM vacancy WHERE id = %s', (vacancy_id,))
            vacancy = cur.fetchone()
            if not vacancy:
                return render_template(
                    'test_candidate_form.html',
                    questions=[],
                    vacancy_id=vacancy_id,
                    vacancy_title='Тест не найден',
                    error='Ссылка на тест неверная или вакансия была удалена.',
                ), 404

            _, vacancy_title, vacancy_status = vacancy
            cur.execute('SELECT id, question FROM question WHERE vacancy_id = %s ORDER BY id', (vacancy_id,))
            questions = cur.fetchall()
            if request.method == 'POST':
                if not questions:
                    return render_template(
                        'test_candidate_form.html',
                        questions=questions,
                        vacancy_id=vacancy_id,
                        vacancy_title=vacancy_title,
                        error='Вопросы для этой вакансии ещё не готовы. Обновите страницу через несколько секунд.',
                    ), 409

                full_name = (request.form.get('full_name') or '').strip()
                cur.execute('INSERT INTO test_session (vacancy_id, full_name) VALUES (%s, %s) RETURNING id',
                            (vacancy_id, full_name))
                session_id = cur.fetchone()[0]
                for qid, text in questions:
                    ans = (request.form.get(f'answer{qid}') or '').strip()
                    score = evaluate_answer_with_llm(text, ans)
                    cur.execute('INSERT INTO answer (question_id, session_id, answer, score) VALUES (%s, %s, %s, %s)',
                                (qid, session_id, ans, score))
                conn.commit()
                executor.submit(background_generate_summary, session_id)
                return redirect(url_for('test_result', session_id=session_id))
            else:
                error = None
                if not questions:
                    if vacancy_status == 'Ошибка':
                        error = 'Вопросы для этого теста не сформировались. HR-специалисту нужно создать тест заново.'
                    else:
                        error = 'Вопросы ещё формируются. Обычно это занимает несколько секунд.'
                return render_template(
                    'test_candidate_form.html',
                    questions=questions,
                    vacancy_id=vacancy_id,
                    vacancy_title=vacancy_title,
                    error=error,
                )
    finally:
        conn.close()

@app.route('/test/result/<int:session_id>')
def test_result(session_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT q.question, a.answer, a.score, q.importance
                FROM answer a
                JOIN question q ON a.question_id = q.id
                WHERE a.session_id = %s
            """, (session_id,))
            answers = normalize_answer_scores(cur.fetchall())
            weighted_score = calculate_weighted_score(answers)

            cur.execute("SELECT summary FROM test_session WHERE id = %s", (session_id,))
            summary_row = cur.fetchone()
            summary = summary_row[0] if summary_row and summary_row[0] else "Отчёт ещё формируется..."
            if summary == "Отчёт ещё формируется..." or summary_needs_score_refresh(summary):
                summary = fallback_candidate_summary(answers, weighted_score)
                cur.execute("UPDATE test_session SET summary = %s WHERE id = %s", (summary, session_id))
                conn.commit()

            return render_template("test_result.html", answers=answers,
                                   final_score=round(weighted_score, 2),
                                   summary=summary)
    finally:
        conn.close()

@app.route('/vacancy/<int:vacancy_id>/result')
@login_required
def vacancy_result(vacancy_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT id, full_name, created_at FROM test_session WHERE vacancy_id = %s', (vacancy_id,))
            sessions = cur.fetchall()
            results = []
            for sid, name, dt in sessions:
                cur.execute('''
                    SELECT q.importance, a.score
                    FROM answer a JOIN question q ON a.question_id = q.id
                    WHERE a.session_id = %s
                ''', (sid,))
                data = cur.fetchall()
                total = sum([int(w) for w, _ in data]) or 1
                score = sum([clamp_score(s) * int(w) for w, s in data]) / total
                results.append({"name": name, "score": clamp_score(score), "date": format_display_date(dt), "session_id": sid})
            return render_template('vacancy_result.html', results=results)
    finally:
        conn.close()

# ========== АВТОРИЗАЦИЯ ==========

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute('SELECT id, password FROM users WHERE email = %s', (email,))
                user = cur.fetchone()
                if user and check_password_hash(user[1], password):
                    session['user_id'] = user[0]
                    return redirect(url_for('dashboard'))
                flash("Неверный логин или пароль", "error")
        finally:
            conn.close()
    return render_template('login.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = request.form['password']
        confirm = request.form['confirmPassword']
        if password != confirm:
            flash("Пароли не совпадают", "error")
            return redirect(url_for('signup'))
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                hashed = build_password_hash(password)
                cur.execute('INSERT INTO users (name, email, password) VALUES (%s, %s, %s)',
                            (name, email, hashed))
                conn.commit()
                return redirect(url_for('login'))
        except Exception as error:
            conn.rollback()
            if is_unique_violation(error):
                flash("Пользователь с таким email уже существует", "error")
            else:
                flash("Ошибка регистрации", "error")
        finally:
            conn.close()
    return render_template('signup.html')
@app.route('/vacancy/<int:vacancy_id>/delete', methods=['POST'])
@login_required
def delete_vacancy(vacancy_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Удалить ответы (answer)
            cur.execute('''
                DELETE FROM answer 
                WHERE question_id IN (
                    SELECT id FROM question WHERE vacancy_id = %s
                )
            ''', (vacancy_id,))

            # Удалить сессии
            cur.execute('DELETE FROM test_session WHERE vacancy_id = %s', (vacancy_id,))

            # Удалить вопросы
            cur.execute('DELETE FROM question WHERE vacancy_id = %s', (vacancy_id,))

            # Удалить вакансию
            cur.execute('DELETE FROM vacancy WHERE id = %s', (vacancy_id,))

            conn.commit()
            flash('Вакансия и все связанные данные удалены.', 'success')
    finally:
        conn.close()

    return redirect(url_for('dashboard'))


@app.route('/vacancy/<int:vacancy_id>/evaluate', methods=['GET', 'POST'])
@login_required
def evaluate_manually(vacancy_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Получаем все вопросы по вакансии
            cur.execute('SELECT id, question FROM question WHERE vacancy_id = %s', (vacancy_id,))
            questions = cur.fetchall()

            if request.method == 'POST':
                if not questions:
                    flash('Вопросы для этой вакансии еще не готовы.', 'error')
                    return redirect(url_for('evaluate_manually', vacancy_id=vacancy_id))

                cur.execute('INSERT INTO test_session (vacancy_id, full_name) VALUES (%s, %s) RETURNING id',
                            (vacancy_id, 'HR MANUAL'))
                session_id = cur.fetchone()[0]
                answers_for_summary = []

                for qid, qtext in questions:
                    importance = 3
                    answer = (request.form.get(f'answer{qid}') or '').strip()
                    score = evaluate_answer_with_llm(qtext, answer)
                    cur.execute(
                        'INSERT INTO answer (question_id, session_id, answer, score) VALUES (%s, %s, %s, %s)',
                        (qid, session_id, answer, score)
                    )
                    answers_for_summary.append((qtext, answer, score, importance))

                # Генерация отчёта и сохранение
                total_weight = sum([3 for _ in questions]) or 1
                weighted_score = sum([3 * 0.5 for _ in questions]) / total_weight  # для примера
                summary = generate_summary_for_candidate([(q[1], "пример", 0.5, 3) for q in questions], weighted_score)

                weighted_score = calculate_weighted_score(answers_for_summary)
                summary = generate_summary_for_candidate(answers_for_summary, weighted_score)

                cur.execute("UPDATE test_session SET summary = %s WHERE id = %s", (summary, session_id))
                conn.commit()

                return redirect(url_for('test_result', session_id=session_id))

            return render_template('evaluate_vacancy.html', questions=questions, vacancy_id=vacancy_id)
    finally:
        conn.close()

@app.route('/logout')
def logout():
    session.clear()
    flash("Вы вышли из системы", "success")
    return redirect(url_for('home'))

@app.route("/openapi.yaml")
def openapi_spec():
    return send_file(resource_path('openapi.yaml'), mimetype="text/yaml")


def run_web_app():
    app.run(
        host=os.getenv('APP_HOST', '127.0.0.1' if getattr(sys, 'frozen', False) else '0.0.0.0'),
        port=int(os.getenv('APP_PORT', '5000')),
        debug=os.getenv('FLASK_DEBUG', '0' if getattr(sys, 'frozen', False) else '1') == '1'
    )


# ========== ЗАПУСК ==========
if __name__ == '__main__':
    run_web_app()
