import sys


def run() -> None:
    # The launch screen goes up before the app's own modules are imported (that import alone takes ~3.5 s): see lspr_imaging_app/launch.py.
    from PyQt6.QtWidgets import QApplication

    from lspr_imaging_app.launch import LaunchSplash
    from lspr_ui import APP_ID_LSPRI_EVALUATION, set_windows_app_user_model_id

    # The taskbar identity must be set before any window exists (the launch screen is one); the Suite's Start-menu shortcut carries the same id.
    set_windows_app_user_model_id(APP_ID_LSPRI_EVALUATION)
    from lspr_imaging_app.opengl_surface import configure_opengl_surface

    configure_opengl_surface()  # before the QApplication: Play no longer waits for the screen refresh (opengl_surface.py)
    from lspr_imaging_app.display_options import apply_saved_resolution

    apply_saved_resolution()  # before the QApplication: Preferences -> Performance -> Screen resolution (display_options.py)
    app = QApplication(sys.argv)
    splash = LaunchSplash()
    splash.show("Loading modules...")
    from lspr_imaging_app.app_rewrite import main

    main(app=app, splash=splash)


if __name__ == "__main__":
    run()
