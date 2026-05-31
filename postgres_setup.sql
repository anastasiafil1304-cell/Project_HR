DO
$$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vacmatch_user') THEN
        CREATE ROLE vacmatch_user WITH LOGIN PASSWORD 'vacmatchvacmatch';
    END IF;
END
$$;

SELECT 'CREATE DATABASE vacmatch OWNER vacmatch_user'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'vacmatch')
\gexec

GRANT ALL PRIVILEGES ON DATABASE vacmatch TO vacmatch_user;