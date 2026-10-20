#include "qpv/main_window.hpp"

#include "qpv/io.hpp"
#include "qpv/pattern_canvas.hpp"

#include <QActionGroup>
#include <QApplication>
#include <QBuffer>
#include <QCheckBox>
#include <QColorDialog>
#include <QCryptographicHash>
#include <QDialog>
#include <QDialogButtonBox>
#include <QDir>
#include <QDirIterator>
#include <QElapsedTimer>
#include <QFile>
#include <QFileDialog>
#include <QFileInfo>
#include <QFrame>
#include <QGroupBox>
#include <QHBoxLayout>
#include <QInputDialog>
#include <QItemSelection>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLabel>
#include <QLineEdit>
#include <QListWidget>
#include <QMenuBar>
#include <QMessageBox>
#include <QMetaObject>
#include <QPainter>
#include <QPainterPath>
#include <QPlainTextEdit>
#include <QPointer>
#include <QCloseEvent>
#include <QProgressBar>
#include <QPushButton>
#include <QRunnable>
#include <QSaveFile>
#include <QScrollBar>
#include <QSettings>
#include <QSignalBlocker>
#include <QSlider>
#include <QSplitter>
#include <QStackedWidget>
#include <QStandardPaths>
#include <QStatusBar>
#include <QTextBrowser>
#include <QThread>
#include <QThreadPool>
#include <QTimer>
#include <QVBoxLayout>

#include <algorithm>
#include <functional>
#include <optional>
#include <unordered_map>

namespace qpv {
namespace {

constexpr int thumbnail_size = 120;
constexpr int background_thumbnail_delay_ms = 25;
constexpr char settings_organization[] = "dvquilting";
constexpr char settings_application[] = "QuiltPatternViewer";
// The organisation name used by earlier versions, whose data is moved once.
constexpr char legacy_organization[] = "OpenAI";

// Where a standard location was under the earlier organisation name.
QString legacyLocation(QStandardPaths::StandardLocation location) {
    const QString current = QCoreApplication::organizationName();
    QCoreApplication::setOrganizationName(legacy_organization);
    const QString path = QStandardPaths::writableLocation(location);
    QCoreApplication::setOrganizationName(current);
    return path;
}

// QString::toStdString() is UTF-8, but a narrow std::filesystem::path is read
// in the ANSI code page on Windows (MSVC), which mangles other names.
std::filesystem::path toPath(const QString& path) {
#ifdef Q_OS_WIN
    return std::filesystem::path(path.toStdWString());
#else
    return std::filesystem::path(QFile::encodeName(path).toStdString());
#endif
}

QString thumbnailCacheDirectory() {
    QString root = QStandardPaths::writableLocation(QStandardPaths::CacheLocation);
    if (root.isEmpty()) root = QDir::tempPath() + "/QuiltPatternViewer";
    return root + "/thumbnails";
}

using Generation = std::shared_ptr<std::atomic<quint64>>;

// Thread-safe: runs on thumbnail workers.
QString thumbnailCachePath(const QString& cacheDir, const QString& path, const QColor& color) {
    const QFileInfo info(path);
    const QByteArray key = ("thumb-v2-thin|" + path + '|' +
        QString::number(info.lastModified().toMSecsSinceEpoch()) +
        '|' + QString::number(info.size()) + '|' + color.name()).toUtf8();
    const auto digest = QCryptographicHash::hash(key, QCryptographicHash::Sha256).toHex();
    return cacheDir + '/' + QString::fromLatin1(digest) + ".png";
}

// Writes via QSaveFile so a reader never sees a half-written file.
bool writeFileAtomically(const QString& path, const QByteArray& data) {
    // On Windows the rename in commit() fails now and then with a sharing
    // violation while antivirus or the indexer briefly opens the new file.
    for (int attempt = 0; attempt < 5; ++attempt) {
        if (attempt) QThread::msleep(10u << attempt);
        QSaveFile file(path);
        if (file.open(QIODevice::WriteOnly) && file.write(data) == data.size() && file.commit())
            return true;
    }
    return false;
}

void saveThumbnail(const QImage& image, const QString& cachePath) {
    QByteArray png;
    QBuffer buffer(&png);
    buffer.open(QIODevice::WriteOnly);
    // Quality 80 selects a faster PNG compression level while remaining
    // lossless. It is also smaller than Qt's default for these line images.
    if (image.save(&buffer, "PNG", 80)) writeFileAtomically(cachePath, png);
}

template <typename Result>
void deliver(const QPointer<QObject>& context,
             const std::function<void(const Result&)>& done, Result result) {
    if (context) QMetaObject::invokeMethod(context, [context, done, result = std::move(result)] {
        if (context) done(result);
    }, Qt::QueuedConnection);
}

// Produces one thumbnail for display, from the disk cache when possible.
// The cache lookup and PNG decode happen here rather than on the GUI thread.
class ThumbnailLoadTask final : public QRunnable {
public:
    ThumbnailLoadTask(QString path, QString cacheDir, QColor color, quint64 generation,
                      Generation live, QPointer<QObject> context,
                      std::function<void(const QImage&)> done)
        : path_(std::move(path)), cache_dir_(std::move(cacheDir)), color_(color),
          generation_(generation), live_(std::move(live)), context_(context),
          done_(std::move(done)) {
        setAutoDelete(true);
    }
    void run() override {
        if (live_->load() != generation_) return;
        const QString cached = thumbnailCachePath(cache_dir_, path_, color_);
        QImage image(cached);
        if (image.isNull()) {
            image = MainWindow::renderThumbnail(path_, color_);
            if (!image.isNull()) saveThumbnail(image, cached);
        }
        deliver(context_, done_, image);
    }
private:
    QString path_;
    QString cache_dir_;
    QColor color_;
    quint64 generation_;
    Generation live_;
    QPointer<QObject> context_;
    std::function<void(const QImage&)> done_;
};

// Fills the disk cache for a batch of files without decoding anything for
// display, and reports the files that could not be rendered.
class ThumbnailWarmTask final : public QRunnable {
public:
    ThumbnailWarmTask(QStringList paths, QString cacheDir, QColor color, quint64 generation,
                      Generation live, QPointer<QObject> context,
                      std::function<void(const QStringList&)> done)
        : paths_(std::move(paths)), cache_dir_(std::move(cacheDir)), color_(color),
          generation_(generation), live_(std::move(live)), context_(context),
          done_(std::move(done)) {
        setAutoDelete(true);
    }
    void run() override {
        QStringList failed;
        for (const auto& path : paths_) {
            if (live_->load() != generation_) return;
            const QString cached = thumbnailCachePath(cache_dir_, path, color_);
            if (QFileInfo::exists(cached)) continue;
            const QImage image = MainWindow::renderThumbnail(path, color_);
            if (image.isNull()) failed << path;
            else saveThumbnail(image, cached);
        }
        deliver(context_, done_, failed);
    }
private:
    QStringList paths_;
    QString cache_dir_;
    QColor color_;
    quint64 generation_;
    Generation live_;
    QPointer<QObject> context_;
    std::function<void(const QStringList&)> done_;
};

QString fileDigest(const QString& path) {
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) return {};
    QCryptographicHash hash(QCryptographicHash::Sha256);
    while (!file.atEnd()) hash.addData(file.read(1024 * 1024));
    return QString::fromLatin1(hash.result().toHex());
}

// Progress window for background jobs. Unlike QProgressDialog, whose setValue()
// runs a nested event loop when modal, updating it never processes events, so a
// job's finish handler cannot run (and delete the dialog) in the middle of an
// update.
class JobDialog final : public QDialog {
public:
    JobDialog(const QString& title, const QString& label, std::function<void()> onCancel,
              QWidget* parent)
        : QDialog(parent), on_cancel_(std::move(onCancel)) {
        setWindowTitle(title);
        setWindowModality(Qt::WindowModal);
        auto* layout = new QVBoxLayout(this);
        label_ = new QLabel(label);
        label_->setMinimumWidth(360);
        bar_ = new QProgressBar;
        bar_->setRange(0, 0);
        cancel_ = new QPushButton(tr("Cancel"));
        layout->addWidget(label_);
        layout->addWidget(bar_);
        layout->addWidget(cancel_, 0, Qt::AlignRight);
        connect(cancel_, &QPushButton::clicked, this, [this] { requestCancel(); });
    }
    void setProgress(int done, int total, const QString& label) {
        if (cancelling_) return;
        bar_->setRange(0, total);
        bar_->setValue(done);
        label_->setText(label);
    }
protected:
    // Escape and the close button cancel the job; the dialog closes when it ends.
    void reject() override { requestCancel(); }
    void closeEvent(QCloseEvent* event) override {
        requestCancel();
        event->ignore();
    }
private:
    void requestCancel() {
        if (cancelling_) return;
        cancelling_ = true;
        cancel_->setEnabled(false);
        label_->setText(tr("Cancelling…"));
        on_cancel_();
    }
    std::function<void()> on_cancel_;
    QLabel* label_{};
    QProgressBar* bar_{};
    QPushButton* cancel_{};
    bool cancelling_{};
};

} // namespace

