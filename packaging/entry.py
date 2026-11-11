"""Frozen-app entry point (PyInstaller): start the app, from a downloaded code patch when there is
a good one for this bundle (packaging/patchboot.py)."""
import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    import patchboot

    patchboot.boot()
