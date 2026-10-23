# Third-party notices

Rust SWF Studio is GPL-3.0-or-later. The React layout, styling, PreviewSurface and Dialog derive from the user's PySWF Studio project, under GPL-3.0-or-later. The AVM tables and migration reference algorithms derive from that same project and are verified with independent fixtures.

- Ruffle `swf` 0.3.0, revision bfb540427dbdacb99eac9c0adb66509ba90151d0: MIT OR Apache-2.0 (MIT option used).
- Electron (with Chromium and Node.js) and electron-builder: MIT; the packaged application carries `LICENSE.electron.txt` and `LICENSES.chromium.html` with the licences of Chromium's own components, and the dependency licence texts under `resources/licenses`.
- Rust standard library and tools: MIT OR Apache-2.0.
- React / React DOM / Vite / Lucide / Motion: MIT. Tailwind: MIT.
- flate2, lzma-rs, serde, serde_json, image, sha2 and their transitive dependencies retain their own licenses; see Cargo.lock and the license inventory in the delivery.

Development-only references: JPEXS is GPLv3 / LGPLv3 for ffdec_lib; the local corpus retains its original provenance and is not bundled in the application. Ruffle source checkout, GCC/MinGW build toolchain, Rustup and 7-Zip are development tools, excluded from the portable application. MinGW/GCC licensing is recorded in its downloaded distribution; 7zr is public domain. No reference Java runtime or Python runtime is included.

Dependency license texts are collected in dist/RustSWFStudio/licenses when building the release.

