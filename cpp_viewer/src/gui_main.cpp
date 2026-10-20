#include "qpv/main_window.hpp"

#include <QApplication>

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    QApplication::setApplicationName("Quilt Pattern Viewer");
    QApplication::setOrganizationName("dvquilting");
    qpv::MainWindow window;
    window.show();
    // arguments() decodes the command line properly on every platform; argv on
    // Windows is limited to the ANSI code page.
    const QStringList arguments = QApplication::arguments();
    if (arguments.size() > 1) window.openInitialPath(arguments.at(1));
    return app.exec();
}
