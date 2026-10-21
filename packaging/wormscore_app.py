"""Entry point for the Windows app built by PyInstaller.

No arguments: open the app window (home screen). --classic: the older launcher window.
Other arguments: run them as a command, for example  wormscore.exe prepare C:\\projects\\my_project
"""
import sys

if __name__ == "__main__":
    # Older macOS versions add a -psn_... argument when an app is opened from Finder.
    sys.argv = [a for a in sys.argv if not a.startswith("-psn_")]
    if len(sys.argv) > 1 and sys.argv[1:] != ["--classic"]:
        from wormscore.cli import main
    else:
        from wormscore.home import main
    main()
