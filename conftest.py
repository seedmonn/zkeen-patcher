import pathlib, sys
SCRIPTS = pathlib.Path(__file__).resolve().parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
