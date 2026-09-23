"""PyInstaller entry point. `src/zerotrace/__main__.py` uses a relative import (`from .cli import
main`), which only works when Python already knows `zerotrace` as a package (`python -m
zerotrace`); PyInstaller runs its entry script directly as `__main__` with no parent package, so
that relative import fails. This wrapper does an absolute import instead.
"""
from zerotrace.cli import main

if __name__ == "__main__":
    main()
