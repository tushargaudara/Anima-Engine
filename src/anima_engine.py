import sys
import os
import json

from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QListWidget, QPushButton,
    QVBoxLayout, QHBoxLayout, QDesktopWidget, QFileDialog,
    QSlider, QMenu, QSystemTrayIcon, QStyle
)
from PyQt5.QtGui import QMovie
from PyQt5.QtCore import Qt, QTimer, QSize, QPoint

# === CONFIG ===
GIF_CHOICES = [
    "gifs/anime_character5.gif",
    "gifs/anime_character2.gif",
    "gifs/anime_character5.gif",
]
DEFAULT_GIF = "anime_character5.gif"

# Optional idle animation: provide this file if you want the feature
IDLE_GIF = "idle.gif"           # will only be used if file exists
IDLE_TIMEOUT_MS = 15000         # 15 seconds of no interaction -> idle

PET_SIZE = 250                  # size of pet GIF
PREVIEW_SIZE = 180              # preview GIF size in UI
ALWAYS_ON_TOP = True

CONFIG_PATH = "anima_config.json"  # config saved next to script

# === GLOBALS ===
PETS = []               # list[DraggableLabel]
selector_window = None  # GifSelectorWindow instance
CONFIG_OBJ = None       # global config object (filled in main)
tray_icon = None        # QSystemTrayIcon


# === CONFIG HELPERS ===
def load_config():
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass


def apply_window_flags(widget):
    """Apply cross-platform 'always on top' flags to a pet."""
    if sys.platform == "darwin":  # macOS
        flags = Qt.FramelessWindowHint
        if ALWAYS_ON_TOP:
            flags |= Qt.WindowStaysOnTopHint
    else:  # Windows / Linux
        flags = Qt.FramelessWindowHint | Qt.Tool
        if ALWAYS_ON_TOP:
            flags |= Qt.WindowStaysOnTopHint
    widget.setWindowFlags(flags)


# === PET LABEL ===
class DraggableLabel(QLabel):
    def __init__(self, gif_path, idle_gif_path=None, config=None, save_position=False):
        super().__init__()
        self._drag_active = False
        self._drag_offset = QPoint()
        self._locked = False
        self._movie = None
        self.save_position = save_position

        self.config = config if config is not None else {}
        self.active_gif = gif_path
        self.current_gif = gif_path

        # Idle support
        self.idle_gif = idle_gif_path if (idle_gif_path and os.path.exists(idle_gif_path)) else None
        self.is_idle = False
        self.idle_timer = QTimer(self)
        self.idle_timer.setSingleShot(True)
        self.idle_timer.timeout.connect(self.enter_idle_state)

        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(PET_SIZE, PET_SIZE)
        self._set_movie(gif_path)

        self.reset_idle_timer()

    def _set_movie(self, gif_path):
        if self._movie is not None:
            self._movie.stop()

        self._movie = QMovie(gif_path)
        self._movie.setScaledSize(QSize(PET_SIZE, PET_SIZE))
        self.setMovie(self._movie)
        self._movie.start()
        self.current_gif = gif_path

    def set_gif(self, gif_path):
        """Change the 'active' GIF for the pet."""
        self.active_gif = gif_path
        self.is_idle = False
        self._set_movie(gif_path)
        # Save last used gif (global)
        self.config["last_gif"] = gif_path
        save_config(self.config)
        self.reset_idle_timer()

    def reset_idle_timer(self):
        if self.idle_gif:
            self.idle_timer.start(IDLE_TIMEOUT_MS)
        else:
            self.idle_timer.stop()

    def enter_idle_state(self):
        if self.idle_gif and not self.is_idle:
            self.is_idle = True
            self._set_movie(self.idle_gif)

    def exit_idle_state(self):
        if self.is_idle:
            self.is_idle = False
            self._set_movie(self.active_gif)
        self.reset_idle_timer()

    # --- Mouse events ---
    def mousePressEvent(self, event):
        global selector_window
        # Any click on a pet makes it the active one for the selector
        if selector_window is not None:
            selector_window.set_current_pet(self)

        if event.button() == Qt.LeftButton:
            self.exit_idle_state()  # any interaction cancels idle
            if not self._locked:
                self._drag_active = True
                self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_active and not self._locked and (event.buttons() & Qt.LeftButton):
            new_pos = event.globalPos() - self._drag_offset
            self.move(new_pos)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_active = False
            event.accept()
            # Save position only for "main" pet if configured
            if self.save_position:
                pos = self.pos()
                self.config["pos"] = [int(pos.x()), int(pos.y())]
                save_config(self.config)
        else:
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            # Toggle lock state (opacity is controlled by slider)
            self._locked = not self._locked
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    # --- Right-click context menu (feature 1) ---
    def contextMenuEvent(self, event):
        menu = QMenu(self)

        lock_action = menu.addAction("Unlock movement" if self._locked else "Lock movement")
        change_action = menu.addAction("Change character…")
        add_action = menu.addAction("Add pet")
        remove_action = menu.addAction("Remove this pet")
        menu.addSeparator()
        quit_action = menu.addAction("Quit Anima Engine")

        # Enforce max 3 pets & min 1 pet
        if len(PETS) >= 3:
            add_action.setEnabled(False)
        if len(PETS) <= 1:
            remove_action.setEnabled(False)

        chosen = menu.exec_(event.globalPos())

        if chosen == lock_action:
            self._locked = not self._locked

        elif chosen == change_action:
            open_selector_for(self)

        elif chosen == add_action:
            add_pet()

        elif chosen == remove_action:
            remove_pet(self)

        elif chosen == quit_action:
            QApplication.quit()


