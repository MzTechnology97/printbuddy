# OrcaSlicer assets (AGPL-3.0)

From OrcaSlicer `v2.5.0` (https://github.com/OrcaSlicer/OrcaSlicer/tree/v2.5.0), AGPL-3.0 like Printbuddy.
See `THIRD_PARTY_NOTICES.md` at the repository root.

- `settings-layout.json`: generated, do not edit. Pages, option groups and order of the process, filament and
  printer settings (`Tab.cpp`), option labels, tooltips, units, ranges, enum labels and Simple / Advanced mode
  (`PrintConfig.cpp`), each enum value's `param_<value>` icon, the settings an object can have of its own
  (`PrintConfig.hpp`), a plate's own settings, and the Italian strings of all of them and of the 3D view's toolbar
  and menus (`OrcaSlicer_it.po`). It drives the workspace's layout and labels
  only; what can be edited and how values are checked is Printbuddy's (`backend/app/farm/workspace/settings.py`).
- `icons/`: SVGs copied unchanged from `resources/images/` (icons only, no logos or brand marks).

- `settings-layout-bambu.json`: the same from Bambu Studio `v02.09.01.70`, for projects saved by Bambu Studio
  (the icons stay OrcaSlicer's).
- The backend checks values against `backend/app/data/slicer_options/<slicer>.json`, written by the same run.

Regenerate for another release:

    git clone --depth 1 --branch <tag> https://github.com/OrcaSlicer/OrcaSlicer /tmp/orca
    python tools/orca_settings/generate.py /tmp/orca <tag>
    git clone --depth 1 --branch <tag> https://github.com/bambulab/BambuStudio /tmp/bambu
    python tools/orca_settings/generate.py /tmp/bambu <tag> --slicer bambu_studio
