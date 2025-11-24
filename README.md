## Steam Orphan Cleanup

Utility for locating and removing leftover Steam game directories that remain after uninstalling a game. The tool discovers every Steam library (even on secondary drives) by searching for `Steam`, `SteamLibrary`, and `steamapps` directories near the executable or a user-provided root, then cross-references each library’s `appmanifest_*.acf` files to decide which folders under `steamapps/common` are still registered with Steam.

### Features

- Automatically discovers multiple Steam libraries via `libraryfolders.vdf`.
- Detects orphaned folders simply by checking whether an `appmanifest_*.acf` exists for the directory.
- Moves orphaned folders to the system recycle bin using `send2trash`, so recovery is still possible.
- Command-line flags for specifying the search root, forcing retention of certain folder names, and suppressing the final pause prompt.

### Usage

1. Install dependencies (required for the Python version):
   ```
   pip install send2trash
   ```
2. Run the script:
   ```
   python cleanup_steam_games.py --search-root "D:\"
   ```
   - `--search-root` defaults to the script/executable’s directory.
   - `--keep` lets you list folder names that should never be removed.
   - `--no-pause` disables the “press Enter to exit” prompt.

### Single-file Executable

Use PyInstaller to build a portable binary that bundles `send2trash`:
```
pyinstaller --clean --onefile --hidden-import send2trash cleanup_steam_games.py
```
The resulting `dist/cleanup_steam_games.exe` can be copied anywhere and run without Python installed.

### Safety Notes

- Always review the detected folders before confirming deletion. The script prints every candidate path and its approximate size before moving anything to the recycle bin.
- Restoring a mistakenly cleaned folder is as simple as recovering it from the recycle bin.

