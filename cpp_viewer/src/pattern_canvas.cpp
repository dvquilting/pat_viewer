#include "qpv/pattern_canvas.hpp"

#include <QKeyEvent>
#include <QMouseEvent>
#include <QPainter>
#include <QWheelEvent>

#include <algorithm>

namespace qpv {

PatternCanvas::PatternCanvas(QWidget* parent) : QWidget(parent) {
    setFocusPolicy(Qt::StrongFocus);
    setMinimumSize(300, 240);
    setAutoFillBackground(true);
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

void PatternCanvas::setShowTravel(bool value) { show_travel_ = value; update(); }
void PatternCanvas::setShowMarkers(bool value) { show_markers_ = value; update(); }
void PatternCanvas::setLineWidth(double value) { line_width_ = value; update(); }
void PatternCanvas::setLineColor(QColor value) { line_color_ = value; update(); }

void PatternCanvas::rebuildPaths() {
    stitches_ = {};
    travel_ = {};
    bool have = false;
    Point current{};
    for (const auto& move : pattern_.moves) {
        auto& target = move.gcode == 0 ? travel_ : stitches_;
        if (!have) {
            current = move.end;
            start_ = {current.x, -current.y};
            end_ = start_;
            have = true;
            continue;
        }
        target.moveTo(current.x, -current.y);
        if ((move.gcode == 2 || move.gcode == 3) && move.center) {
            const auto sampled = sample_arc(current, move, 1.5);
            for (std::size_t i = 1; i < sampled.size(); ++i)
                target.lineTo(sampled[i].x, -sampled[i].y);
        } else {
            target.lineTo(move.end.x, -move.end.y);
        }
        current = move.end;
        end_ = {current.x, -current.y};
    }
    geometry_ = stitches_.boundingRect().united(travel_.boundingRect());
    if (geometry_.width() < 1e-9) geometry_.adjust(-.5, 0, .5, 0);
    if (geometry_.height() < 1e-9) geometry_.adjust(0, -.5, 0, .5);
}

void PatternCanvas::fitToView() {
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

void PatternCanvas::paintEvent(QPaintEvent*) {
    QPainter painter(this);
    painter.fillRect(rect(), palette().color(QPalette::Base));
    painter.setRenderHint(QPainter::Antialiasing, true);
    painter.setTransform(viewTransform());

    if (show_travel_) {
        QPen pen(QColor(120, 120, 120), line_width_, Qt::DashLine);
        pen.setCosmetic(true);
        painter.setPen(pen);
        painter.drawPath(travel_);
    }
    QPen stitch_pen(line_color_, line_width_);
    stitch_pen.setCosmetic(true);
    painter.setPen(stitch_pen);
    painter.drawPath(stitches_);

    if (show_markers_ && !pattern_.moves.empty()) {
        const double radius = 5.0 / zoom_;
        painter.setPen(Qt::NoPen);
        painter.setBrush(QColor(30, 180, 70));
        painter.drawEllipse(start_, radius, radius);
        painter.setBrush(QColor(220, 50, 50));
        painter.drawEllipse(end_, radius, radius);
    }
}

void PatternCanvas::zoomAt(double factor, QPointF point) {
    const QPointF before = viewTransform().inverted().map(point);
    zoom_ = std::clamp(zoom_ * factor, 0.001, 1e7);
    pan_ = point - before * zoom_;
    update();
}

void PatternCanvas::wheelEvent(QWheelEvent* event) {
    zoomAt(event->angleDelta().y() > 0 ? 1.2 : 1.0 / 1.2, event->position());
}
void PatternCanvas::mousePressEvent(QMouseEvent* event) {
    if (event->button() == Qt::LeftButton) {
        dragging_ = true; drag_start_ = event->pos(); pan_start_ = pan_;
        setCursor(Qt::ClosedHandCursor);
    }
}
void PatternCanvas::mouseMoveEvent(QMouseEvent* event) {
    if (dragging_) { pan_ = pan_start_ + event->pos() - drag_start_; update(); }
}
void PatternCanvas::mouseReleaseEvent(QMouseEvent*) {
    dragging_ = false; unsetCursor();
}
void PatternCanvas::keyPressEvent(QKeyEvent* event) {
    if (event->key() == Qt::Key_F || event->key() == Qt::Key_Home) fitToView();
    else if (event->key() == Qt::Key_Plus || event->key() == Qt::Key_Equal)
        zoomAt(1.2, rect().center());
    else if (event->key() == Qt::Key_Minus) zoomAt(1.0 / 1.2, rect().center());
    else QWidget::keyPressEvent(event);
}
void PatternCanvas::resizeEvent(QResizeEvent*) {
    if (!pattern_.moves.empty() && zoom_ == 1.0) fitToView();
}

} // namespace qpv
