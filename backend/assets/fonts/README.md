# Bundled Poppins fonts

Original, unmodified Google Fonts distribution:

- https://github.com/google/fonts/tree/main/ofl/poppins
- https://github.com/google/fonts/blob/main/ofl/poppins/OFL.txt

Copyright 2020 The Poppins Project Authors (https://github.com/itfoundry/Poppins).
Distributed under the SIL Open Font License 1.1; see the included `OFL.txt`.

Verified SHA-256:

- `Poppins-SemiBold.ttf` (weight 600): `d3bf1bdaf0550e83da9ac0b1d1d9fe6db086835a83aa28578e609a394b9a0286`
- `Poppins-Bold.ttf` (weight 700): `983676516167748b74de6f4771fb384c664fd913acb8b471122ecacf5da5ea6c`

The browser uses family `Poppins` with the respective weight. Libass resolves
weight 600 reliably with the legacy family name `Poppins SemiBold`; weight 700
uses `Poppins`. Explicit local font loading avoids depending on system fonts.
