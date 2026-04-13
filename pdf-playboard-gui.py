#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
import os
import sys
from pathlib import Path
from typing import Literal, Union

import fitz
from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QFontMetricsF,
    QGuiApplication,
    QImage,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QDoubleSpinBox,
    QFrame,
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStyle,
    QStyleOptionGraphicsItem,
    QVBoxLayout,
    QWidget,
)

APP_NAME = "pdf-playboard"
BOARD_MARGIN = 240
DEFAULT_RENDER_DPI = 180
EMPTY_BOARD_SIZE = QSize(3200, 2200)
EMPTY_RESULT_SIZE = QSize(1200, 320)
DATA_KIND = 0
SELECTION_OUTLINE_COLOR = QColor(170, 170, 170)


@dataclass(frozen=True)
class RenderedPagePreview:
    image: QImage
    page_rect_pts: tuple[float, float, float, float]
    page_index: int = 0


@dataclass(frozen=True)
class SourceClipMetadata:
    source_pdf_path: str
    page_index: int
    clip_rect_pts: tuple[float, float, float, float]


@dataclass(frozen=True)
class SourceClipElement:
    kind: Literal["source-clip"]
    source_pdf_path: str
    page_index: int
    clip_rect_pts: tuple[float, float, float, float]
    target_rect_px: tuple[float, float, float, float]
    rotation_deg: float


@dataclass(frozen=True)
class RasterPatchElement:
    kind: Literal["raster-patch"]
    image_png: bytes
    target_rect_px: tuple[float, float, float, float]
    rotation_deg: float


@dataclass(frozen=True)
class TextElement:
    kind: Literal["text"]
    text: str
    target_rect_px: tuple[float, float, float, float]
    pivot_px: tuple[float, float]
    rotation_deg: float
    font_size_pt: float
    baseline_offset_px: float
    line_height_px: float
    bold: bool
    color_rgb: tuple[float, float, float]


PlayboardElement = Union[SourceClipElement, RasterPatchElement, TextElement]


@dataclass
class PlayboardComposition:
    preview_image: QImage
    size_px: tuple[float, float]
    elements: list[PlayboardElement]


def render_first_page(pdf_path: Path, *, dpi: int = DEFAULT_RENDER_DPI) -> RenderedPagePreview:
    doc = fitz.open(str(pdf_path))
    try:
        if len(doc) <= 0:
            raise ValueError("Source PDF has no pages.")
        page = doc[0]
        pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csRGB)
        image = QImage.fromData(pix.tobytes("png"), "PNG")
        if image.isNull():
            raise RuntimeError(f"Failed to render {pdf_path.name}.")
        return RenderedPagePreview(
            image=image,
            page_rect_pts=tuple(page.rect),
            page_index=0,
        )
    finally:
        doc.close()


