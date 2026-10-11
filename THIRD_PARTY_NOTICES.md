# Third-party software

The CleanSync image includes:
- rclone (MIT License) — https://github.com/rclone/rclone
- poppler-utils, for PDF previews (GPL-2.0-or-later; a separate program, installed from Debian) — https://poppler.freedesktop.org
- Python and the packages listed in `backend/uv.lock`, each under its own license (Pillow: MIT-CMU; nh3, which cleans HTML mail: MIT; python-docx, openpyxl, python-pptx are MIT and only used at build time to make blank Office files).
- The web app's packages listed in `frontend/package-lock.json`, each under its own license (React, React Router, TanStack Query, Tailwind CSS: MIT).
- Material Symbols icons (Apache-2.0) — https://github.com/google/material-design-icons, via `@material-symbols/svg-400`.
- Public Sans font (SIL Open Font License 1.1) — https://github.com/uswds/public-sans, via `@fontsource-variable/public-sans`.
