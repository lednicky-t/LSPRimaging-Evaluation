import sys


def run() -> None:
    # The launch screen goes up before the app's own modules are imported (that import alone takes ~3.5 s): see lspr_imaging_app/launch.py.
    from PyQt6.QtWidgets import QApplication

    from lspr_imaging_app.launch import LaunchSplash

    app = QApplication(sys.argv)
    splash = LaunchSplash()
    splash.show("Loading modules...")
    from lspr_imaging_app.app_rewrite import main

    main(app=app, splash=splash)


if __name__ == "__main__":
    run()
