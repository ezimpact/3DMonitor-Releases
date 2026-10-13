# Model preview dependencies

- Three.js **0.180.0**: [upstream JavaScript sources](https://github.com/mrdoob/three.js/tree/r180). Included JavaScript is the source itself. The import specifier in `OrbitControls.js` and `STLLoader.js` was changed from `three` to `./three.module.js`.
- occt-import-js **0.0.23**: [upstream importer sources and build scripts](https://github.com/kovacsv/occt-import-js/tree/0.0.23).
- Open CASCADE Technology, commit **d2abb6d844231cb8f29be6894440874a4700e4a5**: [matching source](https://github.com/Open-Cascade-SAS/OCCT/tree/d2abb6d844231cb8f29be6894440874a4700e4a5).

The original notices accompany the files in `work/k1-monitor/k1monitor/static/vendor/model-preview/` and `THIRD_PARTY_NOTICES.txt`. Existing third-party notices remain unchanged.

`THIRD_PARTY_SOURCES.json` records the exact archive hashes. `work/fetch_third_party_sources.py` downloads and verifies these archives for the clean installer payload. The JS/WASM files have a separate hash manifest in their directory.

To rebuild occt-import-js, extract the matching npm source archive, place the OCCT source in its `occt` subdirectory, and run the supplied `tools/setup_emscripten_win.bat` and `tools/build_wasm_win_release.bat`. The pinned upstream toolchain is Emscripten **3.1.69**. Model-preview JS/WASM modules are separate files and can be replaced with compatible locally rebuilt versions.

Предпросмотр использует перечисленные выше отдельные зависимости. Их исходники и оригинальные инструкции доступны по закреплённым ссылкам; хеши архивов и файлов сохранены. Файлы JS/WASM можно заменить совместимой собственной сборкой.
