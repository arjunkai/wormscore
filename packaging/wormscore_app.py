"""Entry point for the Windows app built by PyInstaller.

No arguments: open the app window (home screen). --classic: the older launcher window.
Other arguments: run them as a command, for example  wormscore.exe prepare C:\\projects\\my_project
"""
import sys

if __name__ == "__main__":
    # Older macOS versions add a -psn_... argument when an app is opened from Finder.
    sys.argv = [a for a in sys.argv if not a.startswith("-psn_")]
    # Started from the wormscore:// link (the Start wormscore button on a window that lost its
    # connection): a web page picks that link, so ignore every argument and open the app window.
    if any(a == "--from-link" or a.lower().startswith("wormscore:") for a in sys.argv[1:]):
        sys.argv = sys.argv[:1]
    if len(sys.argv) > 1 and sys.argv[1:] != ["--classic"]:
        from wormscore.cli import main
    else:
        from wormscore.home import main
    main()