// Handed to the work function of MainWindow::runInBackground on its worker thread.
class BackgroundJob {
public:
    BackgroundJob(std::shared_ptr<std::atomic<bool>> cancel, QPointer<MainWindow> window,
                  QPointer<JobDialog> dialog)
        : cancel_(std::move(cancel)), window_(window), dialog_(dialog) {
        since_report_.start();
    }
    [[nodiscard]] bool cancelled() const { return cancel_->load(); }
    // Updates the progress window at most every 100 ms; total 0 shows a busy bar.
    void progress(int done, int total, const QString& label) {
        if (since_report_.elapsed() < 100 || !window_) return;
        since_report_.restart();
        QMetaObject::invokeMethod(window_, [dialog = dialog_, done, total, label] {
            if (dialog) dialog->setProgress(done, total, label);
        }, Qt::QueuedConnection);
    }
private:
    std::shared_ptr<std::atomic<bool>> cancel_;
    QPointer<MainWindow> window_;
    QPointer<JobDialog> dialog_;
    QElapsedTimer since_report_;
};

MainWindow::MainWindow(QWidget* parent) : QMainWindow(parent) {
    thumbnail_pool_ = new QThreadPool(this);
    const int idealThreads = std::max(4, QThread::idealThreadCount());
    thumbnail_worker_count_ = std::clamp(idealThreads - 2, 4, 8);
    max_background_thumbnails_ = std::clamp(thumbnail_worker_count_ / 2, 2, 3);
    thumbnail_pool_->setMaxThreadCount(thumbnail_worker_count_);
    thumbnail_pool_->setExpiryTimeout(5000);
    search_timer_ = new QTimer(this);
    search_timer_->setSingleShot(true);
    search_timer_->setInterval(250);
    thumbnail_timer_ = new QTimer(this);
    thumbnail_timer_->setSingleShot(true);
    thumbnail_timer_->setInterval(60);
    background_thumbnail_timer_ = new QTimer(this);
    background_thumbnail_timer_->setSingleShot(true);
    background_thumbnail_timer_->setInterval(background_thumbnail_delay_ms);
    // Edits come in bursts (typing keywords, bulk changes), so the library file
    // is written once things go quiet rather than after every change.
    save_timer_ = new QTimer(this);
    save_timer_->setSingleShot(true);
    save_timer_->setInterval(1000);
    connect(save_timer_, &QTimer::timeout, this, &MainWindow::flushLibrary);
    thumbnail_cache_dir_ = thumbnailCacheDirectory();
    // Reuse thumbnails cached under the earlier organisation name. If the move
    // fails they are simply rendered again.
    const QString legacyCacheRoot = legacyLocation(QStandardPaths::CacheLocation);
    const QString legacyCache = legacyCacheRoot + "/thumbnails";
    if (!legacyCacheRoot.isEmpty() && legacyCache != thumbnail_cache_dir_ &&
        QFileInfo::exists(legacyCache) && !QFileInfo::exists(thumbnail_cache_dir_)) {
        QDir().mkpath(QFileInfo(thumbnail_cache_dir_).absolutePath());
        QDir().rename(legacyCache, thumbnail_cache_dir_);
    }
    QDir().mkpath(thumbnail_cache_dir_);
    QPixmap blank(thumbnail_size, thumbnail_size);
    blank.fill(QColor(245, 245, 245));
    placeholder_icon_ = QIcon(blank);
    buildUi();
    buildMenus();
    loadLibrary();
    refreshList();
    resize(1260, 800);
    setWindowTitle(tr("Innova PAT / QLI Viewer"));
}

MainWindow::~MainWindow() {
    if (job_cancel_) job_cancel_->store(true);
    flushLibrary();
    invalidateThumbnails();
    thumbnail_pool_->waitForDone();
}

