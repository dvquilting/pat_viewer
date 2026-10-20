#include "qpv/main_window.hpp"

#include "qpv/io.hpp"
#include "qpv/pattern_canvas.hpp"

#include <QActionGroup>
#include <QApplication>
#include <QCheckBox>
#include <QColorDialog>
#include <QCryptographicHash>
#include <QDialog>
#include <QDialogButtonBox>
#include <QDir>
#include <QDirIterator>
#include <QFile>
#include <QFileDialog>
#include <QFileInfo>
#include <QFrame>
#include <QGroupBox>
#include <QHBoxLayout>
#include <QInputDialog>
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
#include <QPushButton>
#include <QRunnable>
#include <QScrollBar>
#include <QSettings>
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
#include <unordered_map>

namespace qpv {
namespace {

constexpr int thumbnail_size = 120;
constexpr int background_thumbnail_delay_ms = 25;

QString thumbnailCacheDirectory() {
    QString root = QStandardPaths::writableLocation(QStandardPaths::CacheLocation);
    if (root.isEmpty()) root = QDir::tempPath() + "/QuiltPatternViewer";
    return root + "/thumbnails";
}

class ThumbnailTask final : public QRunnable {
public:
    ThumbnailTask(QString path, QString cachePath, QColor color, QPointer<QObject> context,
                  std::function<void(const QImage&)> done)
        : path_(std::move(path)), cache_path_(std::move(cachePath)), color_(color),
          context_(context), done_(std::move(done)) {
        setAutoDelete(true);
    }
    void run() override {
        QImage image = MainWindow::renderThumbnail(path_, color_);
        // Quality 80 selects a faster PNG compression level while remaining
        // lossless. It is also smaller than Qt's default for these line images.
        if (!image.isNull()) image.save(cache_path_, "PNG", 80);
        if (context_) QMetaObject::invokeMethod(context_, [context = context_, image, done = done_] {
            if (context) done(image);
        }, Qt::QueuedConnection);
    }
private:
    QString path_;
    QString cache_path_;
    QColor color_;
    QPointer<QObject> context_;
    std::function<void(const QImage&)> done_;
};

QString fileDigest(const QString& path) {
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) return {};
    QCryptographicHash hash(QCryptographicHash::Sha256);
    while (!file.atEnd()) hash.addData(file.read(1024 * 1024));
    return QString::fromLatin1(hash.result().toHex());
}

} // namespace

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
    buildUi();
    buildMenus();
    loadLibrary();
    refreshList();
    resize(1260, 800);
    setWindowTitle(tr("Innova PAT / QLI Viewer"));
}

