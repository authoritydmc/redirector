#!/bin/bash
set -e

echo "?? Entrypoint started"

# Step 1: Init migrations folder if not present
if [ ! -d "migrations" ]; then
  echo "?? No migrations folder. Running flask db init..."
  flask db init
fi

# Step 2: Auto-generate migration (safe ? only generates if needed)
echo "?? Checking for model changes to generate migration..."
flask db migrate -m "auto migration" || echo "?? No changes detected or migration already exists."

# Step 3: Always upgrade
echo "?? Running DB upgrade..."
until flask db upgrade; do
  echo "? Waiting for DB..."
  sleep 2
done

echo "? Migration applied. Starting Gunicorn..."
exec gunicorn -c gunicorn.conf.py "wsgi:app"
echo "?? Gunicorn started successfully"