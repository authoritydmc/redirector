"""arq background workers (EPIC-06 task 2, broker half).

Run:  arq backend.workers.settings.WorkerSettings
Requires Redis (REDIRECTOR_REDIS_URL); the API selects this backend with
REDIRECTOR_JOB_BACKEND=arq. Job rows stay the source of truth — see
backend/modules/jobs/.
"""
