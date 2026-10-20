#pragma once

#include "qpv/pattern.hpp"

#include <QColor>
#include <QHash>
#include <QImage>
#include <QMainWindow>
#include <QSet>
#include <QStringList>
#include <QVariantMap>

class QCheckBox;
class QLabel;
class QLineEdit;
class QListWidget;
class QListWidgetItem;
class QPlainTextEdit;
class QSlider;
class QStackedWidget;
class QThreadPool;
class QTimer;
class QResizeEvent;

namespace qpv {

class PatternCanvas;

class MainWindow final : public QMainWindow {
public:
    explicit MainWindow(QWidget* parent = nullptr);
    ~MainWindow() override;
    void openInitialPath(const QString& path);
    static QImage renderThumbnail(const QString& path, const QColor& color);

protected:
    void resizeEvent(QResizeEvent* event) override;

private:
    PatternCanvas* canvas_{};
    QListWidget* files_{};
    QListWidget* thumbnails_{};
    QListWidget* keyword_grid_{};
    QStackedWidget* view_stack_{};
    QLineEdit* search_{};
    QCheckBox* search_names_{};
    QCheckBox* search_keywords_{};
    QLabel* file_name_{};
    QLabel* file_path_{};
    QLabel* dimensions_{};
    QLabel* statistics_{};
    QPlainTextEdit* comments_{};
    QLineEdit* keyword_edit_{};
    QLineEdit* bulk_keyword_edit_{};
    QLabel* list_status_{};
    QStringList library_;
    QVariantMap keywords_;
    QStringList filtered_;
    QString current_;
    int view_mode_{};
    QColor line_color_{Qt::black};
    QThreadPool* thumbnail_pool_{};
    QTimer* search_timer_{};
    QTimer* thumbnail_timer_{};
    QTimer* background_thumbnail_timer_{};
    int background_thumbnail_cursor_{};
    int thumbnail_worker_count_{};
    int max_background_thumbnails_{};
    quint64 thumbnail_generation_{};
    QSet<QString> thumbnail_inflight_;
    QSet<QString> background_thumbnail_inflight_;
    QSet<QString> thumbnail_loaded_;
    QHash<QString, QListWidgetItem*> thumbnail_items_;
    QHash<QString, QListWidgetItem*> keyword_items_;

    void buildUi();
    void buildMenus();
    void loadLibrary();
    void saveLibrary() const;
    void refreshList();
    void populateView();
    void setViewMode(int mode);
    void invalidateThumbnails();
    void scheduleVisibleThumbnails();
    void scheduleVisibleThumbnails(QListWidget* target);
    void scheduleNextBackgroundThumbnail();
    void requestThumbnail(const QString& path, QListWidget* target,
                          int priority, bool background = false);
    void applyThumbnail(const QString& path, const QImage& image, QListWidget* target);
    QString thumbnailPath(const QString& path) const;
    void addFiles();
    void addFolder();
    void addPath(const QString& path);
    void openFile(const QString& path);
    void removeSelected();
    void purgeMissing();
    void resetDatabase();
    void clearThumbnailCache();
    void mergeDuplicates();
    void saveCurrentKeywords();
    void applyBulkKeywords();
    void showKeywordsList();
    void convertCurrent(const QString& extension);
    void convertSourceDialog(const QString& sourceExtension, const QString& outputExtension);
    void convertFolder(bool recursive);
    void chooseLineColor();
    void showHelp();
    void showAbout();
    static QString normalizeKeywords(const QString& value);
};

} // namespace qpv
