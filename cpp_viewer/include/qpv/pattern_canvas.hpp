#pragma once

#include "qpv/pattern.hpp"

#include <QColor>
#include <QPainterPath>
#include <QPoint>
#include <QTransform>
#include <QWidget>

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
    void resizeEvent(QResizeEvent*) override;

private:
    Pattern pattern_;
    QPainterPath stitches_;
    QPainterPath travel_;
    QRectF geometry_;
    QPointF start_;
    QPointF end_;
    double zoom_{1.0};
    QPointF pan_;
    QPoint drag_start_;
    QPointF pan_start_;
    bool dragging_{};
    bool show_travel_{};
    bool show_markers_{};
    double line_width_{1.0};
    QColor line_color_{Qt::black};

    void rebuildPaths();
    [[nodiscard]] QTransform viewTransform() const;
    void zoomAt(double factor, QPointF widget_point);
};

} // namespace qpv