void MainWindow::buildUi() {
    auto* central = new QWidget(this);
    auto* root = new QVBoxLayout(central);
    root->setContentsMargins(6, 6, 6, 6);
    root->setSpacing(4);

    auto* controls = new QHBoxLayout;
    controls->setSpacing(8);
    auto* travel = new QCheckBox(tr("Show travel moves"));
    auto* markers = new QCheckBox(tr("Show markers"));
    auto* width = new QSlider(Qt::Horizontal);
    width->setRange(10, 400); width->setValue(100); width->setFixedWidth(100);
    controls->addWidget(travel);
    controls->addWidget(markers);
    controls->addSpacing(12);
    controls->addWidget(new QLabel(tr("Line width:")));
    controls->addWidget(width);
    controls->addStretch();
    statistics_ = new QLabel;
    controls->addWidget(statistics_);
    root->addLayout(controls);
    auto* separator = new QFrame;
    separator->setFrameShape(QFrame::HLine);
    separator->setFrameShadow(QFrame::Sunken);
    root->addWidget(separator);

    auto* split = new QSplitter(Qt::Horizontal);
    auto* left = new QWidget;
    auto* leftLayout = new QVBoxLayout(left);
    leftLayout->setContentsMargins(0, 0, 0, 0);
    leftLayout->setSpacing(2);
    search_ = new QLineEdit;
    search_->setPlaceholderText(tr("Search files…"));
    search_->setClearButtonEnabled(true);
    leftLayout->addWidget(search_);
    auto* scopes = new QHBoxLayout;
    scopes->addWidget(new QLabel(tr("Search by:")));
    search_names_ = new QCheckBox(tr("Filename"));
    search_keywords_ = new QCheckBox(tr("Keyword"));
    search_names_->setChecked(true);
    search_keywords_->setChecked(true);
    scopes->addWidget(search_names_);
    scopes->addWidget(search_keywords_);
    scopes->addStretch();
    leftLayout->addLayout(scopes);

    view_stack_ = new QStackedWidget;
    files_ = new QListWidget;
    files_->setMinimumWidth(220);
    files_->setUniformItemSizes(true);
    view_stack_->addWidget(files_);

    // Every cell is the same size, and laying out in batches keeps the window
    // responsive while thousands of items are positioned.
    auto configureIconView = [](QListWidget* list) {
        list->setViewMode(QListView::IconMode);
        list->setIconSize({thumbnail_size, thumbnail_size});
        list->setGridSize({150, 160});
        list->setResizeMode(QListView::Adjust);
        list->setMovement(QListView::Static);
        list->setUniformItemSizes(true);
        list->setLayoutMode(QListView::Batched);
        list->setBatchSize(200);
    };
    thumbnails_ = new QListWidget;
    configureIconView(thumbnails_);
    view_stack_->addWidget(thumbnails_);

    auto* assignPage = new QWidget;
    auto* assignLayout = new QVBoxLayout(assignPage);
    assignLayout->setContentsMargins(0, 0, 0, 0);
    keyword_grid_ = new QListWidget;
    configureIconView(keyword_grid_);
    keyword_grid_->setSelectionMode(QAbstractItemView::ExtendedSelection);
    assignLayout->addWidget(keyword_grid_, 1);
    auto* selectRow = new QHBoxLayout;
    auto* selectAll = new QPushButton(tr("Select All"));
    auto* clearSelection = new QPushButton(tr("Clear Selection"));
    selectRow->addWidget(selectAll); selectRow->addWidget(clearSelection); selectRow->addStretch();
    assignLayout->addLayout(selectRow);
    auto* keywordRow = new QHBoxLayout;
    keywordRow->addWidget(new QLabel(tr("Keyword:")));
    bulk_keyword_edit_ = new QLineEdit;
    bulk_keyword_edit_->setPlaceholderText(tr("Comma-separated keywords…"));
    auto* apply = new QPushButton(tr("Apply to Selected"));
    keywordRow->addWidget(bulk_keyword_edit_, 1);
    keywordRow->addWidget(apply);
    assignLayout->addLayout(keywordRow);
    view_stack_->addWidget(assignPage);
    leftLayout->addWidget(view_stack_, 1);
    list_status_ = new QLabel;
    list_status_->setStyleSheet("font-size: 10px; color: #555; padding: 2px;");
    leftLayout->addWidget(list_status_);
    split->addWidget(left);

    auto* rightSplit = new QSplitter(Qt::Vertical);
    canvas_ = new PatternCanvas;
    rightSplit->addWidget(canvas_);
    auto* info = new QGroupBox(tr("File Information"));
    auto* infoLayout = new QVBoxLayout(info);
    infoLayout->setContentsMargins(6, 6, 6, 6);
    infoLayout->setSpacing(4);
    auto row = [infoLayout](const QString& label, QWidget* value) {
        auto* layout = new QHBoxLayout;
        auto* caption = new QLabel("<b>" + label + "</b>");
        layout->addWidget(caption);
        layout->addWidget(value, 1);
        infoLayout->addLayout(layout);
    };
    file_name_ = new QLabel("—");
    file_name_->setTextInteractionFlags(Qt::TextSelectableByMouse);
    row(tr("File:"), file_name_);
    file_path_ = new QLabel("—");
    file_path_->setTextInteractionFlags(Qt::TextSelectableByMouse);
    file_path_->setWordWrap(true);
    file_path_->setStyleSheet("color: #444;");
    row(tr("Path:"), file_path_);
    dimensions_ = new QLabel("—");
    dimensions_->setTextInteractionFlags(Qt::TextSelectableByMouse);
    dimensions_->setStyleSheet("color: #444;");
    row(tr("Dimensions:"), dimensions_);
    infoLayout->addWidget(new QLabel(tr("<b>Comments:</b>")));
    comments_ = new QPlainTextEdit;
    comments_->setReadOnly(true);
    comments_->setPlaceholderText(tr("No comments in this file."));
    comments_->setFixedHeight(70);
    comments_->setStyleSheet("background: #f8f8f8; border: 1px solid #ccc; font-family: monospace;");
    infoLayout->addWidget(comments_);
    keyword_edit_ = new QLineEdit;
    keyword_edit_->setPlaceholderText(tr("Enter keywords, separated by commas…"));
    keyword_edit_->setToolTip(tr("Keywords save when focus leaves this field and are searchable."));
    row(tr("Keywords:"), keyword_edit_);
    rightSplit->addWidget(info);
    rightSplit->setStretchFactor(0, 1);
    rightSplit->setStretchFactor(1, 0);
    rightSplit->setSizes({640, 160});
    split->addWidget(rightSplit);
    split->setStretchFactor(1, 1);
    split->setSizes({300, 960});
    root->addWidget(split, 1);
    setCentralWidget(central);

    connect(travel, &QCheckBox::toggled, canvas_, &PatternCanvas::setShowTravel);
    connect(markers, &QCheckBox::toggled, canvas_, &PatternCanvas::setShowMarkers);
    connect(width, &QSlider::valueChanged, this,
            [this](int value) { canvas_->setLineWidth(value / 100.0); });
    connect(search_, &QLineEdit::textChanged, search_timer_,
            qOverload<>(&QTimer::start));
    connect(search_timer_, &QTimer::timeout, this, &MainWindow::applyFilter);
    auto scopeChanged = [this](bool) {
        if (!search_names_->isChecked() && !search_keywords_->isChecked())
            search_names_->setChecked(true);
        applyFilter();
    };
    connect(search_names_, &QCheckBox::toggled, this, scopeChanged);
    connect(search_keywords_, &QCheckBox::toggled, this, scopeChanged);
    auto queueVisible = [this](int) { thumbnail_timer_->start(60); };
    connect(thumbnails_->verticalScrollBar(), &QScrollBar::valueChanged, this, queueVisible);
    connect(keyword_grid_->verticalScrollBar(), &QScrollBar::valueChanged, this, queueVisible);
    connect(thumbnail_timer_, &QTimer::timeout, this,
            qOverload<>(&MainWindow::scheduleVisibleThumbnails));
    connect(background_thumbnail_timer_, &QTimer::timeout, this,
            &MainWindow::scheduleNextBackgroundThumbnail);
    connect(files_, &QListWidget::currentItemChanged, this,
            [this](QListWidgetItem* item) { if (item) openFile(item->data(Qt::UserRole).toString()); });
    connect(thumbnails_, &QListWidget::itemClicked, this,
            [this](QListWidgetItem* item) { openFile(item->data(Qt::UserRole).toString()); });
    connect(keyword_grid_, &QListWidget::itemDoubleClicked, this,
            [this](QListWidgetItem* item) { openFile(item->data(Qt::UserRole).toString()); });
    connect(keyword_edit_, &QLineEdit::editingFinished, this, &MainWindow::saveCurrentKeywords);
    connect(selectAll, &QPushButton::clicked, this, &MainWindow::selectAllVisible);
    connect(clearSelection, &QPushButton::clicked, keyword_grid_, &QListWidget::clearSelection);
    connect(apply, &QPushButton::clicked, this, &MainWindow::applyBulkKeywords);
    connect(bulk_keyword_edit_, &QLineEdit::returnPressed, this, &MainWindow::applyBulkKeywords);
    statusBar()->showMessage(tr("Ready · Scroll = zoom · Drag = pan · F/Home = fit · +/− = zoom"));
}

void MainWindow::buildMenus() {
    auto* file = menuBar()->addMenu(tr("&File"));
    auto action = [this](QMenu* menu, const QString& text, const QKeySequence& shortcut,
                         auto callback) {
        auto* a = menu->addAction(text);
        if (!shortcut.isEmpty()) a->setShortcut(shortcut);
        connect(a, &QAction::triggered, this, callback);
        return a;
    };
    action(file, tr("&Add File…"), QKeySequence("Ctrl+O"), [this] { addFiles(); });
    action(file, tr("Add &Folder…"), QKeySequence("Ctrl+Shift+O"), [this] { addFolder(); });
    file->addSeparator();
    action(file, tr("&Remove Selected"), QKeySequence::Delete, [this] { removeSelected(); });
    action(file, tr("&Purge Missing"), {}, [this] { purgeMissing(); });
    action(file, tr("Reset &Database…"), {}, [this] { resetDatabase(); });
    action(file, tr("Clear &Thumbnail Cache"), {}, [this] { clearThumbnailCache(); });
    action(file, tr("&Merge Duplicates…"), {}, [this] { mergeDuplicates(); });
    file->addSeparator();
    action(file, tr("&Quit"), QKeySequence::Quit, [] { QApplication::quit(); });

    auto* view = menuBar()->addMenu(tr("&View"));
    auto* group = new QActionGroup(this);
    auto addView = [&](const QString& text, const QKeySequence& shortcut, int mode) {
        auto* a = action(view, text, shortcut, [this, mode] { setViewMode(mode); });
        a->setCheckable(true); group->addAction(a); a->setChecked(mode == 0);
    };
    addView(tr("&List View"), QKeySequence("Ctrl+L"), 0);
    addView(tr("&Thumbnail Mode"), QKeySequence("Ctrl+P"), 1);
    addView(tr("&Assign Keywords"), QKeySequence("Ctrl+K"), 2);
    view->addSeparator();
    action(view, tr("Line &Color…"), {}, [this] { chooseLineColor(); });
    action(view, tr("Show &Keywords List…"), {}, [this] { showKeywordsList(); });

    auto* convert = menuBar()->addMenu(tr("&Convert"));
    action(convert, tr("Convert &SVG to PAT…"), QKeySequence("Ctrl+Shift+S"),
           [this] { convertSourceDialog(".svg", ".pat"); });
    action(convert, tr("Convert S&VG to QLI…"), QKeySequence("Ctrl+Shift+V"),
           [this] { convertSourceDialog(".svg", ".qli"); });
    action(convert, tr("Convert &QLI to PAT…"), QKeySequence("Ctrl+Shift+Q"),
           [this] { convertSourceDialog(".qli", ".pat"); });
    convert->addSeparator();
    action(convert, tr("Convert &Folder…"), {}, [this] { convertFolder(false); });
    action(convert, tr("Convert Folder and &Subfolders…"), {}, [this] { convertFolder(true); });

    auto* help = menuBar()->addMenu(tr("&Help"));
    action(help, tr("&Help"), QKeySequence::HelpContents, [this] { showHelp(); });
    help->addSeparator();
    action(help, tr("&About"), {}, [this] { showAbout(); });
}

QString MainWindow::libraryFilePath() {
    return QStandardPaths::writableLocation(QStandardPaths::AppDataLocation) + "/library.json";
}