MainWindow::~MainWindow() {
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
    view_stack_->addWidget(files_);

    thumbnails_ = new QListWidget;
    thumbnails_->setViewMode(QListView::IconMode);
    thumbnails_->setIconSize({thumbnail_size, thumbnail_size});
    thumbnails_->setGridSize({150, 160});
    thumbnails_->setResizeMode(QListView::Adjust);
    thumbnails_->setMovement(QListView::Static);
    view_stack_->addWidget(thumbnails_);

    auto* assignPage = new QWidget;
    auto* assignLayout = new QVBoxLayout(assignPage);
    assignLayout->setContentsMargins(0, 0, 0, 0);
    keyword_grid_ = new QListWidget;
    keyword_grid_->setViewMode(QListView::IconMode);
    keyword_grid_->setIconSize({thumbnail_size, thumbnail_size});
    keyword_grid_->setGridSize({150, 160});
    keyword_grid_->setResizeMode(QListView::Adjust);
    keyword_grid_->setMovement(QListView::Static);
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
    connect(search_timer_, &QTimer::timeout, this, &MainWindow::refreshList);
    auto scopeChanged = [this](bool) {
        if (!search_names_->isChecked() && !search_keywords_->isChecked())
            search_names_->setChecked(true);
        refreshList();
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
    connect(selectAll, &QPushButton::clicked, keyword_grid_, &QListWidget::selectAll);
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

void MainWindow::loadLibrary() {
    QSettings settings("OpenAI", "QuiltPatternViewer");
    library_ = settings.value("library").toStringList();
    library_.removeDuplicates();
    keywords_ = settings.value("keywords").toMap();
    line_color_ = QColor(settings.value("lineColor", "#000000").toString());
    canvas_->setLineColor(line_color_);
}

void MainWindow::saveLibrary() const {
    QSettings settings("OpenAI", "QuiltPatternViewer");
    settings.setValue("library", library_);
    settings.setValue("keywords", keywords_);
    settings.setValue("lineColor", line_color_.name());
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

void MainWindow::refreshList() {
    filtered_.clear();
    const auto needle = search_->text().trimmed();
    for (const auto& path : library_) {
        const bool nameMatch = search_names_->isChecked() &&
            QFileInfo(path).fileName().contains(needle, Qt::CaseInsensitive);
        const bool keywordMatch = search_keywords_->isChecked() &&
            keywords_.value(path).toString().contains(needle, Qt::CaseInsensitive);
        if (needle.isEmpty() || nameMatch || keywordMatch) filtered_ << path;
    }
    filtered_.sort(Qt::CaseInsensitive);
    populateView();
    list_status_->setText(tr("%1 matches / %2 total").arg(filtered_.size()).arg(library_.size()));
}

void MainWindow::populateView() {
    invalidateThumbnails();
    thumbnail_items_.clear();
    keyword_items_.clear();
    auto populate = [this](QListWidget* list, bool icons) {
        list->clear();
        for (const auto& path : filtered_) {
            auto* item = new QListWidgetItem(QFileInfo(path).fileName(), list);
            item->setData(Qt::UserRole, path);
            item->setToolTip(path + (keywords_.value(path).toString().isEmpty() ? "" :
                "\nKeywords: " + keywords_.value(path).toString()));
            if (icons) {
                if (list == thumbnails_) thumbnail_items_.insert(path, item);
                else keyword_items_.insert(path, item);
            } else {
                const auto ext = QFileInfo(path).suffix().toLower();
                item->setForeground(ext == "pat" ? QColor(0, 120, 40) :
                                    ext == "qli" ? QColor(30, 90, 190) : QColor(145, 85, 25));
            }
        }
        if (!current_.isEmpty()) {
            QListWidgetItem* item = nullptr;
            if (list == thumbnails_) item = thumbnail_items_.value(current_);
            else if (list == keyword_grid_) item = keyword_items_.value(current_);
            else {
                for (int i = 0; i < list->count(); ++i)
                    if (list->item(i)->data(Qt::UserRole).toString() == current_) {
                        item = list->item(i); break;
                    }
            }
            if (item) item->setSelected(true);
        }
    };
    if (view_mode_ == 0) populate(files_, false);
    else if (view_mode_ == 1) populate(thumbnails_, true);
    else populate(keyword_grid_, true);
    background_thumbnail_cursor_ = 0;
    if (view_mode_ != 0) {
        thumbnail_timer_->start(0);
        background_thumbnail_timer_->start(250);
    }
}

void MainWindow::setViewMode(int mode) {
    view_mode_ = mode;
    view_stack_->setCurrentIndex(mode);
    populateView();
}

void MainWindow::invalidateThumbnails() {
    ++thumbnail_generation_;
    thumbnail_timer_->stop();
    background_thumbnail_timer_->stop();
    thumbnail_pool_->clear();
    thumbnail_inflight_.clear();
    background_thumbnail_inflight_.clear();
    thumbnail_loaded_.clear();
}

void MainWindow::scheduleVisibleThumbnails() {
    if (view_mode_ == 1) scheduleVisibleThumbnails(thumbnails_);
    else if (view_mode_ == 2) scheduleVisibleThumbnails(keyword_grid_);
}

void MainWindow::scheduleVisibleThumbnails(QListWidget* target) {
    if (!target || target->count() == 0 || !target->isVisible()) return;
    target->doItemsLayout();
    const QRect area = target->viewport()->rect();
    QModelIndex first = target->indexAt(area.topLeft() + QPoint(6, 6));
    QModelIndex last = target->indexAt(area.bottomRight() - QPoint(6, 6));
    int firstRow = first.isValid() ? first.row() : 0;
    int lastRow = last.isValid() ? last.row() :
        std::min(target->count() - 1, firstRow + 40);
    const int visibleFirstRow = firstRow;
    const int visibleLastRow = lastRow;
    // A small prefetch margin keeps normal scrolling smooth without rendering
    // thousands of off-screen files.
    firstRow = std::max(0, firstRow - 20);
    lastRow = std::min(target->count() - 1, lastRow + 30);
    for (int row = firstRow; row <= lastRow; ++row) {
        const int priority =
            row >= visibleFirstRow && row <= visibleLastRow ? 10 : 5;
        requestThumbnail(target->item(row)->data(Qt::UserRole).toString(),
                         target, priority);
    }
}

void MainWindow::scheduleNextBackgroundThumbnail() {
    if (view_mode_ == 0 || background_thumbnail_cursor_ >= filtered_.size()) return;

    // Background jobs use fewer than half the pool, reserving most workers for
    // newly visible high-priority items even while the cache is being filled.
    if (background_thumbnail_inflight_.size() >= max_background_thumbnails_) {
        background_thumbnail_timer_->start(background_thumbnail_delay_ms);
        return;
    }

    QListWidget* target = view_mode_ == 1 ? thumbnails_ : keyword_grid_;
    constexpr int cached_scan_batch = 32;
    int scanned = 0;
    while (background_thumbnail_cursor_ < filtered_.size() &&
           scanned < cached_scan_batch &&
           background_thumbnail_inflight_.size() < max_background_thumbnails_) {
        const QString path = filtered_.at(background_thumbnail_cursor_++);
        ++scanned;
        requestThumbnail(path, target, -10, true);
    }

    if (background_thumbnail_cursor_ < filtered_.size() &&
        background_thumbnail_inflight_.size() < max_background_thumbnails_)
        background_thumbnail_timer_->start(25);
}

QString MainWindow::thumbnailPath(const QString& path) const {
    const QFileInfo info(path);
    const QByteArray key = ("thumb-v2-thin|" + path + '|' +
        QString::number(info.lastModified().toMSecsSinceEpoch()) +
        '|' + QString::number(info.size()) + '|' + line_color_.name()).toUtf8();
    const auto digest = QCryptographicHash::hash(key, QCryptographicHash::Sha256).toHex();
    const auto dir = thumbnailCacheDirectory();
    if (!QDir().mkpath(dir)) return {};
    return dir + '/' + QString::fromLatin1(digest) + ".png";
}

void MainWindow::requestThumbnail(const QString& path, QListWidget* target,
                                  int priority, bool background) {
    if (thumbnail_loaded_.contains(path)) return;
    const auto cached = thumbnailPath(path);
    QImage image(cached);
    if (!image.isNull()) {
        thumbnail_loaded_.insert(path);
        applyThumbnail(path, image, target);
        return;
    }
    if (thumbnail_inflight_.contains(path)) return;
    thumbnail_inflight_.insert(path);
    if (background) background_thumbnail_inflight_.insert(path);
    auto* item = target == thumbnails_ ? thumbnail_items_.value(path) : keyword_items_.value(path);
    if (item) {
        QImage blank(thumbnail_size, thumbnail_size, QImage::Format_ARGB32_Premultiplied);
        blank.fill(QColor(245, 245, 245));
        item->setIcon(QPixmap::fromImage(blank));
    }
    const auto generation = thumbnail_generation_;
    auto* task = new ThumbnailTask(path, cached, line_color_, this,
        [this, path, target = QPointer<QListWidget>(target), generation](const QImage& rendered) {
            if (generation != thumbnail_generation_) return;
            thumbnail_inflight_.remove(path);
            background_thumbnail_inflight_.remove(path);
            if (!rendered.isNull()) thumbnail_loaded_.insert(path);
            if (!rendered.isNull() && target) applyThumbnail(path, rendered, target);
            if (view_mode_ != 0)
                background_thumbnail_timer_->start(background_thumbnail_delay_ms);
        });
    thumbnail_pool_->start(task, priority);
}

void MainWindow::applyThumbnail(const QString& path, const QImage& image, QListWidget* target) {
    auto* item = target == thumbnails_ ? thumbnail_items_.value(path) : keyword_items_.value(path);
    if (item) item->setIcon(QPixmap::fromImage(image));
}

QImage MainWindow::renderThumbnail(const QString& path, const QColor& color) {
    try {
        const Pattern pattern = load_pattern(path.toStdString());
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
    if (!info.isFile() || !is_supported_file(info.absoluteFilePath().toStdString())) return;
    const auto canonical = info.canonicalFilePath();
    if (!library_.contains(canonical, Qt::CaseInsensitive)) library_ << canonical;
}

void MainWindow::addFiles() {
    const auto paths = QFileDialog::getOpenFileNames(this, tr("Add quilting patterns"), {},
                                                     tr("Patterns (*.pat *.qli *.svg)"));
    for (const auto& path : paths) addPath(path);
    saveLibrary(); refreshList();
}

void MainWindow::addFolder() {
    const auto folder = QFileDialog::getExistingDirectory(this, tr("Add pattern folder"));
    if (folder.isEmpty()) return;
    QDirIterator it(folder, {"*.pat", "*.qli", "*.svg"}, QDir::Files, QDirIterator::Subdirectories);
    while (it.hasNext()) addPath(it.next());
    saveLibrary(); refreshList();
}

void MainWindow::openFile(const QString& path) {
    if (path == current_) return;
    try {
        Pattern pattern = load_pattern(path.toStdString());
        const auto bounds = pattern_bounds(pattern);
        std::size_t stitch = 0, travel = 0;
        for (const auto& move : pattern.moves) move.gcode == 0 ? ++travel : ++stitch;
        file_name_->setText(QFileInfo(path).fileName());
        file_path_->setText(path);
        dimensions_->setText(QString("%1 × %2 PAT units").arg(bounds.width(), 0, 'f', 3)
                                                         .arg(bounds.height(), 0, 'f', 3));
        statistics_->setText(tr("%1 blocks · %2 stitch · %3 travel")
                             .arg(pattern.moves.size()).arg(stitch).arg(travel));
        QStringList lines;
        for (const auto& c : pattern.comments) lines << QString::fromStdString(c);
        comments_->setPlainText(lines.join('\n'));
        keyword_edit_->setText(keywords_.value(path).toString());
        canvas_->setPattern(std::move(pattern));
        current_ = path;
        setWindowTitle(tr("Innova PAT / QLI Viewer — %1").arg(QFileInfo(path).fileName()));
        statusBar()->showMessage(tr("Loaded %1").arg(QFileInfo(path).fileName()));
    } catch (const std::exception& e) {
        QMessageBox::critical(this, tr("Could not load pattern"), QString::fromUtf8(e.what()));
    }
}

void MainWindow::removeSelected() {
    QStringList paths;
    QListWidget* active = view_mode_ == 0 ? files_ : view_mode_ == 1 ? thumbnails_ : keyword_grid_;
    for (auto* item : active->selectedItems()) paths << item->data(Qt::UserRole).toString();
    if (paths.isEmpty() && !current_.isEmpty()) paths << current_;
    for (const auto& path : paths) { library_.removeAll(path); keywords_.remove(path); }
    saveLibrary(); refreshList();
    statusBar()->showMessage(tr("Removed %1 file(s) from the library; disk files were not deleted.")
                             .arg(paths.size()));
}

void MainWindow::purgeMissing() {
    int removed = 0;
    for (auto it = library_.begin(); it != library_.end();)
        if (!QFileInfo::exists(*it)) { keywords_.remove(*it); it = library_.erase(it); ++removed; }
        else ++it;
    saveLibrary(); refreshList();
    statusBar()->showMessage(tr("Purged %1 missing file(s) from the database.").arg(removed));
}

void MainWindow::resetDatabase() {
    if (library_.isEmpty()) { QMessageBox::information(this, tr("Reset Database"), tr("The database is already empty.")); return; }
    if (QMessageBox::warning(this, tr("Reset Database"),
        tr("Remove all %1 file entries from the database?\n\nFiles on disk will not be deleted.\nThis cannot be undone.")
        .arg(library_.size()), QMessageBox::Yes | QMessageBox::No, QMessageBox::No) != QMessageBox::Yes) return;
    library_.clear(); keywords_.clear(); current_.clear();
    canvas_->clear(); file_name_->setText("—"); file_path_->setText("—");
    dimensions_->setText("—"); statistics_->clear(); comments_->clear(); keyword_edit_->clear();
    search_->clear(); saveLibrary(); refreshList();
}

void MainWindow::clearThumbnailCache() {
    QDir dir(thumbnailCacheDirectory());
    int count = 0;
    for (const auto& file : dir.entryList(QDir::Files)) if (dir.remove(file)) ++count;
    populateView();
    statusBar()->showMessage(tr("Thumbnail cache cleared (%1 entries removed).").arg(count));
}

void MainWindow::mergeDuplicates() {
    QHash<QString, QStringList> groups;
    for (const auto& path : library_) {
        const auto digest = fileDigest(path);
        if (!digest.isEmpty()) groups[QFileInfo(path).suffix().toLower() + ':' + digest] << path;
    }
    int sets = 0, removed = 0;
    QStringList report;
    for (auto paths : groups) {
        if (paths.size() < 2) continue;
        paths.sort(Qt::CaseInsensitive); ++sets;
        const auto keeper = paths.first();
        report << tr("KEPT: %1").arg(keeper);
        QString combined = keywords_.value(keeper).toString();
        for (int i = 1; i < paths.size(); ++i) {
            combined += ", " + keywords_.value(paths[i]).toString();
            library_.removeAll(paths[i]); keywords_.remove(paths[i]); ++removed;
            report << tr("REMOVED: %1").arg(paths[i]);
        }
        keywords_[keeper] = normalizeKeywords(combined);
        report << "";
    }
    saveLibrary(); refreshList();
    if (!sets) QMessageBox::information(this, tr("Merge Duplicates"), tr("No byte-identical files of the same type were found."));
    else QMessageBox::information(this, tr("Duplicate Files Merged"),
        tr("Merged %1 duplicate set(s); %2 library entries removed.\n\n%3")
        .arg(sets).arg(removed).arg(report.join('\n')));
}

void MainWindow::saveCurrentKeywords() {
    if (current_.isEmpty()) return;
    const auto normalized = normalizeKeywords(keyword_edit_->text());
    keyword_edit_->setText(normalized); keywords_[current_] = normalized;
    saveLibrary(); refreshList();
    statusBar()->showMessage(tr("Keywords saved for '%1'.").arg(QFileInfo(current_).fileName()));
}

void MainWindow::applyBulkKeywords() {
    const auto add = normalizeKeywords(bulk_keyword_edit_->text());
    if (add.isEmpty() || keyword_grid_->selectedItems().isEmpty()) return;
    for (auto* item : keyword_grid_->selectedItems()) {
        const auto path = item->data(Qt::UserRole).toString();
        keywords_[path] = normalizeKeywords(keywords_.value(path).toString() + ", " + add);
    }
    saveLibrary(); refreshList();
    statusBar()->showMessage(tr("Keywords applied to selected files."));
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

void MainWindow::convertCurrent(const QString& extension) {
    if (current_.isEmpty()) return;
    QString suggested = QFileInfo(current_).path() + '/' + QFileInfo(current_).completeBaseName() + extension;
    const auto output = QFileDialog::getSaveFileName(this, tr("Save converted pattern"), suggested,
        extension == ".pat" ? tr("Innova PAT (*.pat)") : tr("Statler QLI (*.qli)"));
    if (output.isEmpty()) return;
    try { convert_file(current_.toStdString(), output.toStdString()); addPath(output); saveLibrary();
          refreshList(); openFile(QFileInfo(output).canonicalFilePath()); }
    catch (const std::exception& e) { QMessageBox::critical(this, tr("Conversion failed"), e.what()); }
}

void MainWindow::convertSourceDialog(const QString& sourceExt, const QString& outputExt) {
    const auto sources = QFileDialog::getOpenFileNames(this, tr("Select file(s) to convert"), {},
        sourceExt == ".svg" ? tr("SVG Files (*.svg)") : tr("QLI Files (*.qli)"));
    for (const auto& source : sources) {
        auto output = source; output.chop(sourceExt.size()); output += outputExt;
        if (sources.size() == 1) {
            output = QFileDialog::getSaveFileName(this, tr("Save converted file"), output,
                outputExt == ".pat" ? tr("Innova PAT (*.pat)") : tr("Statler QLI (*.qli)"));
            if (output.isEmpty()) return;
        } else if (QFileInfo::exists(output) && QMessageBox::question(this, tr("Overwrite"),
                   tr("%1 exists. Overwrite?").arg(output)) != QMessageBox::Yes) continue;
        try { convert_file(source.toStdString(), output.toStdString()); addPath(output); }
        catch (const std::exception& e) { QMessageBox::warning(this, tr("Conversion failed"), e.what()); }
    }
    saveLibrary(); refreshList();
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
    int converted = 0, skipped = 0, failed = 0;
    QDirIterator it(folder, {"*" + sourceExt}, QDir::Files,
                    recursive ? QDirIterator::Subdirectories : QDirIterator::NoIteratorFlags);
    while (it.hasNext()) {
        const auto source = it.next();
        auto output = source; output.chop(sourceExt.size()); output += outputExt;
        if (QFileInfo::exists(output) && QMessageBox::question(this, tr("Overwrite"),
            tr("%1 exists. Overwrite?").arg(output)) != QMessageBox::Yes) { ++skipped; continue; }
        try { convert_file(source.toStdString(), output.toStdString()); addPath(output); ++converted; }
        catch (...) { ++failed; }
    }
    saveLibrary(); refreshList();
    QMessageBox::information(this, tr("Folder Conversion"),
        tr("Converted: %1\nSkipped: %2\nFailed: %3").arg(converted).arg(skipped).arg(failed));
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
        QDirIterator it(path, {"*.pat", "*.qli", "*.svg"}, QDir::Files, QDirIterator::Subdirectories);
        while (it.hasNext()) addPath(it.next());
        saveLibrary(); refreshList();
    } else if (info.isFile()) {
        addPath(path); saveLibrary(); refreshList(); openFile(info.canonicalFilePath());
    }
}

void MainWindow::resizeEvent(QResizeEvent* event) {
    QMainWindow::resizeEvent(event);
    if (thumbnail_timer_ && view_mode_ != 0) thumbnail_timer_->start(60);
}

} // namespace qpv
