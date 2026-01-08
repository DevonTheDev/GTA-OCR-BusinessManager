"""PyInstaller build script for GTA Business Manager."""

import os
import sys
import shutil
from pathlib import Path


def build():
    """Build the application using PyInstaller."""
    print("=" * 50)
    print("GTA Business Manager - Build Script")
    print("=" * 50)

    # Check for PyInstaller
    try:
        import PyInstaller.__main__
    except ImportError:
        print("Error: PyInstaller not installed.")
        print("Install with: pip install pyinstaller")
        return 1

    # Paths
    project_dir = Path(__file__).parent
    src_dir = project_dir / "src"
    assets_dir = project_dir / "assets"
    dist_dir = project_dir / "dist"
    build_dir = project_dir / "build"

    # Clean previous builds
    print("\nCleaning previous builds...")
    if dist_dir.exists():
        shutil.rmtree(dist_dir)
    if build_dir.exists():
        shutil.rmtree(build_dir)

    # Create spec file content
    spec_content = f'''# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['{src_dir / "main.py"}'],
    pathex=['{project_dir}'],
    binaries=[],
    datas=[
        ('{assets_dir}', 'assets'),
    ],
    hiddenimports=[
        'PyQt6.QtWidgets',
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'winocr',
        'cv2',
        'numpy',
        'mss',
        'PIL',
        'sqlalchemy',
        'yaml',
        'keyboard',
    ],
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='GTABusinessManager',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # Set to True for debugging
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='{assets_dir / "app_icon.ico"}' if (assets_dir / "app_icon.ico").exists() else None,
)
'''

    # Write spec file
    spec_file = project_dir / "GTABusinessManager.spec"
    print(f"\nWriting spec file: {spec_file}")
    with open(spec_file, "w") as f:
        f.write(spec_content)

    # Run PyInstaller
    print("\nRunning PyInstaller...")
    print("-" * 50)

    try:
        PyInstaller.__main__.run([
            str(spec_file),
            '--clean',
            '--noconfirm',
        ])
    except SystemExit as e:
        if e.code != 0:
            print(f"\nBuild failed with exit code {e.code}")
            return e.code

    # Check output
    exe_path = dist_dir / "GTABusinessManager.exe"
    if exe_path.exists():
        size_mb = exe_path.stat().st_size / (1024 * 1024)
        print("\n" + "=" * 50)
        print("Build successful!")
        print("=" * 50)
        print(f"Output: {exe_path}")
        print(f"Size: {size_mb:.1f} MB")
        print("\nTo run: dist\\GTABusinessManager.exe")
        return 0
    else:
        print("\nBuild failed - executable not found")
        return 1


def build_onedir():
    """Build as one directory (faster startup, easier debugging)."""
    print("Building in one-directory mode...")

    try:
        import PyInstaller.__main__
    except ImportError:
        print("Error: PyInstaller not installed.")
        return 1

    project_dir = Path(__file__).parent
    src_dir = project_dir / "src"
    assets_dir = project_dir / "assets"

    args = [
        str(src_dir / "main.py"),
        "--name=GTABusinessManager",
        f"--add-data={assets_dir};assets",
        "--hidden-import=PyQt6.QtWidgets",
        "--hidden-import=PyQt6.QtCore",
        "--hidden-import=PyQt6.QtGui",
        "--hidden-import=winocr",
        "--hidden-import=cv2",
        "--hidden-import=numpy",
        "--hidden-import=mss",
        "--hidden-import=PIL",
        "--hidden-import=sqlalchemy",
        "--hidden-import=yaml",
        "--hidden-import=keyboard",
        "--windowed",
        "--clean",
        "--noconfirm",
    ]

    icon_path = assets_dir / "app_icon.ico"
    if icon_path.exists():
        args.append(f"--icon={icon_path}")

    try:
        PyInstaller.__main__.run(args)
        print("\nBuild complete! Output in dist/GTABusinessManager/")
        return 0
    except SystemExit as e:
        return e.code if e.code else 0


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "onefile"

    if mode == "onedir":
        sys.exit(build_onedir())
    else:
        sys.exit(build())
