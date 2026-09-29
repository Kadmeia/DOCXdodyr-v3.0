"""Интерактивная smoke-проверка основных окон веб-интерфейса."""

from pathlib import Path
import sys
import threading
import time

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import webview

from main import ApiWrapper


def run_test(window):
    """Открывает и закрывает основные модальные окна, затем меняет вкладку."""
    try:
        print("Waiting for app to load...")
        time.sleep(2)

        modals = [
            ("List Settings", "showListSettingsModal()", "closeListSettingsModal()", "list-settings-overlay"),
            (
                "Placeholder Settings",
                "showPlaceholderSettingsModal()",
                "closePlaceholderSettingsModal()",
                "placeholder-settings-overlay",
            ),
        ]

        for name, open_js, close_js, overlay_id in modals:
            print(f"Testing {name}")
            window.evaluate_js(open_js)
            time.sleep(1)
            window.evaluate_js(close_js)
            time.sleep(1.5)
            display = window.evaluate_js(
                f"document.getElementById('{overlay_id}').style.display;"
            )
            if display != "none":
                raise RuntimeError(f"{name} did not close properly")

        window.evaluate_js("switchTab('restore');")
        time.sleep(0.5)
        display = window.evaluate_js(
            "document.getElementById('tab-restore').style.display;"
        )
        if display != "flex":
            raise RuntimeError("Tab switching did not work")

        print("All UI tests passed successfully")
        window.destroy()
    except Exception as exc:
        print(f"Test failed: {exc}")
        window.destroy()
        sys.exit(1)


if __name__ == "__main__":
    wrapper = ApiWrapper()
    html_path = Path(__file__).resolve().parent.parent / "web" / "index.html"
    app_window = webview.create_window(
        title="Test UI",
        url=str(html_path),
        js_api=wrapper,
    )
    wrapper.set_window(app_window)
    threading.Thread(target=run_test, args=(app_window,), daemon=True).start()
    webview.start()
