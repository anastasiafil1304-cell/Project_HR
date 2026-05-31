#!/bin/sh
set -e

python - <<'PY'
import os
import time
import psycopg2

host = os.getenv('DB_HOST', 'db')
port = os.getenv('DB_PORT', '5432')
dbname = os.getenv('DB_NAME', 'vacmatch')
user = os.getenv('DB_USER', 'vacmatch_user')
password = os.getenv('DB_PASSWORD', 'vacmatchvacmatch')

for attempt in range(30):
    try:
        connection = psycopg2.connect(
            host=host,
            port=port,
            dbname=dbname,
            user=user,
            password=password,
        )
        connection.close()
        print('PostgreSQL is ready')
        break
    except Exception as error:
        print(f'Waiting for PostgreSQL: {error}')
        time.sleep(2)
else:
    raise SystemExit('PostgreSQL did not become ready in time')
PY

exec gunicorn --bind 0.0.0.0:5000 --workers 2 app:app