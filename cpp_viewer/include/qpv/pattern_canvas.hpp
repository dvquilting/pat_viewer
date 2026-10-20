#pragma once

#include "qpv/pattern.hpp"

#include <QColor>
#include <QImage>
#include <QPoint>
#include <QPolygonF>
#include <QTransform>
#include <QWidget>

#include <memory>
#include <vector>

class QTimer;

namespace qpv {

class PatternCanvas final : public QWidget {
public:
    explicit PatternCanvas(QWidget* parent = nullptr);

    void setPattern(Pattern pattern);
    void clear();
    void setShowTravel(bool value);
    void setShowMarkers(bool value);
    void setLineWidth(double pixels);
    void setLineColor(QColor color);
    void fitToView();

protected:
    void paintEvent(QPaintEvent*) override;
    void wheelEvent(QWheelEvent*) override;
    void mousePressEvent(QMouseEvent*) override;
    void mouseMoveEvent(QMouseEvent*) override;
    void mouseReleaseEvent(QMouseEvent*) override;
    void keyPressEvent(QKeyEvent*) override;
    void changeEvent(QEvent*) override;
    void resizeEvent(QResizeEvent*) override;

private:
    // A continuous stretch of stitches or travel, in canvas coordinates
    // (y flipped). Stretches are split into short runs so off-screen parts are
    // skipped and wide pens, which Qt strokes slowly, stay affordable.
    struct Run {
        QPolygonF points;
        QRectF bounds;
        bool travel{};
    };
    using Runs = std::vector<Run>;
    struct Style {
        bool show_travel{};
        double line_width{};
        QColor line_color;
        QColor background;
    };

    Pattern pattern_;
    // Immutable once built, so background renders can share it.
    std::shared_ptr<const Runs> runs_;
    QRectF geometry_;
    QPointF start_;
    QPointF end_;
    double zoom_{1.0};
    QPointF pan_;
    QPoint drag_start_;
    QPointF pan_start_;
    bool dragging_{};
    bool user_view_{};  // zoomed or panned by the user, so resizing keeps the view
    bool show_travel_{};
    bool show_markers_{};
    double line_width_{1.0};
    QColor line_color_{Qt::black};

    // The last rendering and what it was drawn for. While the user drags or
    // zooms, or while a slow pattern is redrawn on a worker thread, this image
    // is shown moved or scaled to the current view.
    quint64 pattern_revision_{};
    quint64 style_revision_{};
    QImage cache_;
    QTransform cache_transform_;
    quint64 cache_pattern_revision_{~0ull};
    quint64 cache_style_revision_{};
    bool interacting_{};
    bool render_in_flight_{};
    double last_render_ms_{};
    QTimer* settle_timer_{};

    void rebuildPaths();
    void styleChanged();
    [[nodiscard]] Style style() const;
    [[nodiscard]] QSize cachePixels() const;
    void renderNow(const QTransform& view);
    void requestRender(const QTransform& view);
    static QImage renderRuns(const Runs& runs, const Style& style, const QTransform& view,
                             QSize pixels, qreal ratio);
    void beginInteraction();
    [[nodiscard]] QTransform viewTransform() const;
    void zoomAt(double factor, QPointF widget_point);
};

} // namespace qpv
