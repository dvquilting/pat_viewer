#include "qpv/main_window.hpp"

#include <QAction>
#include <QApplication>
#include <QDir>
#include <QElapsedTimer>
#include <QFile>
#include <QFileInfo>
#include <QSettings>
#include <QStandardPaths>
#include <QTemporaryDir>
#include <QThread>

#include <iostream>
#include <stdexcept>

namespace {

void check(bool value, const char* message) {
    if (!value) throw std::runtime_error(message);
}

int thumbnailCount() {
    const QDir cache(QStandardPaths::writableLocation(QStandardPaths::CacheLocation)
                     + "/thumbnails");
    return cache.entryList({"*.png"}, QDir::Files).size();
}

} // namespace

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    QApplication::setApplicationName("QuiltPatternViewer-ThumbnailTest");
    QApplication::setOrganizationName("OpenAI-Tests");
    QStandardPaths::setTestModeEnabled(true);

    try {
        const QStringList arguments = app.arguments();
        if (arguments.size() == 4 && arguments[1] == "--benchmark") {
            const QString path = arguments[2];
            const int iterations = arguments[3].toInt();
            check(iterations > 0 && QFileInfo::exists(path),
                  "Benchmark requires an existing pattern and positive iteration count");

            QElapsedTimer renderTimer;
            renderTimer.start();
            QImage image;
            for (int i = 0; i < iterations; ++i)
                image = qpv::MainWindow::renderThumbnail(path, Qt::black);
            const qint64 renderMs = renderTimer.elapsed();
            check(!image.isNull(), "Benchmark thumbnail rendering failed");

            for (const int quality : {-1, 80, 100}) {
                QTemporaryDir output;
                check(output.isValid(), "Could not create benchmark output directory");
                QElapsedTimer encodeTimer;
                encodeTimer.start();
                qint64 totalBytes = 0;
                for (int i = 0; i < iterations; ++i) {
                    const QString file = output.filePath(QString::number(i) + ".png");
                    check(image.save(file, "PNG", quality), "PNG benchmark write failed");
                    totalBytes += QFileInfo(file).size();
                }
                std::cout << "PNG quality " << quality << ": "
                          << encodeTimer.elapsed() << " ms, "
                          << totalBytes / iterations << " bytes/file\n";
            }
            std::cout << "Render: " << renderMs << " ms total, "
                      << static_cast<double>(renderMs) / iterations
                      << " ms/thumbnail\n";
            return 0;
        }

        std::cerr << "Preparing isolated thumbnail cache\n";
        QTemporaryDir state;
        check(state.isValid(), "Could not create temporary settings directory");
        QSettings::setDefaultFormat(QSettings::IniFormat);
        QSettings::setPath(QSettings::IniFormat, QSettings::UserScope, state.path());
        QSettings settings("OpenAI", "QuiltPatternViewer");
        settings.clear();

        const QString cacheRoot =
            QStandardPaths::writableLocation(QStandardPaths::CacheLocation);
        check(!cacheRoot.isEmpty(), "No writable thumbnail test cache");
        QDir cache(cacheRoot);
        check(!cache.exists() || cache.removeRecursively(),
              "Could not clear the isolated thumbnail test cache");

        QTemporaryDir patterns;
        check(patterns.isValid(), "Could not create temporary pattern directory");
        const QByteArray pattern =
            "N10G00X0.0000Y0.0000\r\n"
            "N11G01X2.0000Y0.0000\r\n"
            "N12G01X2.0000Y2.0000\r\n"
            "N13G01X0.0000Y2.0000\r\n"
            "N14G01X0.0000Y0.0000\r\n"
            "N15M02\r\n";
        for (int i = 0; i < 110; ++i) {
            QFile file(patterns.filePath(
                QString("Pattern %1.pat").arg(i, 3, 10, QLatin1Char('0'))));
            check(file.open(QIODevice::WriteOnly), "Could not create test PAT file");
            check(file.write(pattern) == pattern.size(), "Could not write test PAT file");
        }

        qpv::MainWindow window;
        std::cerr << "Opening 110-pattern library\n";
        window.show();
        app.processEvents();
        window.openInitialPath(patterns.path());
        app.processEvents();

        bool triggered = false;
        for (QAction* action : window.findChildren<QAction*>()) {
            if (action->text().contains("Thumbnail Mode")) {
                action->trigger();
                triggered = true;
                break;
            }
        }
        check(triggered, "Thumbnail Mode action was not found");
        QElapsedTimer settle;
        settle.start();
        while (settle.elapsed() < 100) {
            app.processEvents(QEventLoop::AllEvents, 20);
            QThread::msleep(2);
        }
        std::cerr << "Thumbnail mode triggered\n";

        QElapsedTimer elapsed;
        elapsed.start();
        while (thumbnailCount() < 110 && elapsed.elapsed() < 12000) {
            app.processEvents(QEventLoop::AllEvents, 50);
            QThread::msleep(10);
        }

        const int generated = thumbnailCount();
        std::cerr << "Observed " << generated << " cached thumbnails\n";
        check(generated == 110,
              "Thumbnail generation did not finish the 110-pattern test library");
        const QDir generatedCache(
            QStandardPaths::writableLocation(QStandardPaths::CacheLocation)
            + "/thumbnails");
        for (const QString& file : generatedCache.entryList({"*.png"}, QDir::Files))
            check(!QImage(generatedCache.filePath(file)).isNull(),
                  "Generated thumbnail PNG could not be decoded");
        std::cout << "Generated " << generated
                  << " thumbnails in " << elapsed.elapsed() << " ms\n";
        window.close();
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "Thumbnail test failure: " << e.what() << '\n';
        return 1;
    }
}
