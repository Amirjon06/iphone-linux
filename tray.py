import sys
import subprocess
from PyQt6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PyQt6.QtGui import QIcon, QPixmap, QColor
from PyQt6.QtCore import QThread, pyqtSignal

def make_icon(color):
    px = QPixmap(32, 32)
    px.fill(QColor(color))
    return QIcon(px)

class MountThread(QThread):
    done = pyqtSignal(bool)

    def run(self):
        result = subprocess.run(['python3', 'mount_iphone.py'], capture_output=True)
        self.done.emit(result.returncode == 0)

app = QApplication(sys.argv)
app.setQuitOnLastWindowClosed(False)

tray = QSystemTrayIcon()
tray.setIcon(make_icon('gray'))
tray.setToolTip('iphonefs - Not connected')
tray.setVisible(True)

menu = QMenu()

status_action = menu.addAction('Status: Not mounted')
status_action.setEnabled(False)

menu.addSeparator()

def mount():
    status_action.setText('Status: Mounting...')
    tray.setIcon(make_icon('orange'))
    tray.showMessage('iphonefs', 'Mounting iPhone...', QSystemTrayIcon.MessageIcon.Information)
    subprocess.Popen(['python3', 'mount_iphone.py'])
    status_action.setText('Status: Mounted ✓')
    tray.setIcon(make_icon('green'))
    tray.setToolTip('iphonefs - iPhone mounted at ~/iPhone')
    tray.showMessage('iphonefs', 'iPhone mounted at ~/iPhone', QSystemTrayIcon.MessageIcon.Information)

def unmount():
    subprocess.run(['fusermount', '-u', '/home/' + __import__('os').getenv('USER') + '/iPhone'])
    status_action.setText('Status: Not mounted')
    tray.setIcon(make_icon('gray'))
    tray.setToolTip('iphonefs - Not connected')
    tray.showMessage('iphonefs', 'iPhone unmounted', QSystemTrayIcon.MessageIcon.Information)

mount_action = menu.addAction('Mount iPhone')
mount_action.triggered.connect(mount)

unmount_action = menu.addAction('Unmount iPhone')
unmount_action.triggered.connect(unmount)

menu.addSeparator()

quit_action = menu.addAction('Quit')
quit_action.triggered.connect(app.quit)

tray.setContextMenu(menu)

sys.exit(app.exec())
