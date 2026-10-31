"""Exact side-view cat traced from the reference sticker.

The silhouette was traced once from the source artwork and stored as a handful
of smooth Catmull-Rom point rings (body, curled tail, four stub legs).  Drawing
these rings instead of built-up ellipses keeps the outline identical to the
source: soft rump, two round ears, chest sweep, and four short legs.

All coordinates are in the pet canvas space (176 x 182, feet around y=172).
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen

# ------------------------------------------------------------------ geometry

BODY = [
    (31.479, 130.748), (31.864, 125.629), (32.881, 119.755), (33.851, 117.280),
    (33.870, 114.973), (42.453, 101.534), (45.120, 100.755), (51.716, 97.704),
    (59.343, 95.683), (67.467, 94.849), (74.283, 94.774), (77.114, 94.160),
    (87.868, 93.888), (91.501, 92.697), (94.797, 89.791), (101.814, 78.203),
    (103.286, 74.162), (108.433, 65.527), (110.805, 63.075), (112.521, 62.035),
    (116.853, 62.405), (121.128, 65.710), (126.200, 71.663), (130.395, 72.924),
    (134.436, 71.546), (138.496, 66.647), (141.276, 64.604), (144.313, 64.346),
    (147.070, 65.724), (148.321, 67.229), (153.858, 78.231), (157.050, 82.619),
    (160.594, 89.411), (162.680, 96.972), (163.210, 101.266), (163.177, 106.718),
    (161.897, 112.330), (159.614, 116.938), (157.298, 120.135), (150.154, 125.394),
    (145.363, 127.494), (141.637, 129.970), (137.586, 134.878), (135.102, 143.419),
    (134.999, 148.083), (135.468, 150.966), (134.919, 151.515), (92.251, 151.552),
    (88.974, 150.005), (83.518, 149.260), (81.047, 149.546), (78.075, 151.397),
    (76.857, 151.557), (33.260, 151.510), (32.501, 150.891), (32.042, 149.283),
    (31.390, 144.422), (31.451, 131.132),
]

# Outer silhouette only: the belly run (indices 44-57) is left unstroked so no
# line is drawn between the legs, exactly like the flat source artwork.
BODY_TOP = BODY[5:47]

TAIL = [
    (13.127, 83.922), (14.434, 79.717), (16.788, 77.246), (20.810, 75.812),
    (24.433, 76.956), (26.913, 79.989), (29.262, 89.355), (32.829, 95.435),
    (35.572, 98.205), (38.914, 100.376), (42.008, 101.323), (43.588, 101.248),
    (43.897, 101.866), (34.053, 116.956), (33.420, 115.058), (27.616, 111.903),
    (23.477, 108.462), (20.219, 104.829), (17.735, 101.112), (14.439, 94.071),
    (13.052, 88.567), (13.028, 84.512),
]

LEG_A = [
    (32.356, 149.264), (55.232, 149.293), (55.335, 152.316), (51.932, 157.182),
    (50.169, 161.621), (49.358, 169.769), (47.094, 171.639), (41.956, 171.986),
    (39.650, 171.536), (36.772, 168.901), (35.548, 166.679), (33.950, 161.387),
    (32.229, 149.513),
]

LEG_B = [
    (58.513, 149.293), (79.346, 149.288), (79.379, 150.174), (78.094, 151.646),
    (77.161, 154.158), (77.161, 158.143), (78.432, 163.754), (77.588, 166.300),
    (75.853, 168.011), (73.636, 168.550), (69.792, 168.269), (66.595, 166.642),
    (64.612, 164.490), (61.926, 159.596), (58.485, 152.011), (58.345, 149.583),
]

LEG_C = [
    (91.131, 149.293), (111.968, 149.293), (112.198, 151.754), (110.693, 158.518),
    (110.379, 167.162), (109.732, 169.272), (107.186, 171.550), (103.506, 171.995),
    (99.930, 171.437), (96.555, 168.611), (95.279, 165.751), (92.317, 153.554),
    (90.929, 150.807), (90.962, 149.583),
]

LEG_D = [
    (116.857, 149.293), (134.943, 149.288), (138.562, 159.451), (138.660, 163.745),
    (137.094, 166.314), (135.355, 167.331), (131.455, 167.247), (128.586, 165.981),
    (124.461, 162.526), (116.913, 154.224), (116.642, 149.766),
]

LEGS = {"a": LEG_A, "b": LEG_B, "c": LEG_C, "d": LEG_D}
LEG_ORDER = ("a", "b", "c", "d")
LEG_PIVOTS = {
    "a": QPointF(43.8, 147.0),
    "b": QPointF(68.9, 147.0),
    "c": QPointF(101.5, 147.0),
    "d": QPointF(125.9, 147.0),
}
FRONT_LEGS = {"c", "d"}
# Stroke starts just below the body so the leg tops melt into the belly.
LEG_STROKE_Y = 150.4

TAIL_PIVOT = QPointF(40.3, 112.3)

EYE_LEFT = QPointF(136.58, 101.71)
EYE_RIGHT = QPointF(158.06, 101.83)
EYE_W = 4.2
EYE_H = 5.0
MOUTH_CENTER = QPointF(148.75, 106.88)
MOUTH_W = 9.19
MOUTH_H = 2.07
FACE_CENTER = QPointF(147.3, 104.2)
CHEEK_LEFT = QPointF(128.0, 112.0)
CHEEK_RIGHT = QPointF(150.0, 112.0)
MOUTH_ITEM_X = 151.0
MOUTH_ITEM_Y = 124.0


def _points(raw):
    return [QPointF(x, y) for x, y in raw]


def _closed_path(points) -> QPainterPath:
    n = len(points)
    path = QPainterPath(points[0])
    for i in range(n):
        p0 = points[(i - 1) % n]
        p1 = points[i]
        p2 = points[(i + 1) % n]
        p3 = points[(i + 2) % n]
        c1 = QPointF(
            p1.x() + (p2.x() - p0.x()) / 6.0,
            p1.y() + (p2.y() - p0.y()) / 6.0,
        )
        c2 = QPointF(
            p2.x() - (p3.x() - p1.x()) / 6.0,
            p2.y() - (p3.y() - p1.y()) / 6.0,
        )
        path.cubicTo(c1, c2, p2)
    path.closeSubpath()
    return path


def _open_path(points) -> QPainterPath:
    n = len(points)
    path = QPainterPath(points[0])
    for i in range(1, n - 1):
        p0 = points[i - 1]
        p1 = points[i]
        p2 = points[i + 1]
        p3 = points[min(i + 2, n - 1)]
        c1 = QPointF(
            p1.x() + (p2.x() - p0.x()) / 6.0,
            p1.y() + (p2.y() - p0.y()) / 6.0,
        )
        c2 = QPointF(
            p2.x() - (p3.x() - p1.x()) / 6.0,
            p2.y() - (p3.y() - p1.y()) / 6.0,
        )
        path.cubicTo(c1, c2, p2)
    return path


BODY_POINTS = _points(BODY)
BODY_PATH = _closed_path(BODY_POINTS)
BODY_OUTLINE_PATH = _open_path(_points(BODY_TOP))
TAIL_POINTS = _points(TAIL)
TAIL_PATH = _closed_path(TAIL_POINTS)
# The ring from the lower base around the tip to the upper base.  The straight
# cut across the rump is the only edge that is never stroked, so no seam line
# appears where the tail grows out of the body.
TAIL_STROKE_PATH = _open_path(
    TAIL_POINTS[13:] + TAIL_POINTS[:13]
)
LEG_PATHS = {name: _closed_path(_points(raw)) for name, raw in LEGS.items()}
LEG_STROKE_PATHS = {
    name: _open_path([p for p in _points(raw) if p.y() > LEG_STROKE_Y])
    for name, raw in LEGS.items()
}


def outline_pen(palette, width: float = 0.85) -> QPen:
    edge = QColor(palette.soft_edge)
    edge.setAlpha(min(edge.alpha(), 92))
    return QPen(edge, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin)


# ------------------------------------------------------------------ painting

def draw_tail(p: QPainter, palette, angle: float = 0.0) -> None:
    p.save()
    p.translate(TAIL_PIVOT)
    p.rotate(angle)
    p.translate(-TAIL_PIVOT)
    p.setBrush(palette.base)
    p.setPen(Qt.PenStyle.NoPen)
    p.drawPath(TAIL_PATH)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(outline_pen(palette))
    p.drawPath(TAIL_STROKE_PATH)
    p.restore()


def _draw_raised_limb(p: QPainter, palette, leg: str, dx: float, lift: float) -> None:
    pivot = LEG_PIVOTS[leg]
    if leg in FRONT_LEGS:
        wrist = QPointF(min(166.5, pivot.x() + 10.0 + lift * 1.4),
                        pivot.y() - lift * 1.15)
        shoulder = QPointF(pivot.x() + 2.0, pivot.y() - 4.0)
        bow = 4.0
    else:
        wrist = QPointF(max(4.0, pivot.x() - 12.0 - lift * 1.1),
                        pivot.y() - lift)
        shoulder = QPointF(pivot.x() - 2.0, pivot.y() - 4.0)
        bow = -4.0
    wrist.setX(wrist.x() + dx * 0.4)
    control = QPointF(
        (shoulder.x() + wrist.x()) / 2.0 + bow,
        (shoulder.y() + wrist.y()) / 2.0 + 3.0,
    )
    limb = QPainterPath(shoulder)
    limb.quadTo(control, wrist)

    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(palette.edge, 9.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawPath(limb)
    p.setPen(QPen(palette.base, 7.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawPath(limb)

    tilt = -12.0 if leg in FRONT_LEGS else 12.0
    p.save()
    p.translate(wrist)
    p.rotate(tilt)
    paw = QRectF(-7.0, -5.0, 14.0, 10.0)
    p.setBrush(palette.base)
    p.setPen(outline_pen(palette))
    p.drawRoundedRect(paw, 4.6, 4.6)
    p.restore()


def _is_raised(offset) -> bool:
    return -offset.y() > 12.0


def draw_leg(p: QPainter, palette, leg: str, offset=QPointF(0, 0)) -> None:
    dx = offset.x()
    dy = offset.y()
    lift = max(0.0, -dy)

    angle = -lift * 2.1 if leg in FRONT_LEGS else lift * 1.6
    pivot = LEG_PIVOTS[leg]
    p.save()
    p.translate(dx * 0.55, max(0.0, dy) * 0.3)
    if abs(angle) > 0.05:
        p.translate(pivot)
        p.rotate(angle)
        p.translate(-pivot)
    p.setBrush(palette.base)
    p.setPen(Qt.PenStyle.NoPen)
    p.drawPath(LEG_PATHS[leg])
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(outline_pen(palette))
    p.drawPath(LEG_STROKE_PATHS[leg])
    p.restore()


def draw_body(p: QPainter, palette) -> None:
    p.setBrush(palette.base)
    p.setPen(Qt.PenStyle.NoPen)
    p.drawPath(BODY_PATH)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(outline_pen(palette))
    p.drawPath(BODY_OUTLINE_PATH)


def draw_cat_base(p: QPainter, palette, tail_angle: float = 0.0, poses=None) -> None:
    """Tail behind, standing legs under the belly, fused body, then raised paws.

    Raised paws sit in front of the chest so the greeting wave stays visible;
    standing legs sit behind the body so their tops simply melt into the belly.
    """
    poses = poses or {}
    draw_tail(p, palette, tail_angle)
    raised = []
    for leg in LEG_ORDER:
        offset = poses.get(leg, QPointF(0, 0))
        if _is_raised(offset):
            raised.append((leg, offset.x(), -offset.y()))
        else:
            draw_leg(p, palette, leg, offset)
    for leg, dx, lift in raised:
        _draw_raised_limb(p, palette, leg, dx, lift)
    draw_body(p, palette)


def draw_idle_face(p: QPainter, palette) -> None:
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(palette.ink)
    p.drawEllipse(QRectF(EYE_LEFT.x() - EYE_W / 2, EYE_LEFT.y() - EYE_H / 2,
                         EYE_W, EYE_H))
    p.drawEllipse(QRectF(EYE_RIGHT.x() - EYE_W / 2, EYE_RIGHT.y() - EYE_H / 2,
                         EYE_W, EYE_H))
    draw_mouth(p, palette, MOUTH_CENTER, 1.0)


def draw_mouth(p: QPainter, palette, center: QPointF, scale: float = 1.0,
               width: float = 1.25) -> None:
    half = MOUTH_W / 2 * scale
    height = MOUTH_H * scale
    path = QPainterPath(QPointF(center.x() - half, center.y()))
    path.cubicTo(
        QPointF(center.x() - half * 0.50, center.y() + height * 1.5),
        QPointF(center.x() - half * 0.26, center.y() + height * 1.5),
        QPointF(center.x(), center.y() + height * 0.55),
    )
    path.cubicTo(
        QPointF(center.x() + half * 0.26, center.y() + height * 1.5),
        QPointF(center.x() + half * 0.50, center.y() + height * 1.5),
        QPointF(center.x() + half, center.y()),
    )
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(palette.ink, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawPath(path)
