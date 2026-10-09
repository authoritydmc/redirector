"""Admin backup: zip archives of domain tables + manifest (EPIC-04 task 7).

Restore stays staged (see api-inventory): the JSON table dumps are shaped
for a future row-upsert restore in the import-v2 style.
"""