// The library and keywords live in a JSON file rather than QSettings, which on
// Windows is the registry: rewriting thousands of paths there on every keyword
// edit was slow, and Microsoft advises against large registry values.
//
// Earlier versions stored everything under the organisation name "OpenAI":
// first the library in QSettings, later library.json in that app data folder.
// Whatever is found there is moved once, and removed after the new file has
// been written.
void MainWindow::loadLibrary() {
    QSettings settings(settings_organization, settings_application);
    QSettings legacy(legacy_organization, settings_application);
    const bool legacyColor = !settings.contains("lineColor") && legacy.contains("lineColor");
    line_color_ = QColor((legacyColor ? legacy : settings).value("lineColor", "#000000").toString());
    canvas_->setLineColor(line_color_);

    enum class Source { File, LegacyFile, LegacySettings };
    std::optional<Source> source;
    const QString legacyDataDir = legacyLocation(QStandardPaths::AppDataLocation);
    const QString legacyFile = legacyDataDir.isEmpty() ? QString() : legacyDataDir + "/library.json";
    auto readFile = [this](const QString& path) {
        QFile file(path);
        if (!file.open(QIODevice::ReadOnly)) return false;
        QJsonParseError error{};
        const auto document = QJsonDocument::fromJson(file.readAll(), &error);
        if (!document.isObject()) {
            // Keep the unreadable file for inspection; the next save replaces it.
            const QString kept = path + ".unreadable";
            QFile::remove(kept);
            QFile::copy(path, kept);
            statusBar()->showMessage(
                tr("The pattern library could not be read (%1). A copy was kept as %2.")
                    .arg(error.errorString(), QDir::toNativeSeparators(kept)));
            return false;
        }
        const auto root = document.object();
        for (const auto& value : root.value("library").toArray()) library_ << value.toString();
        keywords_ = root.value("keywords").toObject().toVariantMap();
        return true;
    };
    if (QFile::exists(libraryFilePath())) {
        if (readFile(libraryFilePath())) source = Source::File;
    } else if (!legacyFile.isEmpty() && QFile::exists(legacyFile)) {
        if (readFile(legacyFile)) source = Source::LegacyFile;
    } else if (legacy.contains("library") || legacy.contains("keywords")) {
        library_ = legacy.value("library").toStringList();
        keywords_ = legacy.value("keywords").toMap();
        source = Source::LegacySettings;
    }
    rebuildLibraryKeys();

    const bool migrating = (source && *source != Source::File) || legacyColor;
    if (migrating) {
        library_dirty_ = true;
        if (flushLibrary()) {
            if (source == Source::LegacyFile) QFile::remove(legacyFile);
            if (source == Source::LegacySettings) {
                legacy.remove("library");
                legacy.remove("keywords");
            }
            if (legacyColor) legacy.remove("lineColor");
        }
    }
}

void MainWindow::saveLibrary() {
    library_dirty_ = true;
    save_timer_->start();
}

bool MainWindow::flushLibrary() {
    save_timer_->stop();
    if (!library_dirty_) return true;
    QSettings settings(settings_organization, settings_application);
    settings.setValue("lineColor", line_color_.name());
    QJsonObject root;
    root["version"] = 1;
    root["library"] = QJsonArray::fromStringList(library_);
    root["keywords"] = QJsonObject::fromVariantMap(keywords_);
    const QString path = libraryFilePath();
    QDir().mkpath(QFileInfo(path).absolutePath());
    if (!writeFileAtomically(path, QJsonDocument(root).toJson())) {
        // Stays dirty, so the next change or closing the window tries again.
        statusBar()->showMessage(
            tr("Could not save the pattern library to %1.").arg(QDir::toNativeSeparators(path)));
        return false;
    }
    library_dirty_ = false;
    return true;
}

void MainWindow::runInBackground(const QString& title, const QString& label,
                                 std::function<void(BackgroundJob&)> work,
                                 std::function<void(bool cancelled)> finish) {
    if (job_running_) {
        statusBar()->showMessage(tr("Please wait for the current task to finish."));
        return;
    }
    job_running_ = true;
    auto cancel = std::make_shared<std::atomic<bool>>(false);
    job_cancel_ = cancel;
    const QPointer<JobDialog> dialog(
        new JobDialog(title, label, [cancel] { cancel->store(true); }, this));
    // Quick jobs finish before the window would flash up.
    QTimer::singleShot(400, dialog, [dialog] { if (dialog) dialog->show(); });
    const QPointer<MainWindow> self(this);
    // `work` runs on a worker thread and must not touch the window; `finish`
    // runs on the GUI thread once it returns.
    QThreadPool::globalInstance()->start(
        [work = std::move(work), finish = std::move(finish), cancel, self, dialog] {
            BackgroundJob job(cancel, self, dialog);
            work(job);
            if (!self) return;
            QMetaObject::invokeMethod(self, [self, dialog, finish, cancel] {
                if (!self) return;
                self->job_running_ = false;
                if (dialog) {
                    dialog->hide();
                    dialog->deleteLater();
                }
                finish(cancel->load());
            }, Qt::QueuedConnection);
        });
}

QString MainWindow::normalizeKeywords(const QString& value) {
    QStringList result;
    QSet<QString> seen;
    for (const auto& raw : value.split(',')) {
        const auto word = raw.trimmed();
        const auto key = word.toCaseFolded();
        if (!word.isEmpty() && !seen.contains(key)) { seen.insert(key); result << word; }
    }
    return result.join(", ");
}

void MainWindow::rebuildLibraryKeys() {
    library_keys_.clear();
    library_keys_.reserve(library_.size());
    // Also drops entries that differ only in case, which addPath never adds.
    library_.removeIf([this](const QString& path) {
        const auto key = path.toCaseFolded();
        if (library_keys_.contains(key)) return true;
        library_keys_.insert(key);
        return false;
    });
}

int MainWindow::addCanonicalPaths(const QStringList& paths) {
    int added = 0;
    for (const auto& path : paths) {
        if (path.isEmpty()) continue;
        const auto key = path.toCaseFolded();
        if (library_keys_.contains(key)) continue;
        library_keys_.insert(key);
        library_ << path;
        ++added;
    }
    return added;
}

// Call when the library's contents change; searching only calls applyFilter().
void MainWindow::refreshList() {
    rows_ = library_;
    rows_.sort(Qt::CaseInsensitive);
    populateView();
}

QListWidget* MainWindow::activeList() const {
    return view_mode_ == 0 ? files_ : view_mode_ == 1 ? thumbnails_ : keyword_grid_;
}

QListWidgetItem* MainWindow::itemForPath(const QString& path) const {
    const int row = row_of_.value(path, -1);
    return row < 0 ? nullptr : activeList()->item(row);
}

QString MainWindow::itemToolTip(const QString& path) const {
    const auto words = keywords_.value(path).toString();
    return words.isEmpty() ? path : path + "\nKeywords: " + words;
}

void MainWindow::populateView() {
    invalidateThumbnails();
    QListWidget* list = activeList();
    const bool icons = view_mode_ != 0;
    // Only the active view holds items; the others are rebuilt when shown.
    for (auto* other : {files_, thumbnails_, keyword_grid_})
        if (other != list) other->clear();
    list->clear();
    row_names_.clear();
    row_names_.reserve(rows_.size());
    row_of_.clear();
    row_of_.reserve(rows_.size());
    for (int row = 0; row < rows_.size(); ++row) {
        const QString& path = rows_.at(row);
        const QFileInfo info(path);
        row_names_ << info.fileName();
        row_of_.insert(path, row);
        auto* item = new QListWidgetItem(row_names_.back(), list);
        item->setData(Qt::UserRole, path);
        item->setToolTip(itemToolTip(path));
        if (icons) {
            item->setIcon(placeholder_icon_);
        } else {
            const auto ext = info.suffix().toLower();
            item->setForeground(ext == "pat" ? QColor(0, 120, 40) :
                                ext == "qli" ? QColor(30, 90, 190) : QColor(145, 85, 25));
        }
    }
    row_hidden_.assign(static_cast<std::size_t>(rows_.size()), 0);
    if (auto* item = itemForPath(current_)) {
        // Make it current, so keyboard navigation continues from it, without
        // reopening the file.
        const QSignalBlocker blocker(list);
        list->setCurrentItem(item);
    }
    applyFilter();
}

