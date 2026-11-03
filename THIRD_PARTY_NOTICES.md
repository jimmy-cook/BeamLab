# Included runtime components

The Windows desktop executable includes Python 3.12, Tcl/Tk 8.6 and comtypes 1.4.17. Their available license notices are preserved in `licenses/`.

It was packaged with PyInstaller 6.22.3. PyInstaller's licensing exception permits distribution of applications built with its bootloader; see [PyInstaller licensing](https://pyinstaller.org/en/stable/license.html).

ETABS, its API assemblies, the CSI API help, and the proprietary ACI design manuals are not bundled with the application. The application connects to the user's installed, licensed ETABS 22. Automatically generated COM wrapper caches are created locally when connecting.
