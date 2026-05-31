import http.cookiejar
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = 5015
DB_PATH = ROOT / 'smoke_e2e.sqlite3'


def build_opener():
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def wait_for_server(base_url: str, timeout: float = 20.0):
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base_url + '/health', timeout=1.5) as response:
                if response.status == 200:
                    return
        except Exception as error:
            last_error = error
            time.sleep(0.25)
    raise RuntimeError(f'Server did not start: {last_error!r}')


def fetch_text(opener, url: str) -> str:
    with opener.open(url, timeout=5) as response:
        return response.read().decode('utf-8', errors='ignore')


def post_form(opener, url: str, data: dict):
    payload = urllib.parse.urlencode(data).encode()
    request = urllib.request.Request(url, data=payload, method='POST')
    request.add_header('Content-Type', 'application/x-www-form-urlencoded')
    return opener.open(request, timeout=10)


def assert_contains(text: str, needle: str, label: str):
    if needle not in text:
        raise AssertionError(f'{label}: expected to find {needle!r}')


def main():
    if DB_PATH.exists():
        DB_PATH.unlink()

    env = os.environ.copy()
    env['APP_PORT'] = str(PORT)
    env['DB_ENGINE'] = 'sqlite'
    env['SQLITE_PATH'] = str(DB_PATH)
    env['OLLAMA_ENABLED'] = '0'
    env['OPEN_BROWSER'] = '0'

    process = subprocess.Popen(
        [str(ROOT / '.venv' / 'Scripts' / 'python.exe'), 'launcher.py', '--serve-local'],
        cwd=str(ROOT),
        env=env,
    )

    base_url = f'http://127.0.0.1:{PORT}'

    try:
        wait_for_server(base_url)

        public_opener = build_opener()
        hr_opener = build_opener()

        home_html = fetch_text(public_opener, base_url + '/')
        assert_contains(home_html, 'Project HR', 'home page branding')

        with post_form(hr_opener, base_url + '/signup', {
            'name': 'Smoke HR',
            'email': 'smoke_hr@example.com',
            'password': 'StrongPass123',
            'confirmPassword': 'StrongPass123',
        }) as response:
            signup_url = response.geturl()
            signup_html = response.read().decode('utf-8', errors='ignore')
        if '/login' not in signup_url and 'Вход' not in signup_html:
            raise AssertionError('signup did not redirect to login')

        with post_form(hr_opener, base_url + '/login', {
            'email': 'smoke_hr@example.com',
            'password': 'StrongPass123',
        }) as response:
            login_url = response.geturl()
            login_html = response.read().decode('utf-8', errors='ignore')
        if '/dashboard' not in login_url and 'Панель' not in login_html:
            raise AssertionError('login did not open dashboard')

        vacancy_text = 'Python backend developer Flask SQL API testing communication'
        with post_form(hr_opener, base_url + '/vacancy/new', {
            'text': vacancy_text,
        }) as response:
            dashboard_url = response.geturl()
            dashboard_html = response.read().decode('utf-8', errors='ignore')
        if '/dashboard' not in dashboard_url:
            raise AssertionError('vacancy creation did not return dashboard')

        vacancy_id = None
        deadline = time.time() + 15
        while time.time() < deadline:
            dashboard_html = fetch_text(hr_opener, base_url + '/dashboard')
            if '/test/' in dashboard_html:
                marker = '/test/'
                index = dashboard_html.find(marker)
                tail = dashboard_html[index + len(marker):index + len(marker) + 20]
                digits = ''.join(ch for ch in tail if ch.isdigit())
                if digits:
                    vacancy_id = int(digits)
                    break
            time.sleep(0.5)
        if vacancy_id is None:
            raise AssertionError('vacancy link not found on dashboard')

        test_url = f'{base_url}/test/{vacancy_id}'
        test_html = ''
        deadline = time.time() + 20
        while time.time() < deadline:
            test_html = fetch_text(public_opener, test_url)
            if 'answer' in test_html:
                break
            time.sleep(0.5)
        assert_contains(test_html, 'full_name', 'candidate test form')

        answer_ids = []
        for part in test_html.split('name="answer'):
            if not answer_ids and part == test_html:
                continue
            digits = ''.join(ch for ch in part[:12] if ch.isdigit())
            if digits:
                answer_ids.append(digits)
        answer_ids = sorted(set(answer_ids))
        if not answer_ids:
            raise AssertionError('no generated questions found for candidate test')

        candidate_form = {'full_name': 'Smoke Candidate'}
        for answer_id in answer_ids:
            candidate_form[f'answer{answer_id}'] = 'I have practical experience with Python, Flask and testing.'

        with post_form(public_opener, test_url, candidate_form) as response:
            result_url = response.geturl()
            result_html = response.read().decode('utf-8', errors='ignore')
        if '/test/result/' not in result_url:
            raise AssertionError('candidate submission did not redirect to result page')
        assert_contains(result_html, 'Результаты теста', 'candidate result page heading')
        assert_contains(result_html, 'Итоговая оценка', 'candidate result summary')

        vacancy_results_html = fetch_text(hr_opener, f'{base_url}/vacancy/{vacancy_id}/result')
        assert_contains(vacancy_results_html, 'Smoke Candidate', 'vacancy results page')

        docs_html = fetch_text(public_opener, base_url + '/docs')
        assert_contains(docs_html, 'Project HR', 'docs page branding')

        print('SMOKE TEST PASSED')
        print(f'vacancy_id={vacancy_id}')
        print(f'questions={len(answer_ids)}')
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except Exception:
            process.kill()


if __name__ == '__main__':
    main()
