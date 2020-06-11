from winreg import *
from contextlib import suppress
import itertools
from pathlib import Path
from os import path


def subkeys(path, hkey=HKEY_LOCAL_MACHINE, flags=0):
    with suppress(WindowsError), OpenKey(hkey, path, 0, KEY_READ) as k:
        for i in itertools.count():
            yield EnumKey(k, i)

def find_codec2():
    for key in subkeys('SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall'):
        if "FreeDV" in key:
            subkey = OpenKey(HKEY_LOCAL_MACHINE, f"SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{key}")
            value = QueryValueEx(subkey, "UninstallString")
            dll_path = Path(value[0]).parent / "bin/"
            if(path.exists(dll_path)):
                return str(dll_path)
    for key in subkeys('SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall'):
        if "FreeDV" in key:
            subkey = OpenKey(HKEY_LOCAL_MACHINE, f"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{key}")
            value = QueryValueEx(subkey, "UninstallString")
            dll_path = Path(value[0]).parent / "bin/"
            if(path.exists(dll_path)):
                return str(dll_path)
    return None
