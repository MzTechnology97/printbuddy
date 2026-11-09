"""3MF Workspace (fork): open a library 3MF, edit its project settings, save a copy, slice the copy.

* ``project``: safe reading of the archive and its structure (plates, objects, per-object settings),
  and the copy that rewrites only ``Metadata/project_settings.config``;
* ``settings``: which settings are shown and which can change (from the preset field schemas in
  ``backend/app/data``), and the backend check of every change;
* ``routes``: ``/farm/workspace/files/{id}`` (read) and ``/farm/workspace/files/{id}/copy`` (save).

The original is never written: a copy is a new internal library file, outside watched folders, so
library sync, plates, products, re-slicing and the queue never see it. It is sliced only when asked,
through the farm's embedded-settings slicing (``farm.routes``, ``farm.slicing``).
"""