void MainWindow::applyFilter() {
    QListWidget* list = activeList();
    const auto needle = search_->text().trimmed();
    const bool names = search_names_->isChecked();
    const bool words = search_keywords_->isChecked();
    shown_rows_.clear();
    shown_position_.assign(static_cast<std::size_t>(rows_.size()), -1);
    for (int row = 0; row < rows_.size(); ++row) {
        const bool match = needle.isEmpty() ||
            (names && row_names_.at(row).contains(needle, Qt::CaseInsensitive)) ||
            (words && keywords_.value(rows_.at(row)).toString()
                          .contains(needle, Qt::CaseInsensitive));
        if (match) {
            shown_position_[row] = static_cast<int>(shown_rows_.size());
            shown_rows_.push_back(row);
        }
        const char hide = match ? 0 : 1;
        if (row_hidden_[row] == hide) continue;
        row_hidden_[row] = hide;
        // Hidden items must not stay selected, or bulk actions would reach them.
        if (hide && list->item(row)->isSelected()) list->item(row)->setSelected(false);
        list->setRowHidden(row, hide);
    }
    list_status_->setText(tr("%1 matches / %2 total").arg(shown_rows_.size()).arg(library_.size()));
    if (view_mode_ != 0) {
        background_thumbnail_cursor_ = 0;
        thumbnail_timer_->start(0);
        background_thumbnail_timer_->start(250);
    }
}

void MainWindow::selectAllVisible() {
    // QAbstractItemView::selectAll() would also select rows hidden by the search.
    QItemSelection selection;
    const auto* model = keyword_grid_->model();
    for (std::size_t i = 0; i < shown_rows_.size();) {
        std::size_t j = i;
        while (j + 1 < shown_rows_.size() && shown_rows_[j + 1] == shown_rows_[j] + 1) ++j;
        selection.select(model->index(shown_rows_[i], 0), model->index(shown_rows_[j], 0));
        i = j + 1;
    }
    keyword_grid_->selectionModel()->select(selection, QItemSelectionModel::ClearAndSelect);
}

void MainWindow::setViewMode(int mode) {
    view_mode_ = mode;
    view_stack_->setCurrentIndex(mode);
    populateView();
}

void MainWindow::invalidateThumbnails() {
    ++thumbnail_generation_;
    live_generation_->store(thumbnail_generation_);
    thumbnail_timer_->stop();
    background_thumbnail_timer_->stop();
    thumbnail_pool_->clear();
    thumbnail_inflight_.clear();
    background_batches_inflight_ = 0;
    thumbnail_loaded_.clear();
    keep_first_row_ = 0;
    keep_last_row_ = -1;
}

void MainWindow::scheduleVisibleThumbnails() {
    if (view_mode_ == 1) scheduleVisibleThumbnails(thumbnails_);
    else if (view_mode_ == 2) scheduleVisibleThumbnails(keyword_grid_);
}

void MainWindow::scheduleVisibleThumbnails(QListWidget* target) {
    if (!target || shown_rows_.empty() || !target->isVisible()) return;
    const QRect area = target->viewport()->rect();
    const QSize grid = target->gridSize();
    const int columns = std::max(1, area.width() / std::max(1, grid.width()));
    const int perPage = columns * (area.height() / std::max(1, grid.height()) + 2);
    // Scan across a line of the viewport so a gap between cells is not
    // mistaken for the absence of items.
    auto rowsAt = [&](int y) {
        int lowest = -1, highest = -1;
        for (int x = 6; x < area.width(); x += std::max(8, grid.width() / 2)) {
            const QModelIndex index = target->indexAt(QPoint(x, y));
            if (!index.isValid()) continue;
            if (lowest < 0 || index.row() < lowest) lowest = index.row();
            highest = std::max(highest, index.row());
        }
        return std::pair{lowest, highest};
    };
    // Positions below count only rows that match the search (shown_rows_);
    // indexAt() never returns a hidden row.
    auto shownPosition = [this](int row) { return row < 0 ? -1 : shown_position_[row]; };
    int first = shownPosition(rowsAt(6).first);
    if (first < 0) first = shownPosition(rowsAt(grid.height() / 2).first);
    if (first < 0) {
        // The batched layout has not reached the visible rows yet.
        if (target->verticalScrollBar()->value() != 0) {
            thumbnail_timer_->start(60);
            return;
        }
        first = 0;
    }
    int last = shownPosition(rowsAt(area.height() - 6).second);
    if (last < 0) last = shownPosition(rowsAt(area.height() - grid.height() / 2).second);
    if (last < first) last = first + perPage;
    const int lastPosition = static_cast<int>(shown_rows_.size()) - 1;
    last = std::min(lastPosition, last);

    // Icons a few pages away from the viewport are released, so memory stays
    // bounded however large the library is.
    const int keep = std::max(100, 4 * perPage);
    keep_first_row_ = shown_rows_[std::max(0, first - keep)];
    keep_last_row_ = shown_rows_[std::min(lastPosition, last + keep)];
    evictThumbnails(target);

    // A small prefetch margin keeps normal scrolling smooth without rendering
    // thousands of off-screen files.
    const int prefetch = std::max(20, perPage);
    const int firstToLoad = std::max(0, first - prefetch);
    const int lastToLoad = std::min(lastPosition, last + prefetch);
    for (int position = firstToLoad; position <= lastToLoad; ++position) {
        const int priority = position >= first && position <= last ? 10 : 5;
        requestThumbnail(rows_.at(shown_rows_[position]), target, priority);
    }
}

void MainWindow::scheduleNextBackgroundThumbnail() {
    if (view_mode_ == 0) return;

    // Background batches only fill the disk cache; nothing is decoded for
    // display, so filling a large library costs no GUI time or memory. They use
    // fewer than half the pool, reserving most workers for visible items.
    constexpr int batch_size = 16;
    const int shown = static_cast<int>(shown_rows_.size());
    while (background_batches_inflight_ < max_background_thumbnails_ &&
           background_thumbnail_cursor_ < shown) {
        QStringList batch;
        while (batch.size() < batch_size && background_thumbnail_cursor_ < shown) {
            const QString& path = rows_.at(shown_rows_[background_thumbnail_cursor_++]);
            if (!thumbnail_loaded_.contains(path) && !thumbnail_inflight_.contains(path) &&
                !thumbnail_failed_.contains(path))
                batch << path;
        }
        if (batch.isEmpty()) continue;
        ++background_batches_inflight_;
        const auto generation = thumbnail_generation_;
        thumbnail_pool_->start(new ThumbnailWarmTask(
            batch, thumbnail_cache_dir_, line_color_, generation, live_generation_, this,
            [this, generation](const QStringList& failed) {
                if (generation != thumbnail_generation_) return;
                --background_batches_inflight_;
                for (const auto& path : failed) thumbnail_failed_.insert(path);
                background_thumbnail_timer_->start(background_thumbnail_delay_ms);
            }), -10);
    }
}

void MainWindow::requestThumbnail(const QString& path, QListWidget* target, int priority) {
    if (thumbnail_loaded_.contains(path) || thumbnail_inflight_.contains(path) ||
        thumbnail_failed_.contains(path))
        return;
    thumbnail_inflight_.insert(path);
    const auto generation = thumbnail_generation_;
    auto* task = new ThumbnailLoadTask(path, thumbnail_cache_dir_, line_color_, generation,
        live_generation_, this,
        [this, path, target = QPointer<QListWidget>(target), generation](const QImage& image) {
            if (generation != thumbnail_generation_) return;
            thumbnail_inflight_.remove(path);
            if (image.isNull()) {
                thumbnail_failed_.insert(path);
                return;
            }
            // The user may have scrolled away while this was loading.
            const int row = row_of_.value(path, -1);
            if (!target || row < keep_first_row_ || row > keep_last_row_) return;
            thumbnail_loaded_.insert(path);
            applyThumbnail(path, image, target);
        });
    thumbnail_pool_->start(task, priority);
}

void MainWindow::evictThumbnails(QListWidget* target) {
    for (auto it = thumbnail_loaded_.begin(); it != thumbnail_loaded_.end();) {
        const int row = row_of_.value(*it, -1);
        if (row >= keep_first_row_ && row <= keep_last_row_) {
            ++it;
            continue;
        }
        if (auto* item = target->item(row)) item->setIcon(placeholder_icon_);
        it = thumbnail_loaded_.erase(it);
    }
}

void MainWindow::applyThumbnail(const QString& path, const QImage& image, QListWidget* target) {
    if (auto* item = target->item(row_of_.value(path, -1)))
        item->setIcon(QPixmap::fromImage(image));
}

