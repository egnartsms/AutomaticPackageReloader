import sublime_plugin
import sublime
import os
from glob import glob
import re
import platform
from threading import Thread, Lock

from .reloader import reload_package, ProgressBar


try:
    reload_lock  # Preserve same lock across reloads
except NameError:
    reload_lock = Lock()


if platform.system() == 'Windows':
    # On Windows, paths may not be properly cased so we feed them to glob.glob
    # (see https://github.com/randy3k/AutomaticPackageReloader/issues/10)
    def relative_to_spp(path):
        def casedpath(path):
            r = glob(re.sub(r'([^:/\\])(?=[/\\]|$)', r'[\1]', path))
            return r and r[0] or path

        spp = sublime.packages_path()
        for p in [path, casedpath(os.path.realpath(path))]:
            for sp in [spp, casedpath(os.path.realpath(spp))]:
                if p.startswith(sp + os.sep):
                    return p[len(sp):]
        return None
else:
    # On Linux and Mac OS, Sublime also loads packages if they are symlinks to folders located 
    # outside the Sublime package path (SPP). So we detect those files that are opened not via SPP 
    # symlinks but still are loaded into Sublime. We do this by scanning all SPP symlinks and 
    # checking whether the path in question is located under the symlink's target. If yes, that's
    # still our file and we return its path relative to the symlink. 
    def relative_to_spp(path):
        spp = os.path.realpath(sublime.packages_path())
        if path.startswith(spp + os.sep):
            return path[len(spp):]

        for name in os.listdir(spp):
            fullname = os.path.join(spp, name)
            if not (os.path.islink(fullname) and os.path.isdir(fullname)):
                continue

            target = os.readlink(fullname)
            if path.startswith(target + os.sep):
                return os.path.join('/', name, path[len(target + os.sep):])

        return None


class PackageReloaderListener(sublime_plugin.EventListener):

    def on_post_save(self, view):
        if view.is_scratch() or view.settings().get('is_widget'):
            return
        file_name = view.file_name()

        if file_name and file_name.endswith(".py") and relative_to_spp(file_name):
            package_reloader_settings = sublime.load_settings("package_reloader.sublime-settings")
            if package_reloader_settings.get("reload_on_save"):
                view.window().run_command("package_reloader_reload")


class PackageReloaderToggleReloadOnSaveCommand(sublime_plugin.WindowCommand):

    def run(self):
        package_reloader_settings = sublime.load_settings("package_reloader.sublime-settings")
        reload_on_save = not package_reloader_settings.get("reload_on_save")
        package_reloader_settings.set("reload_on_save", reload_on_save)
        onoff = "on" if reload_on_save else "off"
        sublime.status_message("Package Reloader: Reload on Save is %s." % onoff)


class PackageReloaderReloadCommand(sublime_plugin.WindowCommand):
    @property
    def current_package_name(self):
        view = self.window.active_view()
        if view and view.file_name():
            file_path = relative_to_spp(view.file_name())
            if file_path and file_path.endswith(".py"):
                return file_path.split(os.sep)[1]

        folders = self.window.folders()
        if folders and len(folders) > 0:
            first_folder = relative_to_spp(folders[0])
            if first_folder:
                return os.path.basename(first_folder)

        return None

    def prompt_package(self, callback):
        package = self.current_package_name
        if not package:
            package = ""
        view = sublime.active_window().show_input_panel(
            'Package:', package, callback, None, None)
        view.run_command("select_all")

    def run(self, pkg_name=None):
        if pkg_name == "<prompt>":
            self.prompt_package(lambda x: self.run(pkg_name=x))
            return

        if pkg_name is None:
            pkg_name = self.current_package_name
            if pkg_name is None:
                print("Cannot detect package name.")
                return

        Thread(
            name="AutomaticPackageReloader",
            target=self.run_async,
            args=(pkg_name,)
        ).start()

    def run_async(self, pkg_name):
        lock = reload_lock  # In case we're reloading AutoPackageReloader
        if not lock.acquire(blocking=False):
            print("Reloader is running.")
            return

        pr_settings = sublime.load_settings("package_reloader.sublime-settings")
        open_console = pr_settings.get("open_console")
        open_console_on_failure = pr_settings.get("open_console_on_failure")
        close_console_on_success = pr_settings.get("close_console_on_success")

        progress_bar = ProgressBar("Reloading %s" % pkg_name)
        progress_bar.start()

        console_opened = self.window.active_panel() == "console"
        if not console_opened and open_console:
            self.window.run_command("show_panel", {"panel": "console"})
        try:
            reload_package(pkg_name, verbose=pr_settings.get('verbose'))
            if close_console_on_success:
                self.window.run_command("hide_panel", {"panel": "console"})

            sublime.status_message("{} reloaded.".format(pkg_name))
        except Exception:
            if open_console_on_failure:
                self.window.run_command("show_panel", {"panel": "console"})
            sublime.status_message("Fail to reload {}.".format(pkg_name))
            raise
        finally:
            progress_bar.stop()
            lock.release()