def image_to_png_bytes(image: QImage) -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray

    byte_array = QByteArray()
    buffer = QBuffer(byte_array)
    buffer.open(QBuffer.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(byte_array)


def qrectf_to_tuple(rect: QRectF) -> tuple[float, float, float, float]:
    return (float(rect.x()), float(rect.y()), float(rect.width()), float(rect.height()))


def tuple_to_qrectf(values: tuple[float, float, float, float]) -> QRectF:
    x, y, width, height = values
    return QRectF(float(x), float(y), float(width), float(height))


def scene_px_rect_to_pdf_rect(rect: QRectF, *, render_dpi: int) -> fitz.Rect:
    scale = 72.0 / float(render_dpi)
    return fitz.Rect(
        rect.x() * scale,
        rect.y() * scale,
        (rect.x() + rect.width()) * scale,
        (rect.y() + rect.height()) * scale,
    )


def scene_px_point_to_pdf_point(point: tuple[float, float], *, render_dpi: int) -> fitz.Point:
    scale = 72.0 / float(render_dpi)
    return fitz.Point(point[0] * scale, point[1] * scale)


def current_text_layout_dpi() -> float:
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return 96.0
    dpi = float(screen.logicalDotsPerInchY())
    return dpi if dpi > 0.0 else 96.0


def resolve_playboard_output_paths(source_pdf: Path) -> dict[str, Path]:
    source_pdf = source_pdf.expanduser().resolve()
    cropped_root = next((parent for parent in source_pdf.parents if parent.name.lower() == "cropped"), None)
    if cropped_root is None:
        cropped_root = source_pdf.parent / "cropped"
    playboard_root = cropped_root / "p-cropped"
    return {
        "cropped_root": cropped_root,
        "playboard_root": playboard_root,
        "output_pdf": playboard_root / "p-cropped" / source_pdf.name,
        "original_pdf": playboard_root / "original" / source_pdf.name,
    }


def _pdf_rect_from_tuple(values: tuple[float, float, float, float]) -> fitz.Rect:
    return fitz.Rect(values)


def _pdf_rotation_matrix(angle: float) -> fitz.Matrix:
    matrix = fitz.Matrix(1.0, 1.0)
    matrix.prerotate(float(angle))
    return matrix


def _write_text_element(page: fitz.Page, element: TextElement, *, render_dpi: int) -> None:
    target_rect_px = tuple_to_qrectf(element.target_rect_px)
    if target_rect_px.isEmpty() or element.text == "":
        return
    layout_dpi = current_text_layout_dpi()
    pdf_font_size = float(element.font_size_pt) * layout_dpi / float(render_dpi)
    fontname = "hebo" if element.bold else "helv"
    writer = fitz.TextWriter(page.rect, color=element.color_rgb)
    font = fitz.Font(fontname)
    baseline = scene_px_point_to_pdf_point(
        (target_rect_px.x(), target_rect_px.y() + element.baseline_offset_px),
        render_dpi=render_dpi,
    )
    line_step = float(element.line_height_px) * 72.0 / float(render_dpi)
    lines = element.text.splitlines() or [""]
    for index, line in enumerate(lines):
        writer.append(
            fitz.Point(baseline.x, baseline.y + line_step * index),
            line,
            font=font,
            fontsize=pdf_font_size,
        )
    morph = None
    if abs(element.rotation_deg) > 0.001:
        pivot = scene_px_point_to_pdf_point(element.pivot_px, render_dpi=render_dpi)
        morph = (pivot, _pdf_rotation_matrix(element.rotation_deg))
    writer.write_text(page, morph=morph, overlay=True)


def _create_patch_document(image_png: bytes, target_rect_px: tuple[float, float, float, float], *, render_dpi: int) -> fitz.Document:
    patch_rect = scene_px_rect_to_pdf_rect(tuple_to_qrectf(target_rect_px), render_dpi=render_dpi)
    patch_doc = fitz.open()
    patch_page = patch_doc.new_page(width=patch_rect.width, height=patch_rect.height)
    patch_page.insert_image(patch_page.rect, stream=image_png)
    return patch_doc


def save_playboard_pdf(
    source_pdf: Path,
    composition: PlayboardComposition,
    *,
    render_dpi: int = DEFAULT_RENDER_DPI,
) -> dict[str, Path]:
    source_pdf = source_pdf.expanduser().resolve()
    if composition.preview_image.isNull() or not composition.elements:
        raise ValueError("Nothing to save.")
    paths = resolve_playboard_output_paths(source_pdf)
    output_pdf = paths["output_pdf"]
    original_pdf = paths["original_pdf"]
    temp_pdf = output_pdf.with_name(f"{output_pdf.stem}.tmp{output_pdf.suffix}")

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    original_pdf.parent.mkdir(parents=True, exist_ok=True)

    output_doc = fitz.open()
    source_docs: dict[str, fitz.Document] = {}
    patch_docs: list[fitz.Document] = []
    try:
        page_width = float(composition.size_px[0]) * 72.0 / float(render_dpi)
        page_height = float(composition.size_px[1]) * 72.0 / float(render_dpi)
        page = output_doc.new_page(width=page_width, height=page_height)
        for element in composition.elements:
            if isinstance(element, SourceClipElement):
                src_doc = source_docs.get(element.source_pdf_path)
                if src_doc is None:
                    src_doc = fitz.open(element.source_pdf_path)
                    source_docs[element.source_pdf_path] = src_doc
                page.show_pdf_page(
                    scene_px_rect_to_pdf_rect(tuple_to_qrectf(element.target_rect_px), render_dpi=render_dpi),
                    src_doc,
                    element.page_index,
                    keep_proportion=False,
                    rotate=element.rotation_deg,
                    clip=_pdf_rect_from_tuple(element.clip_rect_pts),
                )
                continue
            if isinstance(element, TextElement):
                _write_text_element(page, element, render_dpi=render_dpi)
                continue
            patch_doc = _create_patch_document(element.image_png, element.target_rect_px, render_dpi=render_dpi)
            patch_docs.append(patch_doc)
            page.show_pdf_page(
                scene_px_rect_to_pdf_rect(tuple_to_qrectf(element.target_rect_px), render_dpi=render_dpi),
                patch_doc,
                0,
                keep_proportion=False,
                rotate=element.rotation_deg,
            )
        output_doc.save(str(temp_pdf), garbage=4, deflate=True)
    finally:
        for doc in patch_docs:
            doc.close()
        for doc in source_docs.values():
            doc.close()
        output_doc.close()

    if original_pdf.exists():
        original_pdf.unlink()
    if not source_pdf.exists():
        raise FileNotFoundError(f"Source PDF does not exist: {source_pdf}")
    os.replace(str(source_pdf), str(original_pdf))
    os.replace(str(temp_pdf), str(output_pdf))
    return paths


class PlayboardTextItem(QGraphicsTextItem):
    def __init__(self, owner_view: "PlayboardView", text: str, point_size: int, bold: bool) -> None:
        super().__init__(text)
        self.owner_view = owner_view
        self.setFlags(
            QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemIsFocusable
            | QGraphicsItem.ItemSendsGeometryChanges
        )
        self.setTextInteractionFlags(Qt.TextEditorInteraction)
        self.setDefaultTextColor(QColor(22, 22, 22))
        self.apply_style(point_size, bold)

    def apply_style(self, point_size: int, bold: bool) -> None:
        cursor = self.textCursor()
        cursor.select(QTextCursor.Document)
        fmt = cursor.charFormat()
        fmt.setFontPointSize(float(point_size))
        fmt.setFontWeight(700 if bold else 400)
        cursor.mergeCharFormat(fmt)
        cursor.clearSelection()
        self.setTextCursor(cursor)
        font = self.font()
        font.setPointSize(point_size)
        font.setBold(bold)
        self.setFont(font)

    def paint(self, painter, option, widget=None) -> None:
        styled_option = QStyleOptionGraphicsItem(option)
        styled_option.state &= ~QStyle.State_Selected
        styled_option.state &= ~QStyle.State_HasFocus
        super().paint(painter, styled_option, widget)

    def itemChange(self, change, value):
        result = super().itemChange(change, value)
        if change in {
            QGraphicsItem.ItemSelectedHasChanged,
            QGraphicsItem.ItemPositionHasChanged,
            QGraphicsItem.ItemRotationHasChanged,
            QGraphicsItem.ItemTransformHasChanged,
        }:
            self.owner_view.refresh_selected_outline()
        return result


class PlayboardPixmapItem(QGraphicsPixmapItem):
    def __init__(
        self,
        owner_view: "PlayboardView",
        pixmap: QPixmap,
        *,
        source_clip: SourceClipMetadata | None = None,
    ) -> None:
        super().__init__(pixmap)
        self.owner_view = owner_view
        self.source_clip = source_clip
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)

    def paint(self, painter, option, widget=None) -> None:
        styled_option = QStyleOptionGraphicsItem(option)
        styled_option.state &= ~QStyle.State_Selected
        styled_option.state &= ~QStyle.State_HasFocus
        super().paint(painter, styled_option, widget)

    def itemChange(self, change, value):
        result = super().itemChange(change, value)
        if change in {
            QGraphicsItem.ItemSelectedHasChanged,
            QGraphicsItem.ItemPositionHasChanged,
            QGraphicsItem.ItemRotationHasChanged,
            QGraphicsItem.ItemTransformHasChanged,
        }:
            self.owner_view.refresh_selected_outline()
        return result


