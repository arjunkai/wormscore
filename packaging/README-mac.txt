wormscore for macOS (Apple silicon: M1 and newer)

1. Unzip, then drag wormscore.app into Applications.
2. The first time, macOS blocks it because the app is not signed with a paid Apple developer
   account. Open it once like this:
   - Double-click wormscore.app and close the warning.
   - Open System Settings > Privacy & Security, scroll down, and click "Open Anyway" next to
     wormscore. Confirm with your password.
   After that it opens normally.
3. wormscore opens in your web browser. Nothing leaves your computer: the page is served from
   your own Mac (127.0.0.1).

Intel Mac, or prefer Python? Install Python 3.12 or newer, then: pip install wormscore
and run wormscore-launcher.

Command line use: /Applications/wormscore.app/Contents/MacOS/wormscore prepare /path/to/project

License: MIT (LICENSE.txt). Bundled software and its licenses: THIRD_PARTY_LICENSES.txt.
Source and guide: https://github.com/arjunkai/wormscore
