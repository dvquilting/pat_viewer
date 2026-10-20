#include "qpv/main_window.hpp"

#include <QApplication>

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    QApplication::setApplicationName("Quilt Pattern Viewer");
    QApplication::setOrganizationName("OpenAI");
    qpv::MainWindow window;
    window.show();
    if (argc > 1) window.openInitialPath(QString::fromLocal8Bit(argv[1]));
    return app.exec();
}