QImage MainWindow::renderThumbnail(const QString& path, const QColor& color) {
    try {
        const Pattern pattern = load_pattern(toPath(path));
        const Bounds b = pattern_bounds(pattern);
        QImage image(thumbnail_size, thumbnail_size, QImage::Format_ARGB32_Premultiplied);
        image.fill(Qt::white);
        if (!b.valid) return image;
        const double scale = std::min((thumbnail_size - 12.0) / std::max(b.width(), .001),
                                      (thumbnail_size - 12.0) / std::max(b.height(), .001));
        auto map = [&](Point p) {
            return QPointF(6 + (p.x - b.min_x) * scale,
                           thumbnail_size - 6 - (p.y - b.min_y) * scale);
        };
        QPainter painter(&image);
        painter.setRenderHint(QPainter::Antialiasing);
        QPen pen(color, 0.5);
        pen.setCosmetic(true);
        painter.setPen(pen);
        QPainterPath stitchPath;
        Point current{};
        bool have = false;
        for (const auto& move : pattern.moves) {
            if (have && move.is_stitch()) {
                stitchPath.moveTo(map(current));
                if ((move.gcode == 2 || move.gcode == 3) && move.center) {
                    const auto points = sample_arc(current, move, 8.0);
                    for (std::size_t i = 1; i < points.size(); ++i)
                        stitchPath.lineTo(map(points[i]));
                } else {
                    stitchPath.lineTo(map(move.end));
                }
            }
            current = move.end; have = true;
        }
        painter.drawPath(stitchPath);
        return image;
    } catch (...) { return {}; }
}

void MainWindow::addPath(const QString& path) {
    QFileInfo info(path);
    if (!info.isFile() || !is_supported_file(toPath(info.absoluteFilePath()))) return;
    addCanonicalPaths({info.canonicalFilePath()});
}

void MainWindow::addFiles() {
    const auto paths = QFileDialog::getOpenFileNames(this, tr("Add quilting patterns"), {},
                                                     tr("Patterns (*.pat *.qli *.svg)"));
    for (const auto& path : paths) addPath(path);
    saveLibrary(); refreshList();
}

void MainWindow::addFolder() {
    const auto folder = QFileDialog::getExistingDirectory(this, tr("Add pattern folder"));
    if (!folder.isEmpty()) scanFolder(folder);
}

void MainWindow::scanFolder(const QString& folder) {
    const QString shownFolder = QDir::toNativeSeparators(folder);
    auto found = std::make_shared<QStringList>();
    // Walking the tree and resolving canonical paths (which opens every file on
    // Windows) runs on a worker, so the window keeps repainting meanwhile.
    runInBackground(tr("Add Folder"), tr("Scanning %1…").arg(shownFolder),
        [folder, shownFolder, found](BackgroundJob& job) {
            QDirIterator it(folder, {"*.pat", "*.qli", "*.svg"}, QDir::Files,
                            QDirIterator::Subdirectories);
            while (it.hasNext() && !job.cancelled()) {
                const QString canonical = it.nextFileInfo().canonicalFilePath();
                if (!canonical.isEmpty()) *found << canonical;
                job.progress(0, 0, tr("Scanning %1…\n%2 patterns found")
                                       .arg(shownFolder).arg(found->size()));
            }
        },
        [this, shownFolder, found](bool cancelled) {
            if (cancelled) {
                statusBar()->showMessage(tr("Adding %1 was cancelled.").arg(shownFolder));
                return;
            }
            const int added = addCanonicalPaths(*found);
            saveLibrary();
            refreshList();
            statusBar()->showMessage(tr("Added %1 new pattern(s) from %2 (%3 found).")
                                         .arg(added).arg(shownFolder).arg(found->size()));
        });
}

void MainWindow::openFile(const QString& path) {
    if (path == current_ || path == loading_path_) return;
    loading_path_ = path;
    const quint64 request = ++open_request_;
    struct Loaded {
        Pattern pattern;
        Bounds bounds;
        std::size_t stitch{};
        std::size_t travel{};
        QString error;
    };
    // Parsing a large SVG or PAT can take a while, so it happens on a worker.
    // Only the most recent request is shown; quick keyboard navigation through
    // the list skips the files in between.
    const QPointer<MainWindow> self(this);
    QThreadPool::globalInstance()->start([self, path, request] {
        auto loaded = std::make_shared<Loaded>();
        try {
            loaded->pattern = load_pattern(toPath(path));
            loaded->bounds = pattern_bounds(loaded->pattern);
            for (const auto& move : loaded->pattern.moves)
                move.gcode == 0 ? ++loaded->travel : ++loaded->stitch;
        } catch (const std::exception& e) {
            loaded->error = QString::fromUtf8(e.what());
        }
        if (!self) return;
        QMetaObject::invokeMethod(self, [self, path, request, loaded] {
            if (!self || request != self->open_request_) return;
            self->loading_path_.clear();
            if (!loaded->error.isEmpty()) {
                QMessageBox::critical(self, tr("Could not load pattern"), loaded->error);
                return;
            }
            const auto& bounds = loaded->bounds;
            self->file_name_->setText(QFileInfo(path).fileName());
            self->file_path_->setText(path);
            self->dimensions_->setText(QString("%1 × %2 PAT units")
                                           .arg(bounds.width(), 0, 'f', 3)
                                           .arg(bounds.height(), 0, 'f', 3));
            self->statistics_->setText(tr("%1 blocks · %2 stitch · %3 travel")
                                           .arg(loaded->pattern.moves.size())
                                           .arg(loaded->stitch).arg(loaded->travel));
            QStringList lines;
            for (const auto& c : loaded->pattern.comments) lines << QString::fromStdString(c);
            self->comments_->setPlainText(lines.join('\n'));
            self->keyword_edit_->setText(self->keywords_.value(path).toString());
            self->canvas_->setPattern(std::move(loaded->pattern));
            self->current_ = path;
            self->setWindowTitle(tr("Innova PAT / QLI Viewer — %1").arg(QFileInfo(path).fileName()));
            self->statusBar()->showMessage(tr("Loaded %1").arg(QFileInfo(path).fileName()));
        }, Qt::QueuedConnection);
    });
}

void MainWindow::closeCurrentFile() {
    ++open_request_;  // a load still in flight must not reopen it
    loading_path_.clear();
    current_.clear();
    canvas_->clear();
    file_name_->setText("—");
    file_path_->setText("—");
    dimensions_->setText("—");
    statistics_->clear();
    comments_->clear();
    keyword_edit_->clear();
    setWindowTitle(tr("Innova PAT / QLI Viewer"));
}

void MainWindow::removeSelected() {
    QStringList paths;
    for (auto* item : activeList()->selectedItems())
        if (!item->isHidden()) paths << item->data(Qt::UserRole).toString();
    if (paths.isEmpty() && !current_.isEmpty()) paths << current_;
    const QSet<QString> removing(paths.cbegin(), paths.cend());
    library_.removeIf([&removing](const QString& path) { return removing.contains(path); });
    for (const auto& path : paths) keywords_.remove(path);
    if (removing.contains(current_)) closeCurrentFile();
    rebuildLibraryKeys();
    saveLibrary(); refreshList();
    statusBar()->showMessage(tr("Removed %1 file(s) from the library; disk files were not deleted.")
                             .arg(paths.size()));
}

void MainWindow::purgeMissing() {
    auto missing = std::make_shared<QSet<QString>>();
    // Checking every file can be slow on network drives, so it runs on a worker.
    runInBackground(tr("Purge Missing"), tr("Checking files…"),
        [library = library_, missing](BackgroundJob& job) {
            for (int i = 0; i < library.size() && !job.cancelled(); ++i) {
                if (!QFileInfo::exists(library[i])) missing->insert(library[i]);
                job.progress(i, library.size(),
                             tr("Checking %1 of %2 files…").arg(i + 1).arg(library.size()));
            }
        },
        [this, missing](bool cancelled) {
            if (cancelled) {
                statusBar()->showMessage(tr("Purge Missing was cancelled; nothing was removed."));
                return;
            }
            library_.removeIf([&missing](const QString& path) { return missing->contains(path); });
            for (const auto& path : std::as_const(*missing)) keywords_.remove(path);
            if (missing->contains(current_)) closeCurrentFile();
            rebuildLibraryKeys();
            saveLibrary(); refreshList();
            statusBar()->showMessage(
                tr("Purged %1 missing file(s) from the database.").arg(missing->size()));
        });
}