# === SELECTOR WINDOW ===
class GifSelectorWindow(QWidget):
    def __init__(self, config, gif_paths, pets_list, initial_opacity_percent=100, current_pet=None):
        super().__init__()
        self.config = config
        self.gif_paths = list(gif_paths)  # editable list
        self.preview_movie = None
        self.pets_list = pets_list
        self.current_pet = current_pet   # pet currently being edited

        self.setWindowTitle("Anima Engine – Choose Character")
        self.setFixedSize(520, 360)

        # --- Widgets ---
        self.list_widget = QListWidget()
        self.preview_label = QLabel("Preview")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setFixedSize(PREVIEW_SIZE, PREVIEW_SIZE)

        self.use_button = QPushButton("Use this GIF")
        self.add_button = QPushButton("Add GIF…")
        self.delete_button = QPushButton("Delete Selected")

        # Opacity controls
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(30, 100)  # 30%–100% to avoid totally invisible
        self.opacity_slider.setValue(initial_opacity_percent)
        self.opacity_label = QLabel(f"Opacity: {initial_opacity_percent}%")

        # --- Layouts ---
        left_layout = QVBoxLayout()
        left_layout.addWidget(self.list_widget)
        left_layout.addWidget(self.use_button)

        right_layout = QVBoxLayout()
        right_layout.addWidget(self.preview_label)
        right_layout.addSpacing(10)
        right_layout.addWidget(self.opacity_label)
        right_layout.addWidget(self.opacity_slider)

        top_layout = QHBoxLayout()
        top_layout.addLayout(left_layout)
        top_layout.addLayout(right_layout)

        buttons_layout = QHBoxLayout()
        buttons_layout.addWidget(self.add_button)
        buttons_layout.addWidget(self.delete_button)

        outer_layout = QVBoxLayout()
        outer_layout.addLayout(top_layout)
        outer_layout.addLayout(buttons_layout)

        self.setLayout(outer_layout)

        # --- Connections ---
        self.list_widget.currentRowChanged.connect(self.update_preview)
        self.use_button.clicked.connect(self.apply_selection)
        self.add_button.clicked.connect(self.add_gifs)
        self.delete_button.clicked.connect(self.delete_selected)
        self.opacity_slider.valueChanged.connect(self.on_opacity_changed)

        # Fill list
        self.rebuild_list()

        # Auto-select first if exists
        if self.gif_paths:
            self.list_widget.setCurrentRow(0)

    def rebuild_list(self):
        """Rebuild the QListWidget from gif_paths."""
        self.list_widget.clear()
        for path in self.gif_paths:
            name = os.path.basename(path)
            self.list_widget.addItem(name)

    def update_preview(self, index):
        """Update right-side preview when user changes selection."""
        if self.preview_movie is not None:
            self.preview_movie.stop()
            self.preview_movie = None
            self.preview_label.clear()

        if index < 0 or index >= len(self.gif_paths):
            self.preview_label.setText("Preview")
            return

        path = self.gif_paths[index]
        if not os.path.exists(path):
            self.preview_label.setText("File not found")
            return

        self.preview_movie = QMovie(path)
        self.preview_movie.setScaledSize(QSize(PREVIEW_SIZE, PREVIEW_SIZE))
        self.preview_label.setMovie(self.preview_movie)
        self.preview_movie.start()

    def apply_selection(self):
        """Apply selected GIF to the active pet."""
        if not self.pets_list:
            return

        index = self.list_widget.currentRow()
        if index < 0 or index >= len(self.gif_paths):
            return
        path = self.gif_paths[index]
        if not os.path.exists(path):
            return

        target_pet = self.current_pet or self.pets_list[0]
        target_pet.set_gif(path)

    def add_gifs(self):
        """Import GIFs via file dialog and add them to the list."""
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select GIF files",
            "",
            "GIF Images (*.gif)"
        )
        if not files:
            return

        added = False
        for f in files:
            if f not in self.gif_paths:
                self.gif_paths.append(f)
                added = True

        if added:
            self.rebuild_list()

    def delete_selected(self):
        """Remove selected GIF from the list (not from disk)."""
        index = self.list_widget.currentRow()
        if index < 0 or index >= len(self.gif_paths):
            return

        self.gif_paths.pop(index)
        self.rebuild_list()

        # Adjust selection
        if self.gif_paths:
            new_index = min(index, len(self.gif_paths) - 1)
            self.list_widget.setCurrentRow(new_index)
        else:
            self.preview_label.setText("Preview")
            self.preview_movie = None

    def on_opacity_changed(self, value):
        """Handle opacity slider changes: affects all pets."""
        self.opacity_label.setText(f"Opacity: {value}%")
        opacity = value / 100.0

        for pet in self.pets_list:
            pet.setWindowOpacity(opacity)

        self.config["opacity"] = opacity
        save_config(self.config)

    def set_current_pet(self, pet):
        """Switch which pet is controlled by 'Use this GIF'."""
        self.current_pet = pet

    def closeEvent(self, event):
        """
        Override close (X button) to hide instead of quitting.
        The app stays in system tray.
        """
        event.ignore()
        self.hide()


