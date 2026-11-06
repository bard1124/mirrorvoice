"""Frozen-app entry point (PyInstaller)."""
import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from tongchuan.__main__ import main

    main()
