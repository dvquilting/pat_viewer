#include "qpv/pattern_canvas.hpp"

#include <QElapsedTimer>
#include <QKeyEvent>
#include <QMouseEvent>
#include <QPainter>
#include <QPointer>
#include <QThreadPool>
#include <QTimer>
#include <QWheelEvent>

#include <algorithm>
#include <cmath>

namespace qpv {
namespace {

// Points per run. Short runs let culling skip most of a zoomed-in pattern and
// make wide pens far cheaper: Qt's general stroker (used for cosmetic pens
// wider than 1 px) slows down sharply on long self-overlapping polylines.
constexpr qsizetype max_run_points = 64;
// Patterns that take longer than this to draw are redrawn on a worker thread,
// and moved as an image while the user drags or zooms.
constexpr double live_render_budget_ms = 12.0;
constexpr int settle_delay_ms = 120;

} // namespace

PatternCanvas::PatternCanvas(QWidget* parent)
    : QWidget(parent), runs_(std::make_shared<const Runs>()) {
    setFocusPolicy(Qt::StrongFocus);
    setMinimumSize(300, 240);
    setAttribute(Qt::WA_OpaquePaintEvent);
    settle_timer_ = new QTimer(this);
    settle_timer_->setSingleShot(true);
    settle_timer_->setInterval(settle_delay_ms);
    connect(settle_timer_, &QTimer::timeout, this, [this] {
        interacting_ = false;
        update();
    });
}

void PatternCanvas::setPattern(Pattern pattern) {
    pattern_ = std::move(pattern);
    rebuildPaths();
    fitToView();
}

void PatternCanvas::clear() {
    pattern_ = {};
    rebuildPaths();
    update();
}

void PatternCanvas::setShowTravel(bool value) { show_travel_ = value; styleChanged(); }
void PatternCanvas::setShowMarkers(bool value) { show_markers_ = value; update(); }
void PatternCanvas::setLineWidth(double value) { line_width_ = value; styleChanged(); }
void PatternCanvas::setLineColor(QColor value) { line_color_ = value; styleChanged(); }

void PatternCanvas::styleChanged() {
    ++style_revision_;
    update();
}

PatternCanvas::Style PatternCanvas::style() const {
    return {show_travel_, line_width_, line_color_, palette().color(QPalette::Base)};
}

void PatternCanvas::rebuildPaths() {
    ++pattern_revision_;
    auto runs = std::make_shared<Runs>();
    bool have = false;
    Point current{};
    auto extend = [&runs](bool travel, QPointF from, QPointF to) {
        // Continue the previous run when this move starts where it ended.
        if (runs->empty() || runs->back().travel != travel ||
            runs->back().points.size() >= max_run_points ||
            runs->back().points.constLast() != from) {
            runs->push_back({{from}, {}, travel});
        }
        runs->back().points << to;
    };
    for (const auto& move : pattern_.moves) {
        if (!have) {
            current = move.end;
            start_ = {current.x, -current.y};
            end_ = start_;
            have = true;
            continue;
        }
        const bool travel = move.gcode == 0;
        if ((move.gcode == 2 || move.gcode == 3) && move.center) {
            const auto sampled = sample_arc(current, move, 1.5);
            for (std::size_t i = 1; i < sampled.size(); ++i)
                extend(travel, {sampled[i - 1].x, -sampled[i - 1].y},
                       {sampled[i].x, -sampled[i].y});
        } else {
            extend(travel, {current.x, -current.y}, {move.end.x, -move.end.y});
        }
        current = move.end;
        end_ = {current.x, -current.y};
    }
    geometry_ = {};
    for (auto& run : *runs) {
        // Padded because QRectF::intersects() is false for a zero-height or
        // zero-width rectangle, which a straight run would have.
        run.bounds = run.points.boundingRect().adjusted(-1e-6, -1e-6, 1e-6, 1e-6);
        geometry_ = geometry_.isNull() ? run.bounds : geometry_.united(run.bounds);
    }
    if (geometry_.width() < 1e-9) geometry_.adjust(-.5, 0, .5, 0);
    if (geometry_.height() < 1e-9) geometry_.adjust(0, -.5, 0, .5);
    runs_ = std::move(runs);
}

void PatternCanvas::fitToView() {
    user_view_ = false;
    if (geometry_.isEmpty()) { zoom_ = 1.0; pan_ = {}; update(); return; }
    const double sx = std::max(1.0, width() - 40.0) / geometry_.width();
    const double sy = std::max(1.0, height() - 40.0) / geometry_.height();
    zoom_ = std::clamp(std::min(sx, sy), 0.01, 100000.0);
    pan_ = QPointF(width() / 2.0, height() / 2.0) -
           geometry_.center() * zoom_;
    update();
}

QTransform PatternCanvas::viewTransform() const {
    QTransform transform;
    transform.translate(pan_.x(), pan_.y());
    transform.scale(zoom_, zoom_);
    return transform;
}

QSize PatternCanvas::cachePixels() const {
    return (QSizeF(size()) * devicePixelRatioF()).toSize();
}

// Thread-safe: also runs on worker threads.
QImage PatternCanvas::renderRuns(const Runs& runs, const Style& style, const QTransform& view,
                                 QSize pixels, qreal ratio) {
    QImage image(pixels, QImage::Format_RGB32);
    image.setDevicePixelRatio(ratio);
    image.fill(style.background);
    QPainter painter(&image);
    painter.setRenderHint(QPainter::Antialiasing, true);
    painter.setTransform(view);
    const double margin = (style.line_width + 2.0) / view.m11();
    const QRectF visible = view.inverted()
        .mapRect(QRectF(QPointF(0, 0), QSizeF(pixels) / ratio))
        .adjusted(-margin, -margin, margin, margin);
    auto drawRuns = [&](bool travel) {
        for (const auto& run : runs)
            if (run.travel == travel && run.bounds.intersects(visible))
                painter.drawPolyline(run.points);
    };
    if (style.show_travel) {
        QPen pen(QColor(120, 120, 120), style.line_width, Qt::DashLine);
        pen.setCosmetic(true);
        painter.setPen(pen);
        drawRuns(true);
    }
    QPen stitch_pen(style.line_color, style.line_width);
    stitch_pen.setCosmetic(true);
    painter.setPen(stitch_pen);
    drawRuns(false);
    return image;
}

void PatternCanvas::renderNow(const QTransform& view) {
    QElapsedTimer timer;
    timer.start();
    cache_ = renderRuns(*runs_, style(), view, cachePixels(), devicePixelRatioF());
    cache_transform_ = view;
    cache_pattern_revision_ = pattern_revision_;
    cache_style_revision_ = style_revision_;
    last_render_ms_ = timer.nsecsElapsed() / 1e6;
}

void PatternCanvas::requestRender(const QTransform& view) {
    // One render at a time; when it lands, paintEvent asks again if the view
    // or style has changed meanwhile.
    if (render_in_flight_) return;
    render_in_flight_ = true;
    const QPointer<PatternCanvas> self(this);
    QThreadPool::globalInstance()->start(
        [self, runs = runs_, style = style(), view, pixels = cachePixels(),
         ratio = devicePixelRatioF(), pattern = pattern_revision_, styleRev = style_revision_] {
            QElapsedTimer timer;
            timer.start();
            QImage image = renderRuns(*runs, style, view, pixels, ratio);
            const double ms = timer.nsecsElapsed() / 1e6;
            if (!self) return;
            QMetaObject::invokeMethod(self, [self, image = std::move(image), view, pattern,
                                             styleRev, ms] {
                if (!self) return;
                self->render_in_flight_ = false;
                if (pattern == self->pattern_revision_) {
                    self->cache_ = image;
                    self->cache_transform_ = view;
                    self->cache_pattern_revision_ = pattern;
                    self->cache_style_revision_ = styleRev;
                    self->last_render_ms_ = ms;
                }
                self->update();
            }, Qt::QueuedConnection);
        });
}

void PatternCanvas::paintEvent(QPaintEvent*) {
    if (width() <= 0 || height() <= 0) return;
    QPainter painter(this);
    const QTransform view = viewTransform();
    const bool samePattern = !cache_.isNull() && cache_pattern_revision_ == pattern_revision_;
    const bool current = samePattern && cache_style_revision_ == style_revision_ &&
                         cache_transform_ == view && cache_.size() == cachePixels();
    if (current) {
        painter.drawImage(QPointF(0, 0), cache_);
    } else if (samePattern && (interacting_ || last_render_ms_ > live_render_budget_ms)) {
        // Show the last rendering moved or scaled to the current view.
        painter.fillRect(rect(), palette().color(QPalette::Base));
        painter.setTransform(cache_transform_.inverted() * view);
        painter.drawImage(QPointF(0, 0), cache_);
        painter.resetTransform();
        if (!interacting_) requestRender(view);
    } else {
        renderNow(view);
        painter.drawImage(QPointF(0, 0), cache_);
    }

    if (show_markers_ && !pattern_.moves.empty()) {
        painter.setRenderHint(QPainter::Antialiasing, true);
        painter.setPen(Qt::NoPen);
        painter.setBrush(QColor(30, 180, 70));
        painter.drawEllipse(view.map(start_), 5.0, 5.0);
        painter.setBrush(QColor(220, 50, 50));
        painter.drawEllipse(view.map(end_), 5.0, 5.0);
    }
}

void PatternCanvas::beginInteraction() {
    user_view_ = true;
    interacting_ = true;
    settle_timer_->start();
}

void PatternCanvas::zoomAt(double factor, QPointF point) {
    const QPointF before = viewTransform().inverted().map(point);
    zoom_ = std::clamp(zoom_ * factor, 0.001, 1e7);
    pan_ = point - before * zoom_;
    user_view_ = true;
    update();
}

void PatternCanvas::wheelEvent(QWheelEvent* event) {
    const int delta = event->angleDelta().y();
    if (delta == 0) {  // horizontal scrolling
        event->ignore();
        return;
    }
    beginInteraction();
    // Proportional to the wheel delta, so high-resolution touchpads that send
    // many small steps zoom smoothly rather than 1.2x per step.
    zoomAt(std::pow(1.2, delta / 120.0), event->position());
}
void PatternCanvas::mousePressEvent(QMouseEvent* event) {
    if (event->button() == Qt::LeftButton) {
        dragging_ = true; drag_start_ = event->pos(); pan_start_ = pan_;
        setCursor(Qt::ClosedHandCursor);
    }
}
void PatternCanvas::mouseMoveEvent(QMouseEvent* event) {
    if (dragging_) {
        beginInteraction();
        pan_ = pan_start_ + event->pos() - drag_start_;
        update();
    }
}
void PatternCanvas::mouseReleaseEvent(QMouseEvent*) {
    dragging_ = false; unsetCursor();
    if (interacting_) {
        settle_timer_->stop();
        interacting_ = false;
        update();
    }
}
void PatternCanvas::keyPressEvent(QKeyEvent* event) {
    if (event->key() == Qt::Key_F || event->key() == Qt::Key_Home) fitToView();
    else if (event->key() == Qt::Key_Plus || event->key() == Qt::Key_Equal)
        zoomAt(1.2, rect().center());
    else if (event->key() == Qt::Key_Minus) zoomAt(1.0 / 1.2, rect().center());
    else QWidget::keyPressEvent(event);
}
void PatternCanvas::changeEvent(QEvent* event) {
    if (event->type() == QEvent::PaletteChange || event->type() == QEvent::StyleChange)
        ++style_revision_;
    QWidget::changeEvent(event);
}
void PatternCanvas::resizeEvent(QResizeEvent*) {
    // Keep the pattern fitted until the user has zoomed or panned.
    if (!pattern_.moves.empty() && !user_view_) fitToView();
}

} // namespace qpv