# === MULTI-PET HELPERS ===
def add_pet():
    """Create a new pet (max 3)."""
    global PETS, CONFIG_OBJ

    if len(PETS) >= 3:
        return

    screen = QDesktopWidget().availableGeometry()
    base_gif = PETS[0].active_gif if PETS else DEFAULT_GIF

    pet = DraggableLabel(base_gif, idle_gif_path=IDLE_GIF, config=CONFIG_OBJ, save_position=False)
    apply_window_flags(pet)

    # Place near first pet, horizontally offset
    if PETS:
        base_x, base_y = PETS[0].pos().x(), PETS[0].pos().y()
    else:
        base_x, base_y = 30, screen.height() - PET_SIZE - 50

    offset = len(PETS) * (PET_SIZE + 20)
    x = min(base_x + offset, screen.width() - PET_SIZE)
    y = base_y
    pet.move(x, y)

    # Apply current opacity from config if present
    opacity = float(CONFIG_OBJ.get("opacity", 1.0))
    opacity = max(0.3, min(opacity, 1.0))
    pet.setWindowOpacity(opacity)

    pet.show()
    pet.raise_()
    PETS.append(pet)


def remove_pet(pet):
    """Remove a pet instance (but never remove the last one)."""
    global PETS

    if pet not in PETS:
        return
    if len(PETS) <= 1:
        return

    PETS.remove(pet)
    pet.close()


