# Third-party notices

## OrcaSlicer

- Project: OrcaSlicer, https://github.com/OrcaSlicer/OrcaSlicer
- Version used: release tag `v2.5.0` (commit `40ecc66f78ed72dc368e0c1ebe40d77f00cc8a19`)
- License: GNU Affero General Public License v3.0 (AGPL-3.0), the same license as Printbuddy (see `LICENSE`)
- Used in: `frontend/src/farm/workspace/orca-assets/` (3MF Workspace)
  - `settings-layout.json`: data generated from OrcaSlicer's `src/slic3r/GUI/Tab.cpp`, `src/libslic3r/PrintConfig.cpp`,
    `src/libslic3r/PublishSettings.cpp`, `src/libslic3r/PrintConfig.hpp` and `localization/i18n/it/OrcaSlicer_it.po`
    (settings pages, groups and order, option labels, tooltips, units, ranges, enum labels, modes, the settings an
    object can have of its own, the Italian of those and of the 3D view's toolbar, plate buttons and right-click
    menus) by `tools/orca_settings/generate.py`;
  - `icons/*.svg`: icons copied unchanged from OrcaSlicer's `resources/images/` (settings pages, pattern values,
    3D toolbar and canvas buttons, plate buttons, object parts, printer, filament, search, edit, plate settings).
    No OrcaSlicer or Bambu Lab logos, splash screens or brand marks.
- The colours of the workspace's dark theme follow OrcaSlicer's dark palette (`src/slic3r/GUI/Widgets/StateColor.cpp`).

OrcaSlicer is a fork of Bambu Studio, itself based on PrusaSlicer and Slic3r; the copyright of the files above belongs
to their authors, as recorded in the OrcaSlicer repository.
