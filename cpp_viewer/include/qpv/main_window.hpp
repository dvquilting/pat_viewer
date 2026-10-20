#pragma once

#include "qpv/pattern.hpp"

#include <QColor>
#include <QHash>
#include <QIcon>
#include <QImage>
#include <QMainWindow>
#include <QSet>
#include <QStringList>
#include <QVariantMap>

#include <atomic>
#include <functional>
#include <memory>
#include <vector>

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

class BackgroundJob;
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
    // Case-folded library paths, for constant-time duplicate checks.
    QSet<QString> library_keys_;
    QVariantMap keywords_;
    // Every library path in display order; the active list has one item per
    // entry, and searching hides rows instead of rebuilding the list.
    QStringList rows_;
    QStringList row_names_;
    QHash<QString, int> row_of_;
    std::vector<char> row_hidden_;
    std::vector<int> shown_rows_;      // rows that match the search, in order
    std::vector<int> shown_position_;  // row -> index into shown_rows_, or -1
    QString current_;
    // The one background job allowed at a time (see runInBackground).
    std::shared_ptr<std::atomic<bool>> job_cancel_;
    bool job_running_{};
    QTimer* save_timer_{};
    bool library_dirty_{};
    int view_mode_{};
    QColor line_color_{Qt::black};
    QThreadPool* thumbnail_pool_{};
    QTimer* search_timer_{};
    QTimer* thumbnail_timer_{};
    QTimer* background_thumbnail_timer_{};
    int background_thumbnail_cursor_{};
    int background_batches_inflight_{};
    int thumbnail_worker_count_{};
    int max_background_thumbnails_{};
    quint64 thumbnail_generation_{};
    // Shared with worker tasks so queued background work stops after invalidation.
    std::shared_ptr<std::atomic<quint64>> live_generation_{
        std::make_shared<std::atomic<quint64>>(0)};
    QString thumbnail_cache_dir_;
    QIcon placeholder_icon_;
    QSet<QString> thumbnail_inflight_;
    QSet<QString> thumbnail_loaded_;
    QSet<QString> thumbnail_failed_;
    // Rows whose icons stay in memory; icons outside this window are released.
    int keep_first_row_{};
    int keep_last_row_{-1};

    void buildUi();
    void buildMenus();
    void loadLibrary();
    void saveLibrary();
    bool flushLibrary();
    [[nodiscard]] static QString libraryFilePath();
    void runInBackground(const QString& title, const QString& label,
                         std::function<void(BackgroundJob&)> work,
                         std::function<void(bool cancelled)> finish);
    void rebuildLibraryKeys();
    int addCanonicalPaths(const QStringList& paths);
    void refreshList();
    void populateView();
    void applyFilter();
    QListWidget* activeList() const;
    QListWidgetItem* itemForPath(const QString& path) const;
    QString itemToolTip(const QString& path) const;
    void selectAllVisible();
    void setViewMode(int mode);
    void invalidateThumbnails();
    void scheduleVisibleThumbnails();
    void scheduleVisibleThumbnails(QListWidget* target);
    void scheduleNextBackgroundThumbnail();
    void requestThumbnail(const QString& path, QListWidget* target, int priority);
    void evictThumbnails(QListWidget* target);
    void applyThumbnail(const QString& path, const QImage& image, QListWidget* target);
    void addFiles();
    void addFolder();
    void addPath(const QString& path);
    void scanFolder(const QString& folder);
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
    // `existing` is how many outputs already exist, or -1 to check here.
    void convertFiles(const QList<QPair<QString, QString>>& sourcesAndOutputs, int existing = -1);
    void chooseLineColor();
    void showHelp();
    void showAbout();
    static QString normalizeKeywords(const QString& value);
};

} // namespace qpv
