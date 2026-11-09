# OrcaSlicer assets (AGPL-3.0)

From OrcaSlicer `v2.5.0` (https://github.com/OrcaSlicer/OrcaSlicer/tree/v2.5.0), AGPL-3.0 like Printbuddy.
See `THIRD_PARTY_NOTICES.md` at the repository root.

- `settings-layout.json`: generated, do not edit. Pages, option groups and order of the process, filament and
  printer settings (`Tab.cpp`), option labels, tooltips, units, ranges, enum labels and Simple / Advanced mode
  (`PrintConfig.cpp`), and their Italian strings (`OrcaSlicer_it.po`). It drives the workspace's layout and labels
  only; what can be edited and how values are checked is Printbuddy's (`backend/app/farm/workspace/settings.py`).
- `icons/`: SVGs copied unchanged from `resources/images/` (icons only, no logos or brand marks).

Regenerate for another release:

    git clone --depth 1 --branch <tag> https://github.com/OrcaSlicer/OrcaSlicer /tmp/orca
    python tools/orca_settings/generate.py /tmp/orca <tag>
