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
MAX_ANSWER_LENGTH = int(os.getenv('MAX_ANSWER_LENGTH', '2500'))
DB_READY = False

DEMO_EMAIL = 'demo.hr@project.local'
DEMO_PASSWORD = 'Demo12345'
DEMO_VACANCY_TITLE = 'Python Backend Developer — демонстрационная вакансия'
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


def get_ollama_client():
    global OLLAMA_CLIENT, OLLAMA_IMPORT_FAILED

    if not OLLAMA_ENABLED:
        return None

    if OLLAMA_CLIENT is not None:
        return OLLAMA_CLIENT

    if OLLAMA_IMPORT_FAILED:
        return None

    try:
        OLLAMA_CLIENT = importlib.import_module('ollama')
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
        return -1.0, 0.0

    answer_tokens = keyword_tokens(normalized_answer)
    question_tokens = keyword_tokens(question)

    if len(answer_tokens) < 3:
        return -0.5, 0.0

    if looks_like_gibberish(normalized_answer, answer_tokens):
        return -0.8, 0.0

    alpha_chars = [char for char in normalized_answer.lower() if char.isalpha()]
    if len(alpha_chars) >= 20:
        vowels = set('aeiouy' + '\u0430\u0435\u0451\u0438\u043e\u0443\u044b\u044d\u044e\u044f')
        vowel_ratio = sum(1 for char in alpha_chars if char in vowels) / len(alpha_chars)
        if vowel_ratio < 0.16:
            return -0.6, 0.0

    answer_set = set(answer_tokens)
    question_set = set(question_tokens)
    overlap = answer_set & question_set
    new_terms = answer_set - question_set
    soft_overlap = count_soft_overlap(answer_tokens, question_tokens)
    professional_hits = count_stem_matches(answer_tokens, PROFESSIONAL_RELEVANCE_STEMS)
    off_topic_hits = count_stem_matches(answer_tokens, OFF_TOPIC_STEMS)

    if off_topic_hits and professional_hits <= 1:
        return -0.7, 0.0

    if question_set and soft_overlap == 0 and professional_hits == 0:
        return -0.6, 0.0

    if question_set and soft_overlap <= 1 and professional_hits <= 1 and len(answer_tokens) < 12:
        return -0.5, 0.0

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

    overlap = count_relevant_overlap(answer_tokens, question_tokens)
    overlap_ratio = overlap / max(1, len(set(question_tokens))) if question_tokens else 0

    score = 0.15
    score += min(0.2, len(answer_tokens) * 0.03)

    if question_tokens:
        score += min(0.55, overlap_ratio * 0.75)
        if overlap == 0:
            score -= 0.25
    else:
        score += 0.15

    if any(token.isdigit() for token in answer_tokens):
        score += 0.05

    if len(answer_tokens) >= 8:
        score += 0.05

    return max(-1.0, min(max_score, round(score, 2)))


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

