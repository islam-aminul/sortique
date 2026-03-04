"""Cross-platform desktop notifications using Qt's system tray."""

from __future__ import annotations

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QSystemTrayIcon


class NotificationService:
    """Cross-platform desktop notifications using Qt's system tray."""

    def __init__(self) -> None:
        self._tray_icon: QSystemTrayIcon | None = None

    def initialize(self, app: QApplication, icon: QIcon | None = None) -> None:
        """Set up system tray icon for notifications."""
        tray_icon = icon or QIcon.fromTheme("applications-utilities")
        self._tray_icon = QSystemTrayIcon(tray_icon, app)
        self._tray_icon.show()

    def notify_completion(self, session_stats: dict) -> None:
        """Show a processing-complete notification."""
        processed = session_stats.get("files_processed", 0)
        dupes = session_stats.get("dupes_found", 0)
        saved = self._format_bytes(session_stats.get("space_saved", 0))

        self._show(
            "Sortique: Processing complete",
            f"{processed} files organized, {dupes} duplicates found, {saved} saved.",
        )

    def notify_error(self, error_message: str) -> None:
        """Show an error notification."""
        self._show(
            "Sortique: Error",
            error_message,
            QSystemTrayIcon.MessageIcon.Critical,
        )

    def notify_paused(self, reason: str) -> None:
        """Show a paused notification (e.g. low disk space)."""
        self._show(
            "Sortique: Processing paused",
            reason,
            QSystemTrayIcon.MessageIcon.Warning,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_bytes(bytes_count: int) -> str:
        """Format *bytes_count* to a human-readable string."""
        for unit in ("B", "KB", "MB", "GB"):
            if abs(bytes_count) < 1024:
                return f"{bytes_count:.1f} {unit}" if unit != "B" else f"{bytes_count} B"
            bytes_count /= 1024
        return f"{bytes_count:.1f} TB"

    def _show(
        self,
        title: str,
        message: str,
        icon_type: QSystemTrayIcon.MessageIcon = QSystemTrayIcon.MessageIcon.Information,
    ) -> None:
        """Show the notification via system tray, falling back to print()."""
        if (
            self._tray_icon is not None
            and QSystemTrayIcon.isSystemTrayAvailable()
        ):
            self._tray_icon.showMessage(title, message, icon_type, 5000)
        else:
            print(f"{title}: {message}")
