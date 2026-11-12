"""Lightweight i18n: a single dict of {key: {"fr": ..., "en": ...}} and a
lookup function that picks the string for the detected locale.

Deliberately not using Qt's tr()/.ts/.qm toolchain (pyside6-lupdate,
lrelease) - that's the "correct" scalable approach for a large app with
many translators, but is overkill for the few dozen short strings here
and would add a separate build step. This keeps every string reviewable
as plain Python with no external tooling.

Only user-facing UI text goes through _() - log messages stay as
plain French for the developer reading them in the console, regardless
of the UI language (see main_window.py, pane_widget.py, etc.).
"""

from __future__ import annotations

import os

from PySide6.QtCore import QLocale


def _detect_language() -> str:
    # MTOOLS_GUI_LANG lets a user (or a test) force the language without
    # changing their whole system locale.
    override = os.environ.get("MTOOLS_GUI_LANG")
    if override:
        return "fr" if override.lower().startswith("fr") else "en"
    name = QLocale.system().name()  # e.g. "fr_FR", "en_US"
    return "fr" if name.lower().startswith("fr") else "en"


LANGUAGE = _detect_language()

_STRINGS: dict[str, dict[str, str]] = {
    "window_title": {
        "fr": "mtools_gui - échange Linux / lecteur DOS",
        "en": "mtools_gui - exchange files between Linux and a DOS drive",
    },
    "status_ready": {"fr": "Prêt", "en": "Ready"},
    "toolbar_refresh": {"fr": "Rafraîchir (F5)", "en": "Refresh (F5)"},
    "toolbar_copy": {"fr": "Copier (Ctrl+C)", "en": "Copy (Ctrl+C)"},
    "toolbar_move": {"fr": "Déplacer", "en": "Move"},
    "toolbar_move_tooltip": {
        "fr": "Déplacer (ou Alt+glisser-déposer)",
        "en": "Move (or Alt+drag-and-drop)",
    },
    "toolbar_rename": {"fr": "Renommer (F2)", "en": "Rename (F2)"},
    "toolbar_delete": {"fr": "Supprimer (Suppr)", "en": "Delete (Del)"},
    "toolbar_mkdir": {"fr": "Nouveau dossier (Ctrl+N)", "en": "New folder (Ctrl+N)"},
    "toolbar_about": {"fr": "À propos", "en": "About"},
    "drive_label": {"fr": "Lecteur DOS :", "en": "DOS drive:"},
    "drive_refresh_button": {"fr": "Rafraîchir lecteurs", "en": "Refresh drives"},
    "drive_placeholder": {"fr": "(choisir un lecteur)", "en": "(choose a drive)"},
    "drive_unknown_fstype": {
        "fr": "type inconnu - probablement DOS",
        "en": "unknown type - probably DOS",
    },
    "drive_no_label": {"fr": "(sans nom)", "en": "(no label)"},
    "image_open_button": {"fr": "Image disquette…", "en": "Disk image…"},
    "image_open_tooltip": {
        "fr": "Monter une image disquette (.ST Atari, .IMG, ...) dans le volet droit",
        "en": "Mount a floppy disk image (Atari .ST, .IMG, ...) in the right pane",
    },
    "image_unmount_button": {"fr": "Démonter l'image", "en": "Unmount image"},
    "image_dialog_title": {"fr": "Choisir une image disquette", "en": "Choose a disk image"},
    "image_dialog_filter": {
        "fr": "Images disquette (*.st *.ST *.img *.IMG *.ima *.IMA *.dsk *.DSK *.vfd *.VFD);;Tous les fichiers (*)",
        "en": "Disk images (*.st *.ST *.img *.IMG *.ima *.IMA *.dsk *.DSK *.vfd *.VFD);;All files (*)",
    },
    "image_mounted_label": {"fr": "Image : {name}", "en": "Image: {name}"},
    "image_mount_failed": {
        "fr": "Impossible de monter l'image {path} :\n\n{error}",
        "en": "Could not mount the image {path}:\n\n{error}",
    },
    "pane_up_button": {"fr": "Remonter", "en": "Up"},
    "column_name": {"fr": "Nom", "en": "Name"},
    "column_size": {"fr": "Taille", "en": "Size"},
    "column_modified": {"fr": "Modifié", "en": "Modified"},
    "size_bytes": {"fr": "o", "en": "B"},
    "size_kb": {"fr": "Ko", "en": "KB"},
    "size_mb": {"fr": "Mo", "en": "MB"},
    "size_gb": {"fr": "Go", "en": "GB"},
    "confirm_delete_title": {"fr": "Confirmer la suppression", "en": "Confirm delete"},
    "confirm_delete_text": {"fr": "Supprimer : {names} ?", "en": "Delete: {names}?"},
    "names_truncated": {
        "fr": "{shown}, ... et {count} de plus",
        "en": "{shown}, ... and {count} more",
    },
    "side_left": {"fr": "gauche", "en": "left"},
    "side_right": {"fr": "droite", "en": "right"},
    "confirm_move_title": {"fr": "Confirmer le déplacement", "en": "Confirm move"},
    "confirm_move_text": {
        "fr": "Déplacer vers {side} : {names} ?",
        "en": "Move to {side}: {names}?",
    },
    "confirm_overwrite_title": {"fr": "Confirmer le remplacement", "en": "Confirm overwrite"},
    "confirm_overwrite_text": {
        "fr": (
            "Existe déjà à destination et sera remplacé : {names}\n\n"
            "(le contenu actuel sera supprimé avant la copie)"
        ),
        "en": (
            "Already exists at the destination and will be replaced: {names}\n\n"
            "(its current contents will be deleted before copying)"
        ),
    },
    "status_choose_drive": {
        "fr": "Choisissez d'abord un lecteur DOS ou une image disquette",
        "en": "Choose a DOS drive or a disk image first",
    },
    "status_select_one_to_rename": {
        "fr": "Sélectionnez un seul élément à renommer",
        "en": "Select exactly one item to rename",
    },
    "status_op_done": {"fr": "{description} : terminé", "en": "{description}: done"},
    "status_in_progress_one": {
        "fr": "{description} en cours...",
        "en": "{description} in progress...",
    },
    "status_in_progress_many": {
        "fr": "{count} opérations en cours...",
        "en": "{count} operations in progress...",
    },
    "op_copy": {"fr": "Copier {name}", "en": "Copy {name}"},
    "op_copy_many": {"fr": "Copier {count} éléments", "en": "Copy {count} items"},
    "op_move": {"fr": "Déplacer {name}", "en": "Move {name}"},
    "op_move_many": {"fr": "Déplacer {count} éléments", "en": "Move {count} items"},
    "op_delete": {"fr": "Supprimer {name}", "en": "Delete {name}"},
    "op_delete_many": {"fr": "Supprimer {count} éléments", "en": "Delete {count} items"},
    "op_mkdir": {"fr": "Nouveau dossier {name}", "en": "New folder {name}"},
    "op_rename": {"fr": "Renommer {name}", "en": "Rename {name}"},
    "dialog_new_folder_title": {"fr": "Nouveau dossier", "en": "New folder"},
    "dialog_new_folder_label": {"fr": "Nom du dossier :", "en": "Folder name:"},
    "dialog_rename_title": {"fr": "Renommer", "en": "Rename"},
    "dialog_rename_label": {"fr": "Nouveau nom :", "en": "New name:"},
    "dialog_error_title": {"fr": "Erreur", "en": "Error"},
    "err_mtools_not_found": {
        "fr": "Commande {op} introuvable : mtools est-il installé ?",
        "en": "{op} command not found: is mtools installed?",
    },
    "dialog_install_required_title": {
        "fr": "Installation requise pour accéder aux périphériques amovibles",
        "en": "Installation required to access removable drives",
    },
    "dialog_install_required_text": {
        "fr": (
            "Pour lire et écrire sur une carte SD ou une clé USB, "
            "mtools_gui a besoin d'un petit programme complémentaire "
            "exécuté avec les droits root (il ne fait rien d'autre : il "
            "sert uniquement à lancer les commandes mtools sur le "
            "périphérique que vous choisissez). Ce programme n'est pas "
            "encore installé.\n\n"
            "Cliquez sur \"Installer maintenant\" (demandera votre mot de "
            "passe), ou lancez une fois manuellement dans un terminal :\n"
            "  {command}\n\n"
            "Le volet gauche (fichiers locaux) fonctionne déjà sans cela."
        ),
        "en": (
            "To read and write to an SD card or USB key, mtools_gui "
            "needs a small companion program run with root privileges "
            "(it does nothing else - it only runs mtools commands "
            "against the removable drive you pick). This program isn't "
            "installed yet.\n\n"
            "Click \"Install now\" (will ask for your password), or run "
            "this once manually in a terminal:\n"
            "  {command}\n\n"
            "The left pane (local files) already works without it."
        ),
    },
    "install_now_button": {"fr": "Installer maintenant", "en": "Install now"},
    "install_later_button": {"fr": "Plus tard", "en": "Later"},
    "install_success_title": {"fr": "Installation réussie", "en": "Installation succeeded"},
    "install_success_text": {
        "fr": "Le helper privilégié est maintenant installé.",
        "en": "The privileged helper is now installed.",
    },
    "install_auth_cancelled": {
        "fr": "Installation annulée (authentification refusée).",
        "en": "Installation cancelled (authentication refused).",
    },
    "install_failed_text": {
        "fr": "Échec de l'installation : {error}",
        "en": "Installation failed: {error}",
    },
    "about_title": {"fr": "À propos de mtools_gui", "en": "About mtools_gui"},
    "about_text": {
        "fr": (
            "<b>mtools_gui</b><br>"
            "Version {version}<br>"
            "Auteur : Stéphane Perez<br><br>"
            "Gestionnaire de fichiers à deux volets pour échanger des "
            "fichiers entre Linux et une partition DOS sur un lecteur "
            "amovible, via mtools."
        ),
        "en": (
            "<b>mtools_gui</b><br>"
            "Version {version}<br>"
            "Author: Stéphane Perez<br><br>"
            "Two-pane file manager for exchanging files between Linux "
            "and a DOS partition on a removable drive, via mtools."
        ),
    },
    "err_helper_not_installed": {
        "fr": "Le helper privilégié n'est pas installé. Lancez une fois : {command}",
        "en": "The privileged helper isn't installed. Run this once: {command}",
    },
    "err_auth_dismissed": {
        "fr": "Authentification refusée ou annulée.",
        "en": "Authentication refused or dismissed.",
    },
    "err_validation_default": {
        "fr": "Opération rejetée par le helper.",
        "en": "Operation rejected by the helper.",
    },
    "err_usage_default": {"fr": "Erreur d'usage du helper.", "en": "Helper usage error."},
    "err_command_default": {
        "fr": "Échec de la commande {op}.",
        "en": "Command {op} failed.",
    },
    "err_pkexec_not_found": {
        "fr": "pkexec introuvable sur ce système.",
        "en": "pkexec not found on this system.",
    },
    "err_helper_timeout": {
        "fr": "{op} n'a pas répondu après {timeout}s (périphérique bloqué ou déconnecté ?).",
        "en": "{op} did not respond after {timeout}s (is the device stuck or disconnected?).",
    },
    "op_unexpected_error": {
        "fr": "Erreur inattendue : {error}",
        "en": "Unexpected error: {error}",
    },
    "op_failed_combined": {"fr": "{description} : {msg}", "en": "{description}: {msg}"},
    "err_unknown_pane_combination": {
        "fr": "Combinaison de volets inconnue",
        "en": "Unknown pane combination",
    },
    "err_unknown_pane_combination_move": {
        "fr": "Combinaison de volets inconnue pour un déplacement croisé",
        "en": "Unknown pane combination for a cross-side move",
    },
    "move_copied_but_source_delete_failed": {
        "fr": (
            "{name} a été copié, mais l'original n'a pas pu être supprimé "
            "de la source : {error}. Les deux copies existent maintenant."
        ),
        "en": (
            "{name} was copied, but the original could not be removed "
            "from the source: {error}. Both copies now exist."
        ),
    },
    "move_copied_but_source_delete_failed_many": {
        "fr": (
            "{count} éléments ont été copiés, mais les originaux n'ont pas pu "
            "être supprimés de la source : {error}. Les deux copies existent "
            "maintenant."
        ),
        "en": (
            "{count} items were copied, but the originals could not be "
            "removed from the source: {error}. Both copies now exist."
        ),
    },
    "view_dialog_read_error": {
        "fr": "Lecture de {name} : {error}",
        "en": "Reading {name}: {error}",
    },
    "install_need_root": {
        "fr": (
            "Ce programme doit être exécuté en root, par exemple :\n"
            '  sudo "$(command -v mtools-gui-install-helper)"'
        ),
        "en": (
            "This program must be run as root, e.g.:\n"
            '  sudo "$(command -v mtools-gui-install-helper)"'
        ),
    },
    "install_installed": {"fr": "Installé : {path}", "en": "Installed: {path}"},
    "install_removed": {"fr": "Supprimé : {path}", "en": "Removed: {path}"},
    "install_already_absent": {"fr": "Déjà absent : {path}", "en": "Already absent: {path}"},
    "install_verify_hint": {
        "fr": (
            "\nVérification recommandée (depuis un terminal de la session active) :\n"
            "  pkexec {helper_path} mdir /dev/sdX1 ::\n"
            "ne devrait demander aucun mot de passe."
        ),
        "en": (
            "\nRecommended check (from a terminal in the active session):\n"
            "  pkexec {helper_path} mdir /dev/sdX1 ::\n"
            "should not ask for a password."
        ),
    },
}


def _(key: str, **kwargs) -> str:
    entry = _STRINGS.get(key)
    if entry is None:
        return key
    text = entry.get(LANGUAGE) or entry.get("fr") or key
    return text.format(**kwargs) if kwargs else text