void MainWindow::resetDatabase() {
    if (library_.isEmpty()) { QMessageBox::information(this, tr("Reset Database"), tr("The database is already empty.")); return; }
    if (QMessageBox::warning(this, tr("Reset Database"),
        tr("Remove all %1 file entries from the database?\n\nFiles on disk will not be deleted.\nThis cannot be undone.")
        .arg(library_.size()), QMessageBox::Yes | QMessageBox::No, QMessageBox::No) != QMessageBox::Yes) return;
    library_.clear(); library_keys_.clear(); keywords_.clear();
    closeCurrentFile();
    search_->clear(); saveLibrary(); refreshList();
}

void MainWindow::clearThumbnailCache() {
    QDir dir(thumbnail_cache_dir_);
    int count = 0;
    for (const auto& file : dir.entryList(QDir::Files)) if (dir.remove(file)) ++count;
    thumbnail_failed_.clear();
    populateView();
    statusBar()->showMessage(tr("Thumbnail cache cleared (%1 entries removed).").arg(count));
}

void MainWindow::mergeDuplicates() {
    auto groups = std::make_shared<QList<QStringList>>();
    // Hashing runs on a worker. Only files of the same type and size can be
    // identical, so most files are never read at all.
    runInBackground(tr("Merge Duplicates"), tr("Looking for identical files…"),
        [library = library_, groups](BackgroundJob& job) {
            QHash<QString, QStringList> bySize;
            for (int i = 0; i < library.size() && !job.cancelled(); ++i) {
                const QFileInfo info(library[i]);
                if (info.isFile())
                    bySize[info.suffix().toLower() + ':' + QString::number(info.size())] << library[i];
                job.progress(i, library.size(),
                             tr("Checking file sizes… %1 of %2").arg(i + 1).arg(library.size()));
            }
            QStringList candidates;
            for (const auto& sameSize : std::as_const(bySize))
                if (sameSize.size() > 1) candidates << sameSize;
            QHash<QString, QStringList> byContent;
            for (int i = 0; i < candidates.size() && !job.cancelled(); ++i) {
                const QString& path = candidates[i];
                const auto digest = fileDigest(path);
                if (!digest.isEmpty())
                    byContent[QFileInfo(path).suffix().toLower() + ':' + digest] << path;
                job.progress(i, candidates.size(), tr("Comparing %1 of %2 files of matching size…")
                                                       .arg(i + 1).arg(candidates.size()));
            }
            for (const auto& same : std::as_const(byContent))
                if (same.size() > 1) *groups << same;
        },
        [this, groups](bool cancelled) {
            if (cancelled) {
                statusBar()->showMessage(tr("Merge Duplicates was cancelled; nothing changed."));
                return;
            }
            int removed = 0;
            QStringList report;
            QSet<QString> removing;
            for (auto paths : std::as_const(*groups)) {
                paths.sort(Qt::CaseInsensitive);
                const auto keeper = paths.first();
                report << tr("KEPT: %1").arg(keeper);
                QString combined = keywords_.value(keeper).toString();
                for (int i = 1; i < paths.size(); ++i) {
                    combined += ", " + keywords_.value(paths[i]).toString();
                    removing.insert(paths[i]);
                    keywords_.remove(paths[i]);
                    ++removed;
                    report << tr("REMOVED: %1").arg(paths[i]);
                }
                keywords_[keeper] = normalizeKeywords(combined);
                report << "";
            }
            library_.removeIf([&removing](const QString& path) { return removing.contains(path); });
            if (removing.contains(current_)) closeCurrentFile();
            rebuildLibraryKeys();
            saveLibrary(); refreshList();
            if (groups->isEmpty()) {
                QMessageBox::information(this, tr("Merge Duplicates"),
                                         tr("No byte-identical files of the same type were found."));
                return;
            }
            // Keep the message box a sensible size for large libraries.
            constexpr int max_report_lines = 40;
            QString details = report.mid(0, max_report_lines).join('\n');
            if (report.size() > max_report_lines)
                details += tr("\n… and %1 more lines").arg(report.size() - max_report_lines);
            QMessageBox::information(this, tr("Duplicate Files Merged"),
                tr("Merged %1 duplicate set(s); %2 library entries removed.\n\n%3")
                    .arg(groups->size()).arg(removed).arg(details));
        });
}

void MainWindow::saveCurrentKeywords() {
    if (current_.isEmpty()) return;
    const auto normalized = normalizeKeywords(keyword_edit_->text());
    if (keyword_edit_->text() != normalized) keyword_edit_->setText(normalized);
    // editingFinished also fires whenever focus merely leaves the field.
    if (keywords_.value(current_).toString() == normalized) return;
    keywords_[current_] = normalized;
    saveLibrary();
    if (auto* item = itemForPath(current_)) item->setToolTip(itemToolTip(current_));
    // Only a keyword search can change which rows match.
    if (search_keywords_->isChecked() && !search_->text().trimmed().isEmpty()) applyFilter();
    statusBar()->showMessage(tr("Keywords saved for '%1'.").arg(QFileInfo(current_).fileName()));
}

void MainWindow::applyBulkKeywords() {
    const auto add = normalizeKeywords(bulk_keyword_edit_->text());
    QList<QListWidgetItem*> selected;
    for (auto* item : keyword_grid_->selectedItems())
        if (!item->isHidden()) selected << item;
    if (add.isEmpty() || selected.isEmpty()) return;
    for (auto* item : selected) {
        const auto path = item->data(Qt::UserRole).toString();
        keywords_[path] = normalizeKeywords(keywords_.value(path).toString() + ", " + add);
        item->setToolTip(itemToolTip(path));
    }
    saveLibrary();
    if (search_keywords_->isChecked() && !search_->text().trimmed().isEmpty()) applyFilter();
    statusBar()->showMessage(tr("Keywords applied to %1 selected file(s).").arg(selected.size()));
}

void MainWindow::showKeywordsList() {
    QMap<QString, int> counts;
    for (const auto& value : keywords_)
        for (const auto& keyword : value.toString().split(',', Qt::SkipEmptyParts))
            ++counts[keyword.trimmed()];
    QDialog dialog(this); dialog.setWindowTitle(tr("Keywords In Use")); dialog.resize(420, 500);
    auto* layout = new QVBoxLayout(&dialog);
    layout->addWidget(new QLabel(counts.isEmpty() ? tr("<b>No keywords have been assigned to any files yet.</b>") :
        tr("<b>%1 keywords in use.</b>").arg(counts.size())));
    auto* list = new QListWidget;
    for (auto it = counts.cbegin(); it != counts.cend(); ++it) {
        auto* item = new QListWidgetItem(tr("%1   (%2 files)").arg(it.key()).arg(it.value()), list);
        item->setData(Qt::UserRole, it.key());
    }
    layout->addWidget(list);
    auto* close = new QPushButton(tr("Close")); layout->addWidget(close);
    connect(close, &QPushButton::clicked, &dialog, &QDialog::accept);
    connect(list, &QListWidget::itemDoubleClicked, &dialog, [this, &dialog](QListWidgetItem* item) {
        search_->setText(item->data(Qt::UserRole).toString()); search_names_->setChecked(false);
        search_keywords_->setChecked(true); dialog.accept();
    });
    dialog.exec();
}

void MainWindow::convertSourceDialog(const QString& sourceExt, const QString& outputExt) {
    const auto sources = QFileDialog::getOpenFileNames(this, tr("Select file(s) to convert"), {},
        sourceExt == ".svg" ? tr("SVG Files (*.svg)") : tr("QLI Files (*.qli)"));
    if (sources.isEmpty()) return;
    if (sources.size() == 1) {
        auto output = sources.first();
        output.chop(sourceExt.size());
        output += outputExt;
        output = QFileDialog::getSaveFileName(this, tr("Save converted file"), output,
            outputExt == ".pat" ? tr("Innova PAT (*.pat)") : tr("Statler QLI (*.qli)"));
        if (output.isEmpty()) return;
        try { convert_file(toPath(sources.first()), toPath(output)); addPath(output); }
        catch (const std::exception& e) { QMessageBox::warning(this, tr("Conversion failed"), e.what()); }
        saveLibrary(); refreshList();
        return;
    }
    QList<QPair<QString, QString>> files;
    for (const auto& source : sources) {
        auto output = source;
        output.chop(sourceExt.size());
        files.append({source, output + outputExt});
    }
    convertFiles(files);
}