class PdfDropGraphicsView(QGraphicsView):
    pdfDropped = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:
        if self._pdf_paths(event.mimeData()):
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event) -> None:
        if self._pdf_paths(event.mimeData()):
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event) -> None:
        paths = self._pdf_paths(event.mimeData())
        if not paths:
            event.ignore()
            return
        self.pdfDropped.emit(paths)
        event.acceptProposedAction()

    @staticmethod
    def _pdf_paths(mime_data) -> list[str]:
        if not mime_data.hasUrls():
            return []
        out: list[str] = []
        for url in mime_data.urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile()).expanduser()
            if path.suffix.lower() == ".pdf":
                out.append(str(path))
        return out


class PlayboardView(PdfDropGraphicsView):
    selectionStateChanged = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.board_scene = QGraphicsScene(self)
        self.setScene(self.board_scene)
        self.setRenderHint(QPainter.Antialiasing, True)
        self.setRenderHint(QPainter.SmoothPixmapTransform, True)
        self.setFrameShape(QFrame.NoFrame)
        self.setBackgroundBrush(QColor(244, 244, 244))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)

        self.tool_mode = "select"
        self.text_point_size = 28
        self.text_bold = False
        self.last_scene_pos = QPointF(BOARD_MARGIN, BOARD_MARGIN)
        self.rubber_origin: QPointF | None = None
        self.selection_rect_item: QGraphicsRectItem | None = None
        self.selection_scene_rect: QRectF | None = None
        self.selected_outline_item: QGraphicsRectItem | None = None
        self.board_rect_item: QGraphicsRectItem | None = None
        self.board_pixmap_item: QGraphicsPixmapItem | None = None
        self.board_image = QImage()
        self.source_pdf_path = ""
        self.source_page_index = 0
        self.source_page_rect_pts: tuple[float, float, float, float] | None = None
        self.source_image_scene_rect: QRectF | None = None
        self.board_has_raster_edits = False
        self.board_scene.selectionChanged.connect(self._emit_selection_state)
        self.resetTransform()
        self._build_empty_scene()

    def event(self, event) -> bool:
        if event.type() == QEvent.NativeGesture and event.gestureType() == Qt.ZoomNativeGesture:
            factor = max(0.2, 1.0 + float(event.value()))
            self.scale(factor, factor)
            event.accept()
            return True
        return super().event(event)

    def _build_empty_scene(self) -> None:
        self.board_scene.clear()
        self.selection_rect_item = None
        self.selection_scene_rect = None
        self.selected_outline_item = None
        self.source_pdf_path = ""
        self.source_page_index = 0
        self.source_page_rect_pts = None
        self.source_image_scene_rect = None
        self.board_has_raster_edits = False
        empty_board = QImage(EMPTY_BOARD_SIZE, QImage.Format_RGB32)
        empty_board.fill(Qt.white)
        self._load_board_image(empty_board)
        self.viewport().update()
        self.selectionStateChanged.emit(False)

    def _emit_selection_state(self) -> None:
        self.refresh_selected_outline()
        self.selectionStateChanged.emit(self.current_selection_rect() is not None)

    def selected_content_items(self) -> list[QGraphicsItem]:
        return [item for item in self.board_scene.selectedItems() if item.data(DATA_KIND) == "content"]

    def clear_board(self) -> None:
        self.resetTransform()
        self.last_scene_pos = QPointF(BOARD_MARGIN, BOARD_MARGIN)
        self._build_empty_scene()

    def set_tool_mode(self, mode: str) -> None:
        self.tool_mode = mode

    def set_text_style(self, point_size: int, bold: bool) -> None:
        self.text_point_size = int(point_size)
        self.text_bold = bool(bold)
        for item in self.board_scene.selectedItems():
            if isinstance(item, PlayboardTextItem):
                item.apply_style(self.text_point_size, self.text_bold)
                self._sync_item_transform_origin(item)
        self.refresh_selected_outline()

    def set_board_image(
        self,
        image: QImage,
        *,
        source_pdf_path: str = "",
        source_page_index: int = 0,
        source_page_rect_pts: tuple[float, float, float, float] | None = None,
    ) -> None:
        self.resetTransform()
        self.board_scene.clear()
        self.selection_rect_item = None
        self.selection_scene_rect = None
        self.selected_outline_item = None
        board_width = max(image.width() + BOARD_MARGIN * 2, int(image.width() * 2.8))
        board_height = max(image.height() + BOARD_MARGIN * 2, int(image.height() * 2.5))
        board_image = QImage(board_width, board_height, QImage.Format_RGB32)
        board_image.fill(Qt.white)
        painter = QPainter(board_image)
        painter.drawImage(BOARD_MARGIN, BOARD_MARGIN, image)
        painter.end()
        self._load_board_image(board_image)
        self.last_scene_pos = QPointF(float(BOARD_MARGIN), float(BOARD_MARGIN))
        self.source_pdf_path = source_pdf_path
        self.source_page_index = int(source_page_index)
        self.source_page_rect_pts = source_page_rect_pts
        self.source_image_scene_rect = QRectF(float(BOARD_MARGIN), float(BOARD_MARGIN), float(image.width()), float(image.height()))
        self.board_has_raster_edits = False
        self.selectionStateChanged.emit(False)
        self.centerOn(self.board_pixmap_item)

    def _load_board_image(self, board_image: QImage) -> None:
        self.board_image = board_image
        board_rect = QRectF(0.0, 0.0, float(board_image.width()), float(board_image.height()))
        self.board_rect_item = self.board_scene.addRect(board_rect, QPen(Qt.NoPen), QColor(Qt.white))
        self.board_rect_item.setZValue(-100)
        self.board_rect_item.setData(DATA_KIND, "board")
        self.board_pixmap_item = self.board_scene.addPixmap(QPixmap.fromImage(board_image))
        self.board_pixmap_item.setZValue(-50)
        self.board_pixmap_item.setData(DATA_KIND, "board-image")
        self.board_pixmap_item.setFlag(QGraphicsItem.ItemIsSelectable, False)
        self.board_pixmap_item.setFlag(QGraphicsItem.ItemIsMovable, False)
        self.board_scene.setSceneRect(board_rect)

    def zoom_in(self) -> None:
        self.scale(1.15, 1.15)

    def zoom_out(self) -> None:
        self.scale(1.0 / 1.15, 1.0 / 1.15)

    def reset_zoom(self) -> None:
        self.resetTransform()

    def _overlay_items(self) -> list[QGraphicsItem]:
        return [item for item in self.board_scene.items() if item.data(DATA_KIND) == "content"]

    def _committable_overlay_items(self) -> list[QGraphicsItem]:
        return [item for item in self._overlay_items() if isinstance(item, PlayboardPixmapItem)]

    @staticmethod
    def _sync_item_transform_origin(item: QGraphicsItem) -> None:
        if isinstance(item, (QGraphicsPixmapItem, QGraphicsTextItem)):
            item.setTransformOriginPoint(item.boundingRect().center())

    @staticmethod
    def _united_scene_rect(items: list[QGraphicsItem]) -> QRectF:
        rect = items[0].sceneBoundingRect()
        for item in items[1:]:
            rect = rect.united(item.sceneBoundingRect())
        return rect.normalized()

    def _ensure_selected_outline_item(self) -> None:
        if self.selected_outline_item is not None:
            return
        pen = QPen(SELECTION_OUTLINE_COLOR, 2)
        pen.setStyle(Qt.DashLine)
        pen.setCosmetic(True)
        self.selected_outline_item = self.board_scene.addRect(QRectF(), pen)
        self.selected_outline_item.setBrush(Qt.NoBrush)
        self.selected_outline_item.setZValue(250)
        self.selected_outline_item.setData(DATA_KIND, "selected-outline")
        self.selected_outline_item.setAcceptedMouseButtons(Qt.NoButton)

    def refresh_selected_outline(self) -> None:
        items = self.selected_content_items()
        if not items:
            if self.selected_outline_item is not None:
                self.board_scene.removeItem(self.selected_outline_item)
                self.selected_outline_item = None
            return
        self._ensure_selected_outline_item()
        self.selected_outline_item.setRect(self._united_scene_rect(items))
        self.selected_outline_item.show()

    def _refresh_board_pixmap(self) -> None:
        if self.board_pixmap_item is not None and not self.board_image.isNull():
            self.board_pixmap_item.setPixmap(QPixmap.fromImage(self.board_image))

    def _render_overlay_items(self, items: list[QGraphicsItem]) -> tuple[QImage, QRectF] | None:
        if not items:
            return None
        rect = self._united_scene_rect(items)
        width = max(1, int(round(rect.width())))
        height = max(1, int(round(rect.height())))
        image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)

        all_items = list(self.board_scene.items())
        visibility = {id(item): item.isVisible() for item in all_items}
        try:
            for item in all_items:
                item.setVisible(item in items)
            painter = QPainter(image)
            self.board_scene.render(painter, QRectF(0.0, 0.0, float(width), float(height)), rect)
            painter.end()
        finally:
            for item in all_items:
                item.setVisible(visibility[id(item)])
        return image, rect

    def _selected_content_render(self) -> tuple[list[QGraphicsItem], QImage] | None:
        items = self.selected_content_items()
        if not items:
            return None
        rendered = self._render_overlay_items(items)
        if rendered is None:
            return None
        image, _ = rendered
        return items, image

    def _commit_overlay_items(self) -> None:
        items = self._committable_overlay_items()
        if not items or self.board_image.isNull():
            return
        rendered = self._render_overlay_items(items)
        if rendered is None:
            return
        image, rect = rendered
        painter = QPainter(self.board_image)
        painter.drawImage(rect.topLeft(), image)
        painter.end()
        for item in items:
            self.board_scene.removeItem(item)
        self.board_has_raster_edits = True
        self._refresh_board_pixmap()
        self.refresh_selected_outline()

    def _source_clip_metadata_for_rect(self, rect: QRectF) -> SourceClipMetadata | None:
        if (
            self.board_has_raster_edits
            or not self.source_pdf_path
            or self.source_page_rect_pts is None
            or self.source_image_scene_rect is None
            or not self.source_image_scene_rect.contains(rect)
        ):
            return None
        source_rect = self.source_image_scene_rect
        page_rect = _pdf_rect_from_tuple(self.source_page_rect_pts)
        x_scale = page_rect.width / source_rect.width()
        y_scale = page_rect.height / source_rect.height()
        clip_rect = fitz.Rect(
            page_rect.x0 + (rect.x() - source_rect.x()) * x_scale,
            page_rect.y0 + (rect.y() - source_rect.y()) * y_scale,
            page_rect.x0 + (rect.x() + rect.width() - source_rect.x()) * x_scale,
            page_rect.y0 + (rect.y() + rect.height() - source_rect.y()) * y_scale,
        )
        return SourceClipMetadata(
            source_pdf_path=self.source_pdf_path,
            page_index=self.source_page_index,
            clip_rect_pts=tuple(clip_rect),
        )

    def _cut_board_rect_to_overlay(self, rect: QRectF) -> QGraphicsPixmapItem | None:
        rect = rect.normalized().intersected(self.board_scene.sceneRect())
        if rect.width() < 2 or rect.height() < 2 or self.board_image.isNull():
            return None
        x = max(0, int(rect.x()))
        y = max(0, int(rect.y()))
        width = max(1, int(round(rect.width())))
        height = max(1, int(round(rect.height())))
        image = self.board_image.copy(x, y, width, height)
        if image.isNull():
            return None
        source_clip = self._source_clip_metadata_for_rect(QRectF(float(x), float(y), float(width), float(height)))
        painter = QPainter(self.board_image)
        painter.fillRect(QRectF(float(x), float(y), float(width), float(height)), Qt.white)
        painter.end()
        self.board_has_raster_edits = True
        self._refresh_board_pixmap()

        item = PlayboardPixmapItem(self, QPixmap.fromImage(image), source_clip=source_clip)
        item.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable)
        item.setData(DATA_KIND, "content")
        item.setPos(float(x), float(y))
        item.setZValue(10)
        self._sync_item_transform_origin(item)
        self.board_scene.addItem(item)
        self.board_scene.clearSelection()
        item.setSelected(True)
        return item

    def copy_selection(self) -> QImage | None:
        selected = self._selected_content_render()
        if selected is None:
            return None
        _, image = selected
        QGuiApplication.clipboard().setImage(image)
        return image

    def cut_selection(self) -> QImage | None:
        selected = self._selected_content_render()
        if selected is None:
            return None
        items, image = selected
        for item in items:
            self.board_scene.removeItem(item)
        self.clear_selection_rect()
        self.selectionStateChanged.emit(False)
        QGuiApplication.clipboard().setImage(image)
        return image

    def paste_image(self, image: QImage | None = None) -> QGraphicsPixmapItem | None:
        clip = image if image is not None else QGuiApplication.clipboard().image()
        if clip.isNull():
            return None
        item = PlayboardPixmapItem(self, QPixmap.fromImage(clip))
        item.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable)
        item.setData(DATA_KIND, "content")
        item.setPos(self.last_scene_pos)
        item.setZValue(10)
        self._sync_item_transform_origin(item)
        self.board_scene.addItem(item)
        self.board_scene.clearSelection()
        item.setSelected(True)
        self.selectionStateChanged.emit(True)
        return item

    def _item_target_rect_px(self, item: QGraphicsItem, union_rect: QRectF) -> QRectF:
        local_rect = item.boundingRect()
        scene_pos = item.scenePos()
        return QRectF(
            float(scene_pos.x() - union_rect.x()),
            float(scene_pos.y() - union_rect.y()),
            float(local_rect.width()),
            float(local_rect.height()),
        )

    def _item_pivot_px(self, item: QGraphicsItem, union_rect: QRectF) -> tuple[float, float]:
        pivot = item.mapToScene(item.transformOriginPoint())
        return (float(pivot.x() - union_rect.x()), float(pivot.y() - union_rect.y()))

    def _build_text_element(self, item: PlayboardTextItem, union_rect: QRectF) -> TextElement:
        color = item.defaultTextColor()
        font = item.font()
        point_size = font.pointSizeF() if font.pointSizeF() > 0 else float(font.pointSize())
        metrics = QFontMetricsF(font)
        if point_size <= 0:
            point_size = metrics.height() * 72.0 / current_text_layout_dpi()
        return TextElement(
            kind="text",
            text=item.toPlainText(),
            target_rect_px=qrectf_to_tuple(self._item_target_rect_px(item, union_rect)),
            pivot_px=self._item_pivot_px(item, union_rect),
            rotation_deg=float(item.rotation()),
            font_size_pt=float(point_size),
            baseline_offset_px=float(metrics.ascent()),
            line_height_px=float(metrics.lineSpacing()),
            bold=bool(font.bold() or font.weight() >= 700),
            color_rgb=(float(color.redF()), float(color.greenF()), float(color.blueF())),
        )

    def _build_selection_composition(self, items: list[QGraphicsItem]) -> PlayboardComposition | None:
        rendered = self._render_overlay_items(items)
        if rendered is None:
            return None
        preview_image, union_rect = rendered
        elements: list[PlayboardElement] = []
        for item in items:
            if isinstance(item, PlayboardPixmapItem):
                target_rect_px = qrectf_to_tuple(self._item_target_rect_px(item, union_rect))
                if item.source_clip is not None:
                    elements.append(
                        SourceClipElement(
                            kind="source-clip",
                            source_pdf_path=item.source_clip.source_pdf_path,
                            page_index=item.source_clip.page_index,
                            clip_rect_pts=item.source_clip.clip_rect_pts,
                            target_rect_px=target_rect_px,
                            rotation_deg=float(item.rotation()),
                        )
                    )
                    continue
                elements.append(
                    RasterPatchElement(
                        kind="raster-patch",
                        image_png=image_to_png_bytes(item.pixmap().toImage()),
                        target_rect_px=target_rect_px,
                        rotation_deg=float(item.rotation()),
                    )
                )
                continue
            if isinstance(item, PlayboardTextItem):
                elements.append(self._build_text_element(item, union_rect))
        return PlayboardComposition(
            preview_image=preview_image,
            size_px=(float(union_rect.width()), float(union_rect.height())),
            elements=elements,
        )

    def confirm_selection(self) -> PlayboardComposition | None:
        items = self.selected_content_items()
        if not items:
            return None
        composition = self._build_selection_composition(items)
        if composition is None:
            return None
        for item in items:
            self.board_scene.removeItem(item)
        self.clear_selection_rect()
        self.selectionStateChanged.emit(False)
        return composition

    def current_selection_rect(self) -> QRectF | None:
        if self.selection_scene_rect is not None and not self.selection_scene_rect.isNull():
            rect = self.selection_scene_rect.normalized().intersected(self.board_scene.sceneRect())
            if rect.width() >= 2 and rect.height() >= 2:
                return rect
        selected_items = [item for item in self.board_scene.selectedItems() if item.data(DATA_KIND) == "content"]
        if not selected_items:
            return None
        return self._united_scene_rect(selected_items)

    def clear_selection_rect(self) -> None:
        self.selection_scene_rect = None
        if self.selection_rect_item is not None:
            self.board_scene.removeItem(self.selection_rect_item)
            self.selection_rect_item = None

    def selected_rotation(self) -> float | None:
        items = self.selected_content_items()
        if not items:
            return None
        return float(items[0].rotation())

    def set_selected_rotation(self, angle: float) -> None:
        for item in self.selected_content_items():
            self._sync_item_transform_origin(item)
            item.setRotation(float(angle))
        self.viewport().update()

    def _ensure_selection_item(self) -> None:
        if self.selection_rect_item is not None:
            return
        pen = QPen(QColor(170, 170, 170), 2)
        pen.setStyle(Qt.DashLine)
        self.selection_rect_item = self.board_scene.addRect(QRectF(), pen)
        self.selection_rect_item.setBrush(Qt.NoBrush)
        self.selection_rect_item.setZValue(200)
        self.selection_rect_item.setData(DATA_KIND, "selection")
        self.selection_rect_item.setAcceptedMouseButtons(Qt.NoButton)

    def _top_content_item_at(self, point) -> object | None:
        for item in self.items(point):
            if item.data(DATA_KIND) == "content":
                return item
        return None

    def _add_text_item(self, scene_pos: QPointF) -> None:
        text_item = PlayboardTextItem(self, "Text", self.text_point_size, self.text_bold)
        text_item.setPos(scene_pos)
        text_item.setZValue(20)
        text_item.setData(DATA_KIND, "content")
        self._sync_item_transform_origin(text_item)
        self.board_scene.addItem(text_item)
        self.board_scene.clearSelection()
        text_item.setSelected(True)
        text_item.setFocus(Qt.MouseFocusReason)
        cursor = text_item.textCursor()
        cursor.select(QTextCursor.Document)
        text_item.setTextCursor(cursor)
        self.selectionStateChanged.emit(True)

    def wheelEvent(self, event) -> None:
        if event.modifiers() & (Qt.ControlModifier | Qt.MetaModifier):
            if event.angleDelta().y() > 0:
                self.zoom_in()
            else:
                self.zoom_out()
            event.accept()
            return
        super().wheelEvent(event)

    def mousePressEvent(self, event) -> None:
        self.last_scene_pos = self.mapToScene(event.position().toPoint())
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        content_item = self._top_content_item_at(event.position().toPoint())
        if self.tool_mode == "text":
            if content_item is not None:
                self.clear_selection_rect()
                super().mousePressEvent(event)
                return
            self._add_text_item(self.last_scene_pos)
            event.accept()
            return
        if content_item is not None:
            self.clear_selection_rect()
            super().mousePressEvent(event)
            return
        self._commit_overlay_items()
        self.board_scene.clearSelection()
        self.rubber_origin = self.last_scene_pos
        self._ensure_selection_item()
        self.selection_rect_item.setRect(QRectF(self.rubber_origin, self.rubber_origin))
        self.selection_scene_rect = QRectF(self.rubber_origin, self.rubber_origin)
        self._emit_selection_state()
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        self.last_scene_pos = self.mapToScene(event.position().toPoint())
        if self.rubber_origin is not None and self.selection_rect_item is not None:
            rect = QRectF(self.rubber_origin, self.last_scene_pos).normalized()
            self.selection_rect_item.setRect(rect)
            self.selection_scene_rect = rect
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.rubber_origin is not None:
            self.rubber_origin = None
            rect = self.selection_scene_rect.normalized() if self.selection_scene_rect is not None else QRectF()
            if rect.width() < 2 or rect.height() < 2:
                self.clear_selection_rect()
                self._emit_selection_state()
            else:
                self._cut_board_rect_to_overlay(rect)
                self.clear_selection_rect()
                self._emit_selection_state()
            event.accept()
            return
        super().mouseReleaseEvent(event)
        self._emit_selection_state()


