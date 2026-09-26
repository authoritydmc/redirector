#!/bin/bash
set -e

echo "🚀 Entrypoint started"

# Step 1: Init migrations folder if not present
if [ ! -d "migrations" ]; then
  echo "📦 No migrations folder. Running flask db init..."
  flask db init
fi

# Step 2: Apply existing migrations in production (avoid auto-migrating in container runtime)
echo "🔄 Running database upgrade..."
until flask db upgrade; do
  echo "⏳ Waiting for database..."
  sleep 2
done

echo "✅ Database upgrade complete. Starting Gunicorn..."
exec gunicorn -c gunicorn.conf.py "wsgi:app"