def open_selector_for(pet):
    """Focus the selector window and make it control this pet."""
    global selector_window, CONFIG_OBJ, PETS

    # If somehow selector_window doesn't exist, create it safely
    if selector_window is None:
        cfg = CONFIG_OBJ or {}
        opacity = float(cfg.get("opacity", 1.0))
        opacity = max(0.3, min(opacity, 1.0))
        initial_percent = int(opacity * 100)

        selector_window = GifSelectorWindow(cfg, GIF_CHOICES, PETS, initial_percent, current_pet=pet)

    else:
        selector_window.set_current_pet(pet)

    selector_window.show()
    selector_window.raise_()
    selector_window.activateWindow()


# === SYSTEM TRAY HELPERS ===
def show_selector():
    global selector_window
    if selector_window is not None:
        selector_window.show()
        selector_window.raise_()
        selector_window.activateWindow()


def hide_selector():
    global selector_window
    if selector_window is not None:
        selector_window.hide()


def create_tray(app):
    global tray_icon
    tray_icon = QSystemTrayIcon(app.style().standardIcon(QStyle.SP_ComputerIcon), app)

    menu = QMenu()
    show_action = menu.addAction("Show Anima Engine")
    hide_action = menu.addAction("Hide Anima Engine")
    menu.addSeparator()
    quit_action = menu.addAction("Quit Anima Engine")

    show_action.triggered.connect(show_selector)
    hide_action.triggered.connect(hide_selector)
    quit_action.triggered.connect(app.quit)

    tray_icon.setContextMenu(menu)
    tray_icon.setToolTip("Anima Engine")
    tray_icon.show()


# === MAIN ===
def main():
    global PETS, selector_window, CONFIG_OBJ, tray_icon

    app = QApplication(sys.argv)
    # Don't quit automatically when windows close; we manage it via tray
    app.setQuitOnLastWindowClosed(False)

    # Load config
    config = load_config()
    CONFIG_OBJ = config

    # Decide starting GIF
    start_gif = config.get("last_gif", DEFAULT_GIF)
    if not os.path.exists(start_gif):
        start_gif = DEFAULT_GIF
    config["last_gif"] = start_gif

    # Decide starting opacity
    target_opacity = float(config.get("opacity", 1.0))
    target_opacity = max(0.3, min(target_opacity, 1.0))  # clamp
    initial_opacity_percent = int(target_opacity * 100)

    # === Create the first (main) pet ===
    main_pet = DraggableLabel(start_gif, idle_gif_path=IDLE_GIF, config=config, save_position=True)
    apply_window_flags(main_pet)

    # Position main pet
    screen = QDesktopWidget().availableGeometry()
    if "pos" in config and isinstance(config["pos"], list) and len(config["pos"]) == 2:
        x, y = config["pos"]
        x = max(0, min(x, screen.width() - PET_SIZE))
        y = max(0, min(y, screen.height() - PET_SIZE))
    else:
        x = 30
        y = screen.height() - PET_SIZE - 50
        config["pos"] = [x, y]
    main_pet.move(x, y)

    # Fade-in to target opacity
    def fade_in(step=0):
        max_steps = 20
        frac = min(step / max_steps, 1.0)
        opacity = target_opacity * frac
        main_pet.setWindowOpacity(opacity)
        if frac < 1.0:
            QTimer.singleShot(30, lambda: fade_in(step + 1))

    main_pet.setWindowOpacity(0.0)
    fade_in()
    main_pet.show()
    main_pet.raise_()

    PETS.append(main_pet)

    # === Create the selector window at startup (targets main pet) ===
    selector = GifSelectorWindow(config, GIF_CHOICES, PETS, initial_opacity_percent, current_pet=main_pet)
    selector_window = selector
    selector.show()

    # === Create system tray icon ===
    create_tray(app)

    # Save config once at start
    save_config(config)

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()