void MainWindow::convertFolder(bool recursive) {
    bool ok = false;
    const auto mode = QInputDialog::getItem(this, tr("Convert Folder"), tr("Conversion:"),
        {tr("QLI → PAT"), tr("SVG → PAT"), tr("SVG → QLI")}, 0, false, &ok);
    if (!ok) return;
    const auto folder = QFileDialog::getExistingDirectory(this, tr("Select folder"));
    if (folder.isEmpty()) return;
    const QString sourceExt = mode.startsWith("QLI") ? ".qli" : ".svg";
    const QString outputExt = mode.endsWith("QLI") ? ".qli" : ".pat";
    auto files = std::make_shared<QList<QPair<QString, QString>>>();
    auto existing = std::make_shared<int>(0);
    runInBackground(tr("Convert Folder"), tr("Looking for %1 files…").arg(sourceExt),
        [folder, sourceExt, outputExt, recursive, files, existing](BackgroundJob& job) {
            QDirIterator it(folder, {"*" + sourceExt}, QDir::Files,
                            recursive ? QDirIterator::Subdirectories : QDirIterator::NoIteratorFlags);
            while (it.hasNext() && !job.cancelled()) {
                const QString source = it.next();
                QString output = source;
                output.chop(sourceExt.size());
                output += outputExt;
                if (QFileInfo::exists(output)) ++*existing;
                files->append({source, output});
                job.progress(0, 0, tr("Looking for %1 files… %2 found")
                                       .arg(sourceExt).arg(files->size()));
            }
        },
        [this, files, existing, sourceExt](bool cancelled) {
            if (cancelled) {
                statusBar()->showMessage(tr("Folder conversion was cancelled."));
                return;
            }
            if (files->isEmpty()) {
                QMessageBox::information(this, tr("Folder Conversion"),
                                         tr("No %1 files were found.").arg(sourceExt));
                return;
            }
            convertFiles(*files, *existing);
        });
}

void MainWindow::convertFiles(const QList<QPair<QString, QString>>& files, int existing) {
    if (existing < 0) {
        existing = 0;
        for (const auto& file : files) existing += QFileInfo::exists(file.second) ? 1 : 0;
    }
    // One question for the whole batch instead of one per existing file.
    bool overwrite = false;
    if (existing > 0) {
        QMessageBox box(QMessageBox::Question, tr("Convert"),
                        tr("%1 of the %2 output files already exist.").arg(existing).arg(files.size()),
                        QMessageBox::NoButton, this);
        auto* overwriteButton = box.addButton(tr("Overwrite"), QMessageBox::AcceptRole);
        auto* skipButton = box.addButton(tr("Skip Existing"), QMessageBox::AcceptRole);
        box.addButton(QMessageBox::Cancel);
        box.setDefaultButton(skipButton);
        box.exec();
        if (box.clickedButton() != overwriteButton && box.clickedButton() != skipButton) return;
        overwrite = box.clickedButton() == overwriteButton;
    }
    struct Outcome {
        QStringList converted;
        int skipped = 0;
        QStringList failures;
    };
    auto outcome = std::make_shared<Outcome>();
    runInBackground(tr("Convert"), tr("Converting…"),
        [files, overwrite, outcome](BackgroundJob& job) {
            for (int i = 0; i < files.size() && !job.cancelled(); ++i) {
                const auto& [source, output] = files[i];
                job.progress(i, files.size(), tr("Converting %1 of %2\n%3")
                    .arg(i + 1).arg(files.size()).arg(QFileInfo(source).fileName()));
                if (!overwrite && QFileInfo::exists(output)) {
                    ++outcome->skipped;
                    continue;
                }
                try {
                    convert_file(toPath(source), toPath(output));
                    outcome->converted << QFileInfo(output).canonicalFilePath();
                } catch (const std::exception& e) {
                    outcome->failures << QString("%1: %2").arg(QDir::toNativeSeparators(source),
                                                               QString::fromUtf8(e.what()));
                }
            }
        },
        [this, total = files.size(), outcome](bool cancelled) {
            // Files converted before a cancel exist on disk, so they are kept.
            addCanonicalPaths(outcome->converted);
            saveLibrary(); refreshList();
            const auto done = outcome->converted.size() + outcome->skipped + outcome->failures.size();
            QString text = tr("Converted: %1\nSkipped: %2\nFailed: %3")
                               .arg(outcome->converted.size()).arg(outcome->skipped)
                               .arg(outcome->failures.size());
            if (cancelled) text.prepend(tr("Cancelled after %1 of %2 files.\n\n").arg(done).arg(total));
            constexpr int max_failures_shown = 10;
            if (!outcome->failures.isEmpty()) {
                text += "\n\n" + outcome->failures.mid(0, max_failures_shown).join('\n');
                if (outcome->failures.size() > max_failures_shown)
                    text += tr("\n… and %1 more").arg(outcome->failures.size() - max_failures_shown);
            }
            QMessageBox::information(this, tr("Conversion"), text);
        });
}

void MainWindow::chooseLineColor() {
    const auto color = QColorDialog::getColor(line_color_, this, tr("Choose Line Color"));
    if (!color.isValid()) return;
    line_color_ = color; canvas_->setLineColor(color); saveLibrary(); populateView();
}

void MainWindow::showHelp() {
    QDialog dialog(this); dialog.setWindowTitle(tr("Help — Innova PAT / QLI Viewer")); dialog.resize(640, 720);
    auto* layout = new QVBoxLayout(&dialog);
    auto* browser = new QTextBrowser;
    browser->setHtml(R"(
<h2>Innova PAT / QLI Viewer — Help</h2>
<p>A desktop app for viewing, organizing, and converting PAT, QLI, and SVG quilting patterns.
The persistent library stores paths and user keywords; library actions never delete disk files.</p>
<h3>File menu</h3><ul>
<li><b>Add File…</b> (Ctrl+O) and <b>Add Folder…</b> (Ctrl+Shift+O)</li>
<li><b>Remove Selected</b> (Del), <b>Purge Missing</b>, and <b>Reset Database…</b></li>
<li><b>Clear Thumbnail Cache</b> and <b>Merge Duplicates…</b></li></ul>
<h3>View menu</h3><ul>
<li><b>List View</b> (Ctrl+L), <b>Thumbnail Mode</b> (Ctrl+P), and
<b>Assign Keywords</b> (Ctrl+K)</li>
<li><b>Line Color…</b> changes canvas and thumbnail stitches.</li>
<li><b>Show Keywords List…</b> lists keyword usage; double-click to filter.</li></ul>
<h3>Convert menu</h3><p>Convert SVG→PAT, SVG→QLI, or QLI→PAT as individual files,
folders, or complete folder trees. Outputs are added to the library.</p>
<h3>Search and keywords</h3><p>Search is case-insensitive. Select Filename, Keyword,
or both. Edit comma-separated keywords in File Information, or bulk-assign them in
Assign Keywords mode. Duplicate keywords are normalized automatically.</p>
<h3>Canvas</h3><ul><li>Scroll: zoom toward cursor</li><li>Drag: pan</li>
<li>F/Home: fit</li><li>+/−: zoom</li><li>Travel paths and markers are toolbar options.</li></ul>
<h3>File Information</h3><p>Shows file, path, dimensions, embedded comments, and
editable keywords.</p>
<h3>Shortcuts</h3><p>Ctrl+O Add File · Ctrl+Shift+O Add Folder · Del Remove ·
Ctrl+L List · Ctrl+P Thumbnails · Ctrl+K Assign Keywords · Ctrl+Shift+S SVG→PAT ·
Ctrl+Shift+V SVG→QLI · Ctrl+Shift+Q QLI→PAT · F1 Help.</p>)");
    layout->addWidget(browser);
    auto* close = new QPushButton(tr("Close")); layout->addWidget(close);
    connect(close, &QPushButton::clicked, &dialog, &QDialog::accept);
    dialog.exec();
}

void MainWindow::showAbout() {
    QMessageBox::about(this, tr("About Innova PAT / QLI Viewer"),
        tr("<h3>Innova PAT / QLI Viewer</h3>"
           "<p>A viewer for Innova PAT and Statler Stitcher QLI quilting pattern files.</p>"
           "<p>Supports G00/G01 linear moves and G02/G03 circular arc moves. "
           "QLI files are converted to PAT format internally before display.</p>"
           "<p>Pattern files are stored in a persistent library with user-defined "
           "keywords for easy searching.</p>"));
}

void MainWindow::openInitialPath(const QString& path) {
    QFileInfo info(path);
    if (info.isDir()) {
        scanFolder(path);
    } else if (info.isFile()) {
        addPath(path); saveLibrary(); refreshList(); openFile(info.canonicalFilePath());
    }
}

void MainWindow::resizeEvent(QResizeEvent* event) {
    QMainWindow::resizeEvent(event);
    if (thumbnail_timer_ && view_mode_ != 0) thumbnail_timer_->start(60);
}

} // namespace qpv
