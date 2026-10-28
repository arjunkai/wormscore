"""The Mac app's place in the Dock.

The Mac app shows its pages in the browser and has no window of its own. Without Cocoa's event
loop macOS cannot reach it: opening wormscore.app again while it still ran did nothing (macOS
only switches to a running copy, it never starts a second one) and Quit from the Dock failed
("Application isn't running"). This runs that loop on the main thread: opening the app again
shows the home page, and Quit (Dock, menu or Cmd+Q) closes it cleanly, asking first while a step
runs. Only the downloaded wormscore.app uses it; a pip install runs as before.
"""
import sys

_keep = {}        # the delegate and menu must outlive run(); Cocoa holds only weak references


def available():
    if sys.platform != "darwin" or not getattr(sys, "frozen", False):
        return False
    try:
        import AppKit  # noqa: F401
        from PyObjCTools import AppHelper  # noqa: F401
    except ImportError:
        return False
    return True


def quit_question(app):
    """The question to ask before quitting, or None to quit at once (same rule as closing the
    last window on Windows: only a running step asks)."""
    if app.job.running:
        return (f'"{app.job.title}" is still running. Quitting wormscore stops it, and it would '
                "have to be run again.")
    return None


def run(on_reopen, question, on_quit):
    """Run Cocoa's event loop until stop() or Quit. on_reopen(): the app was opened again (or its
    Dock icon clicked). question(): see quit_question. on_quit(): runs just before the app ends."""
    import AppKit
    from Foundation import NSObject
    from PyObjCTools import AppHelper

    class WormscoreDelegate(NSObject):
        def applicationShouldHandleReopen_hasVisibleWindows_(self, sender, has_windows):
            on_reopen()
            return False

        def showHome_(self, sender):
            on_reopen()

        def applicationShouldTerminate_(self, sender):
            text = question()
            if text is None:
                return AppKit.NSTerminateNow
            AppKit.NSApp.activateIgnoringOtherApps_(True)
            alert = AppKit.NSAlert.alloc().init()
            alert.setMessageText_("Quit wormscore?")
            alert.setInformativeText_(text)
            alert.addButtonWithTitle_("Keep running")       # first button: the default, Return keeps it
            alert.addButtonWithTitle_("Quit anyway")
            leave = alert.runModal() == AppKit.NSAlertSecondButtonReturn
            return AppKit.NSTerminateNow if leave else AppKit.NSTerminateCancel

        def applicationWillTerminate_(self, note):
            on_quit()

    nsapp = AppKit.NSApplication.sharedApplication()
    delegate = WormscoreDelegate.alloc().init()
    nsapp.setDelegate_(delegate)

    # The menu bar shown while wormscore is the active app: open the home page, or quit.
    bar = AppKit.NSMenu.alloc().init()
    top = AppKit.NSMenuItem.alloc().init()
    bar.addItem_(top)
    menu = AppKit.NSMenu.alloc().initWithTitle_("wormscore")
    show = menu.addItemWithTitle_action_keyEquivalent_("Open wormscore", "showHome:", "o")
    show.setTarget_(delegate)
    menu.addItem_(AppKit.NSMenuItem.separatorItem())
    menu.addItemWithTitle_action_keyEquivalent_("Quit wormscore", "terminate:", "q")
    top.setSubmenu_(menu)
    nsapp.setMainMenu_(bar)
    _keep.update(delegate=delegate, menu=bar)
    AppHelper.runEventLoop(installInterrupt=False)


def stop():
    """End run() from any thread (the app closes by itself, or for an update). It ends the way
    Quit does, so on_quit runs; no step is running then, so nothing is asked."""
    from PyObjCTools import AppHelper
    AppHelper.callAfter(AppHelper.stopEventLoop)
