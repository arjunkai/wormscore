"""Entry point for the Windows app built by PyInstaller.

No arguments: open the launcher window. With arguments: run them as a command,
for example  wormscore.exe prepare C:\\projects\\my_project
"""
import sys

if __name__ == "__main__":
    if len(sys.argv) > 1:
        from wormscore.cli import main
    else:
        from wormscore.launcher import main
    main()