def fallback_candidate_summary(answers, score):
    strong_answers = [question for question, _, answer_score, _ in answers if float(answer_score) >= 0.75]
    weak_answers = [question for question, _, answer_score, _ in answers if float(answer_score) < 0.3]
    verdict = 'Кандидат в целом подходит для следующего этапа.' if score >= 0.6 else 'Кандидату требуется дополнительная проверка на следующем этапе.'
    strengths = ', '.join(strong_answers[:2]) if strong_answers else 'сильные ответы не выявлены'
    weaknesses = ', '.join(weak_answers[:2]) if weak_answers else 'критичных провалов не выявлено'
    return (
        f"Итоговая оценка: {round(score, 2)}\n"
        f"Вывод: {verdict}\n"
        f"Сильные стороны: {strengths}.\n"
        f"Зоны риска: {weaknesses}.\n"
        "Рекомендация: используйте итог как предварительный скрининг и подтвердите вывод интервью с HR или техспециалистом."
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
    ('Тестирование и качество', 4, ['qa', 'test', 'тестирование', 'pytest', 'unittest', 'selenium', 'playwright', 'автотест']),
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


def fallback_question_for_skill(skill, index):
    skill_name = skill['name'] if isinstance(skill, dict) else str(skill)
    skill_context = {
        'SQL и базы данных': 'базами данных и SQL',
        'Backend-разработка': 'backend-разработкой',
        'Frontend-разработка': 'frontend-разработкой',
        'Fullstack-разработка': 'fullstack-разработкой',
        'Docker и контейнеризация': 'Docker и контейнеризацией',
        'API и интеграции': 'API и интеграциями',
        'Git и командная работа': 'Git и командной работой',
        'Тестирование и качество': 'тестированием и контролем качества',
        'Linux и администрирование': 'Linux и администрированием',
        'Управление задачами': 'управлением задачами',
        'HR-процессы': 'HR-процессами',
        'Архитектура решений': 'архитектурой решений',
        'Лидерство и наставничество': 'лидерством и наставничеством',
        'Профильный опыт': 'профильными задачами',
        'Решение практических задач': 'практическими задачами',
    }.get(skill_name, skill_name)
    importance = int(skill.get('importance', 3)) if isinstance(skill, dict) else 3
    templates = [
        'Опишите реальный проект или задачу, где вы работали с {skill}. Какой был результат и ваша зона ответственности?',
        'Представьте, что в работе с {skill} появилась нестабильная ошибка. Как вы будете диагностировать причину и проверять исправление?',
        'Какие типичные ошибки при работе с {skill} вы встречали? Как вы их предотвращаете?',
        'Как бы вы объяснили выбор подхода или инструмента для задачи, связанной с {skill}: какие плюсы, ограничения и альтернативы учли бы?',
        'Расскажите о ситуации, где вам пришлось улучшать качество, скорость или надежность решения, связанного с {skill}.',
    ]
    return {
        'skill': skill_name,
        'question': templates[index % len(templates)].format(skill=skill_context),
        'importance': max(1, min(5, importance)),
    }


def fallback_generate_questions(skills):
    normalized_skills = []
    for skill in skills:
        if isinstance(skill, dict):
            name = skill.get('name') or skill.get('skill')
            importance = int(skill.get('importance', 3))
        else:
            name = str(skill)
            importance = 3
        if name:
            normalized_skills.append({'name': name, 'importance': importance})

    questions = [fallback_question_for_skill(skill, index) for index, skill in enumerate(normalized_skills[:7])]
    for extra_skill in [{'name': 'Практический опыт', 'importance': 4}, {'name': 'Коммуникация', 'importance': 3}]:
        if len(questions) >= 3:
            break
        questions.append(fallback_question_for_skill(extra_skill, len(questions)))
    return questions


# ========== LLM ==========
def extract_skills_with_llm(text):
    prompt = f"Извлеки ключевые навыки из описания вакансии, навыки напиши на русском:\n{text}\nВерни JSON-массив строк."
    ollama_client = get_ollama_client()
    if ollama_client is None:
        return fallback_extract_skills(text)
    try:
        response = ollama_client.chat(model=OLLAMA_MODEL, messages=[{"role": "user", "content": prompt}])
        parsed = json.loads(response['message']['content'])
        if isinstance(parsed, list) and parsed:
            return parsed
    except Exception as error:
        print(f"[Ошибка извлечения навыков]: {error}")
    return fallback_extract_skills(text)

def generate_questions_with_llm(skills):
    prompt = f"""
Сгенерируй по одному вопросу на русском языке на каждый навык из списка: {skills}.
Формат JSON: список объектов с полями "skill", "question", "importance" (1–5).
    """.strip()
    ollama_client = get_ollama_client()
    if ollama_client is None:
        return [{"skill": skill, "question": f"Что вы знаете про {skill}?", "importance": 3} for skill in skills]
    try:
        response = ollama_client.chat(model=OLLAMA_MODEL, messages=[{"role": "user", "content": prompt}])
        content = response["message"]["content"]
        data = json.loads(content)
        if isinstance(data, list) and data and "question" in data[0]:
            return data
        raise ValueError("Invalid format")
    except Exception as error:
        print(f"[Ошибка генерации вопросов]: {error}")
        return [{"skill": skill, "question": f"Что вы знаете про {skill}?", "importance": 3} for skill in skills]

def generate_questions_with_llm(skills):
    prompt = f"""
Сгенерируй по одному вопросу на русском языке на каждый навык из списка: {skills}.
Формат JSON: список объектов с полями "skill", "question", "importance" (1-5).
    """.strip()
    ollama_client = get_ollama_client()
    if ollama_client is None:
        return fallback_generate_questions(skills)
    try:
        response = ollama_client.chat(model=OLLAMA_MODEL, messages=[{"role": "user", "content": prompt}])
        content = response["message"]["content"]
        data = json.loads(content)
        if isinstance(data, list) and data and "question" in data[0]:
            return data
        raise ValueError("Invalid format")
    except Exception as error:
        print(f"[Ошибка генерации вопросов]: {error}")
        return fallback_generate_questions(skills)


def evaluate_answer_with_llm(question, answer):
    answer = (answer or '').strip()
    forced_score, max_score = answer_quality_gate(question, answer)
    if forced_score is not None:
        return forced_score

    # 1. Проверка: если ответ слишком короткий или пустой
    if len(answer) < 10:
        return -1.0

    # 2. Промпт с уточнением
    prompt = f"""
Ты эксперт по найму.
Оцени, насколько ответ кандидата полон, корректен и соответствует вопросу.
- Если ответ пустой, бессмысленный или состоит из одного символа — ставь -1.
- Если ответ написан связными словами, но не отвечает на вопрос, шутит или уходит в бытовые темы — ставь от -1 до -0.5.
- Одно случайное слово из вопроса без реального описания опыта, действий и результата не должно давать положительную оценку.
- Если ответ частично корректен — от 0 до 0.7 в зависимости от полноты, 0 - это меньше половины верно.
- Если ответ отличный, с примерами — от 0.8 до 1.

Верни **только число** от -1 до 1.

Вопрос: {question}
Ответ: {answer}
"""
    ollama_client = get_ollama_client()
    if ollama_client is None:
        return fallback_evaluate_answer(question, answer)
    try:
        response = ollama_client.chat(model=OLLAMA_MODEL, messages=[{"role": "user", "content": prompt}])
        score_text = response["message"]["content"].strip()
        score = float(score_text.split()[0])
        return min(max_score, max(-1.0, score))  # Ограничение в диапазоне [-1, 1]
    except Exception as e:
        print(f"Ошибка при оценке ответа: {e}")
        return fallback_evaluate_answer(question, answer)


def generate_summary_for_candidate(answers, score):
    prompt = f"""Ты HR. Вот ответы кандидата:
{json.dumps([{'question': q, 'answer': a, 'score': s} for q, a, s, _ in answers], ensure_ascii=False)}
Общая оценка: {score}
Сделай краткий отчёт: соответствие, сильные/слабые стороны, рекомендации.
"""
    ollama_client = get_ollama_client()
    if ollama_client is None:
        return fallback_candidate_summary(answers, score)
    try:
        response = ollama_client.chat(model=OLLAMA_MODEL, messages=[{"role": "user", "content": prompt}])
        return response['message']['content']
    except Exception as error:
        print(f"[Ошибка генерации итогового отчёта]: {error}")
        return fallback_candidate_summary(answers, score)

# ========== ФОНОВАЯ ГЕНЕРАЦИЯ ==========
def calculate_weighted_score(answers):
    total_weight = sum(int(row[3] or 0) for row in answers) or 1
    return sum(float(row[2]) * int(row[3] or 0) for row in answers) / total_weight


def background_generate_questions(vacancy_id, text):
    conn = None
    try:
        skills = extract_skills_with_llm(text)
        questions = generate_questions_with_llm(skills)
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
                answers = cur.fetchall()
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
                cur.execute('''
                    SELECT AVG(a.score)
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
                else:
                    cur.execute(
                        'INSERT INTO users (name, email, password) VALUES (%s, %s, %s) RETURNING id',
                        ('Демо HR', DEMO_EMAIL, build_password_hash(DEMO_PASSWORD)),
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
# ========== МАРШРУТЫ ==========

@app.errorhandler(PsycopgOperationalError)
@app.errorhandler(sqlite3.Error)
def handle_db_unavailable(error):
    return (
        "<h1>PostgreSQL недоступен</h1>"
        "<p>Проверьте, что сервер базы данных запущен и переменные окружения DB_NAME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT заполнены корректно. Для локального запуска можно использовать SQLite fallback.</p>",
        503,
    )

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


@app.route('/demo')
def demo():
    user_id, vacancy_id = seed_demo_data()
    session['user_id'] = user_id
    flash(
        f'Демо-стенд готов: создана вакансия, вопросы и результаты кандидатов. Логин: {DEMO_EMAIL}, пароль: {DEMO_PASSWORD}.',
        'success',
    )
    return redirect(url_for('vacancy_result', vacancy_id=vacancy_id))


@app.route('/dashboard')
@login_required
def dashboard():
    vacancies, stats = get_dashboard_data()
    return render_template('dashboard.html', vacancies=vacancies, stats=stats)

@app.route('/vacancy/new', methods=['GET', 'POST'])
@login_required
def new_vacancy():
    if request.method == 'POST':
        text = request.form['text']
        title = text[:60]
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute('INSERT INTO vacancy (title, text) VALUES (%s, %s) RETURNING id', (title, text))
                vid = cur.fetchone()[0]
                conn.commit()
                executor.submit(background_generate_questions, vid, text)  # фоновая генерация
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
            cur.execute('SELECT id, question FROM question WHERE vacancy_id = %s', (vacancy_id,))
            questions = cur.fetchall()
            if request.method == 'POST':
                if not questions:
                    return render_template(
                        'test_candidate_form.html',
                        questions=questions,
                        vacancy_id=vacancy_id,
                        error='Вопросы для этой вакансии еще не готовы.',
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
                return render_template('test_candidate_form.html', questions=questions, vacancy_id=vacancy_id)
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
            answers = cur.fetchall()
            weighted_score = calculate_weighted_score(answers)

            cur.execute("SELECT summary FROM test_session WHERE id = %s", (session_id,))
            summary_row = cur.fetchone()
            summary = summary_row[0] if summary_row and summary_row[0] else "Отчёт ещё формируется..."

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
                score = sum([float(s) * int(w) for w, s in data]) / total
                results.append({"name": name, "score": round(score, 2), "date": format_display_date(dt), "session_id": sid})
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