class ResultView(QGraphicsView):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.result_scene = QGraphicsScene(self)
        self.setScene(self.result_scene)
        self.setRenderHint(QPainter.Antialiasing, True)
        self.setRenderHint(QPainter.SmoothPixmapTransform, True)
        self.setFrameShape(QFrame.NoFrame)
        self.setBackgroundBrush(QColor(244, 244, 244))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.current_image = QImage()
        self.clear_result()

    def clear_result(self) -> None:
        self.result_scene.clear()
        self.current_image = QImage()
        rect = QRectF(0.0, 0.0, float(EMPTY_RESULT_SIZE.width()), float(EMPTY_RESULT_SIZE.height()))
        self.result_scene.addRect(rect, QPen(Qt.NoPen), QColor(Qt.white)).setZValue(-100)
        self.result_scene.setSceneRect(rect)

    def set_result_image(self, image: QImage | None) -> None:
        self.result_scene.clear()
        if image is None or image.isNull():
            self.clear_result()
            return
        self.current_image = QImage(image)
        rect = QRectF(0.0, 0.0, float(image.width()), float(image.height()))
        self.result_scene.addRect(rect, QPen(Qt.NoPen), QColor(Qt.white)).setZValue(-100)
        item = self.result_scene.addPixmap(QPixmap.fromImage(image))
        item.setPos(0.0, 0.0)
        self.result_scene.setSceneRect(rect)
        self.fitInView(rect, Qt.KeepAspectRatio)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not self.current_image.isNull():
            self.fitInView(self.result_scene.sceneRect(), Qt.KeepAspectRatio)


class PlayboardPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.source_pdf_path = ""
        self.clipboard_image = QImage()
        self.current_result_composition: PlayboardComposition | None = None

        self.save_button = QPushButton("SAVE")
        self.path_label = QLabel("")

        self.select_button = QPushButton("SELECT")
        self.text_button = QPushButton("TEXT")
        self.confirm_button = QPushButton("CONFIRM")
        self.text_size_input = QSpinBox()
        self.text_bold_input = QCheckBox("BOLD")
        self.rotate_input = QDoubleSpinBox()

        self.top_view = PlayboardView()
        self.bottom_view = ResultView()

        self.select_button.setCheckable(True)
        self.text_button.setCheckable(True)

        self._build_ui()
        self._wire_actions()
        self._wire_shortcuts()
        self._set_tool_mode("select")
        self._sync_text_style()
        self._refresh_actions()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        head_row = QHBoxLayout()
        head_row.setContentsMargins(0, 0, 0, 0)
        head_row.setSpacing(8)
        head_row.addWidget(self.save_button)
        head_row.addWidget(self.path_label, 1)
        root.addLayout(head_row)

        top_panel = QWidget()
        top_layout = QVBoxLayout(top_panel)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(8)

        tool_row = QHBoxLayout()
        tool_row.setContentsMargins(0, 0, 0, 0)
        tool_row.setSpacing(8)
        self.text_size_input.setRange(8, 144)
        self.text_size_input.setValue(28)
        self.rotate_input.setRange(-360.0, 360.0)
        self.rotate_input.setDecimals(1)
        self.rotate_input.setSingleStep(1.0)
        self.rotate_input.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.rotate_input.setFixedWidth(96)
        tool_row.addWidget(self.select_button)
        tool_row.addWidget(self.text_button)
        tool_row.addWidget(self.confirm_button)
        tool_row.addWidget(self.text_size_input)
        tool_row.addWidget(self.text_bold_input)
        tool_row.addWidget(self.rotate_input)
        tool_row.addStretch(1)
        top_layout.addLayout(tool_row)
        top_layout.addWidget(self.top_view, 1)

        bottom_panel = QWidget()
        bottom_layout = QVBoxLayout(bottom_panel)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(8)
        bottom_layout.addWidget(self.bottom_view, 1)

        root.addWidget(top_panel, 3)
        root.addWidget(bottom_panel, 2)

    def _wire_actions(self) -> None:
        self.save_button.clicked.connect(self.save_result)
        self.select_button.clicked.connect(lambda: self._set_tool_mode("select"))
        self.text_button.clicked.connect(lambda: self._set_tool_mode("text"))
        self.confirm_button.clicked.connect(self.confirm_selection)
        self.text_size_input.valueChanged.connect(self._sync_text_style)
        self.text_bold_input.toggled.connect(self._sync_text_style)
        self.rotate_input.valueChanged.connect(self._sync_rotation)
        self.top_view.pdfDropped.connect(lambda paths: paths and self.load_pdf(Path(paths[0])))
        self.top_view.selectionStateChanged.connect(lambda _value: self._refresh_actions())

    def _wire_shortcuts(self) -> None:
        for shortcut, callback in (
            (QKeySequence.Copy, self.copy_selection),
            (QKeySequence.Cut, self.cut_selection),
            (QKeySequence.Paste, self.paste_selection),
            (QKeySequence.ZoomIn, self.top_view.zoom_in),
            (QKeySequence.ZoomOut, self.top_view.zoom_out),
            ("Ctrl+0", self.top_view.reset_zoom),
        ):
            action = QAction(self)
            action.setShortcut(shortcut)
            action.triggered.connect(callback)
            self.addAction(action)

    def _set_tool_mode(self, mode: str) -> None:
        self.top_view.set_tool_mode(mode)
        self.select_button.setChecked(mode == "select")
        self.text_button.setChecked(mode == "text")

    def _show_warning(self, message: str) -> None:
        QMessageBox.warning(self, APP_NAME, message)

    def _show_error(self, exc: Exception) -> None:
        QMessageBox.critical(self, APP_NAME, str(exc))

    def _capture_selection_image(self, getter) -> None:
        image = getter()
        if image is not None:
            self.clipboard_image = image
        self._refresh_actions()

    def _sync_text_style(self) -> None:
        self.top_view.set_text_style(self.text_size_input.value(), self.text_bold_input.isChecked())

    def _sync_rotation(self) -> None:
        self.top_view.set_selected_rotation(self.rotate_input.value())

    def _refresh_actions(self) -> None:
        has_source = bool(self.source_pdf_path)
        has_selection = self.top_view.current_selection_rect() is not None
        has_selected_content = bool(self.top_view.selected_content_items())
        has_clipboard = not self.clipboard_image.isNull() or not QGuiApplication.clipboard().image().isNull()
        has_result = self.current_result_composition is not None and not self.bottom_view.current_image.isNull()

        self.select_button.setEnabled(has_source)
        self.text_button.setEnabled(has_source)
        self.confirm_button.setEnabled(has_selection)
        self.text_size_input.setEnabled(has_source)
        self.text_bold_input.setEnabled(has_source)
        self.rotate_input.setEnabled(has_selected_content)
        rotation = self.top_view.selected_rotation()
        self.rotate_input.blockSignals(True)
        self.rotate_input.setValue(rotation if rotation is not None else 0.0)
        self.rotate_input.blockSignals(False)
        self.save_button.setEnabled(has_result and has_source)
        self.path_label.setText(self.source_pdf_path or "")
        self.top_view.setEnabled(True)
        if has_source and has_clipboard:
            self.top_view.viewport().setToolTip("Copy/Cut/Paste: Cmd/Ctrl+C, X, V. Zoom: Cmd/Ctrl+wheel or +/-")
        else:
            self.top_view.viewport().setToolTip("")

    def load_pdf(self, pdf_path: Path) -> None:
        try:
            preview = render_first_page(pdf_path)
        except Exception as exc:
            self._show_error(exc)
            return
        self.source_pdf_path = str(pdf_path.expanduser().resolve())
        self.top_view.set_board_image(
            preview.image,
            source_pdf_path=self.source_pdf_path,
            source_page_index=preview.page_index,
            source_page_rect_pts=preview.page_rect_pts,
        )
        self.bottom_view.clear_result()
        self.clipboard_image = QImage()
        self.current_result_composition = None
        self._set_tool_mode("select")
        self._refresh_actions()

    def copy_selection(self) -> None:
        self._capture_selection_image(self.top_view.copy_selection)

    def cut_selection(self) -> None:
        self._capture_selection_image(self.top_view.cut_selection)

    def paste_selection(self) -> None:
        if not self.source_pdf_path:
            return
        item = self.top_view.paste_image(self.clipboard_image if not self.clipboard_image.isNull() else None)
        if item is None:
            self._show_warning("Nothing to paste.")
        self._refresh_actions()

    def confirm_selection(self) -> None:
        composition = self.top_view.confirm_selection()
        if composition is None or composition.preview_image.isNull():
            self._show_warning("Select an area first.")
            return
        self.current_result_composition = composition
        self.bottom_view.set_result_image(composition.preview_image)
        self._refresh_actions()

    def save_result(self) -> None:
        if not self.source_pdf_path:
            self._show_warning("Open a PDF first.")
            return
        if self.current_result_composition is None or self.bottom_view.current_image.isNull():
            self._show_warning("Confirm an area first.")
            return
        try:
            output_paths = save_playboard_pdf(Path(self.source_pdf_path), self.current_result_composition)
        except Exception as exc:
            self._show_error(exc)
            return
        self.path_label.setText(str(output_paths["output_pdf"]))
        self.source_pdf_path = ""
        self.clipboard_image = QImage()
        self.current_result_composition = None
        self.top_view.clear_board()
        self.bottom_view.clear_result()
        self._refresh_actions()


class PlayboardWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1320, 920)
        self.panel = PlayboardPanel(self)
        self.setCentralWidget(self.panel)


def main() -> None:
    app = QApplication(sys.argv)
    window = PlayboardWindow()
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
