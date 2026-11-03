# Optional: rebuild both executables from source with a local Python environment.
# ETABS Beam Lab 0.7.exe = desktop window; ETABS Beam Lab Web.exe = browser screen with the live beam view.
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    python -m pip install -e . pyinstaller
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
    $beamManifest = Join-Path $PSScriptRoot 'windows.manifest'
    $beamData = Join-Path $PSScriptRoot 'beam_lab/data'
    $beamWeb = Join-Path $PSScriptRoot 'beam_lab/web'
    foreach ($build in @(@{Name = 'ETABS Beam Lab 0.7'; Entry = 'desktop_entry.py'}, @{Name = 'ETABS Beam Lab Web'; Entry = 'desktop_web_entry.py'})) {
        python -m PyInstaller --noconfirm --onefile --windowed --manifest $beamManifest --name $build.Name --distpath . --workpath build --specpath build --add-data "$beamData;beam_lab/data" --add-data "$beamWeb;beam_lab/web" --hidden-import comtypes.client --hidden-import comtypes.automation --hidden-import comtypes.safearray --hidden-import comtypes.typeinfo $build.Entry
        if ($LASTEXITCODE -ne 0) { throw "Build failed: $($build.Name)" }
    }
} finally {
    Pop-Location
}
