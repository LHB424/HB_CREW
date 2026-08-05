# -*- mode: python ; coding: utf-8 -*-
# 빌드: 저장소 루트에서  pyinstaller build_src\crew_launcher.spec
# CREW 는 화면이 목록 하나뿐이라 PD 와 달리 QtMultimedia(플레이블라스트) 수집이 필요 없다.
# 아이콘은 아직 없다 — crew_icon.ico 를 만들면 EXE(icon=...) 한 줄만 켜면 된다.

a = Analysis(
    ['crew_launcher.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='crew_launcher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
