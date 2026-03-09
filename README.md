# Microfiche Problem Detector

Windows GUI tool for detecting problematic microfiche PDF pages.

Current detection modes:
- `LLM`: uses configured API models
- `PY`: uses local Python heuristic logic only

Current outputs:
- `CSV`: writes flagged rows
- `Overlap`: exports `O_*.pdf`
- `Blurry`: exports `B_*.pdf`
- `Extracted Original`: exports `E_*.pdf`
- `Uncertain`: always exported as `U_*.pdf` and written to CSV

`Extracted Original` currently removes:
- overlap pages
- blurry pages

It does **not** remove `uncertain` pages.

## Files In This Folder

- `microfiche_overlap_third_party_app.py`
  Windows EXE entrypoint for the third-party app
- `microfiche_overlap_extractor.py`
  Main application logic and GUI
- `microfiche_overlap_detector_py.py`
  Standalone pure-Python detector script
- `requirements.txt`
  Python dependencies
- `build_exe.bat`
  Local Windows build script
- `.github/workflows/build-windows-exe.yml`
  GitHub Actions workflow for Windows EXE build

## Upload To GitHub

Upload the contents of this folder as the **repo root**.

Required structure:

```text
repo-root/
  microfiche_overlap_extractor.py
  microfiche_overlap_third_party_app.py
  microfiche_overlap_detector_py.py
  requirements.txt
  build_exe.bat
  .github/
    workflows/
      build-windows-exe.yml
```

## Build On GitHub

1. Push the repo to GitHub.
2. Open the repo in a browser.
3. Go to `Actions`.
4. Open `Build Windows EXE`.
5. Click `Run workflow`.
6. After it finishes, download artifact `MicroficheOverlapExtractor-windows`.

Expected output:

```text
dist/MicroficheOverlapExtractor.exe
```

## Build On Windows Locally

Run:

```bat
build_exe.bat
```

If build succeeds, output path is:

```text
dist\MicroficheOverlapExtractor.exe
```

If build fails, inspect:

```text
build.log
```

## Runtime Behavior

Top controls:
- `LLM`
- `PY`
- `Model`
- `Scan Directory`
- `Output Directory`

Outputs:
- `CSV`
- `Overlap`
- `Blurry`
- `Extracted Original`

Run controls:
- `Run`
- `Pause`
- `Stop`

## Notes

- `PY` mode does not send API requests.
- `LLM` mode uses the configured third-party model profiles.
- `U_*.pdf` uncertain-page export is automatic.
- CSV includes:
  - overlap pages
  - blurry pages
  - uncertain pages

