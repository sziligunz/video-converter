import PyQt6.QtWidgets as Widget
import PyQt6.QtCore as Core
import sys


# Main window class (start screen)
class MainWindow(Widget.QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Video Converter Tool")
        self.setMinimumSize(500, 300)

        vbox = Widget.QVBoxLayout()
        vbox.setAlignment(Core.Qt.AlignmentFlag.AlignTop | Core.Qt.AlignmentFlag.AlignLeft)

        title = Widget.QLabel("Select one or more files to convert to .mp4")
        vbox.addWidget(title)
        browse_button = Widget.QPushButton("Browse")
        browse_button.setFixedWidth(150)
        browse_button.clicked.connect(lambda: self.browse())
        vbox.addWidget(browse_button)

        mainWidget = Widget.QWidget()
        mainWidget.setLayout(vbox)
        self.setCentralWidget(mainWidget)
    
    def browse(self):
        self.pop_up = FileBrowser()
        self.pop_up.browser.accepted.connect(self.get_selected_files)
        self.pop_up.exec()
    
    def get_selected_files(self):
        print(self.pop_up.browser.selectedFiles())


# File Browser pop up
class FileBrowser(Widget.QDialog):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Select Files")
        self.setMinimumSize(600, 400)

        vbox = Widget.QVBoxLayout()
        vbox.setAlignment(Core.Qt.AlignmentFlag.AlignCenter)
        self.browser = Widget.QFileDialog()
        #browser.setNameFilter("MKV Files (*.mkv)")
        self.browser.setFileMode(Widget.QFileDialog.FileMode.ExistingFiles)
        self.browser.accepted.connect(lambda: self.accept())
        vbox.addWidget(self.browser)
        widget = Widget.QWidget()
        widget.setLayout(vbox)
        self.setLayout(vbox)


# Run application
app = Widget.QApplication(sys.argv)
window = MainWindow()
window.show()
app.exec()
