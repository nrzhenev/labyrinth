"""SVG-представление через стандартный вывод Jupyter, без GUI-виджетов."""

from collections.abc import Callable
from dataclasses import dataclass, field
from html import escape
from threading import Timer
from uuid import uuid4

from IPython.display import SVG, display

from labyrinth import Game, Location, ObjectPlacement, PlaceObject, TrailLine, WallHitEvent


@dataclass(slots=True)
class RenderContext:
    placements: tuple[ObjectPlacement, ...]
    prefix: str
    trails: dict[tuple[int, int], TrailLine] = field(init=False)
    _index: int = 0

    def __post_init__(self):
        self.trails = {(id(item.object.owner), item.object.sequence): item.object
                       for item in self.placements if isinstance(item.object, TrailLine)}

    def gradient_id(self) -> str:
        self._index += 1
        return f"{self.prefix}-trail-{self._index}"


Renderer = Callable[["MazeView", PlaceObject, Location, RenderContext], str]


def render_trail_line(view: "MazeView", obj: TrailLine,
                      location: Location, context: RenderContext) -> str:
    if not view.show_trail:
        return ""
    following = context.trails.get((id(obj.owner), obj.sequence + 1))
    end_color = following.color if following is not None and following.start == obj.end else obj.color
    start_color, end_color = escape(obj.color, quote=True), escape(end_color, quote=True)
    start = view._center(obj.start)
    end = view._center(obj.end)
    dx, dy = obj.direction.delta
    virtual_end = (start[0] + dx * view.cell_size, start[1] + dy * view.cell_size)

    def segment(a, b, gradient_start, gradient_end):
        gradient_id = context.gradient_id()
        return (
            f'<defs><linearGradient id="{gradient_id}" gradientUnits="userSpaceOnUse" '
            f'x1="{gradient_start[0]}" y1="{gradient_start[1]}" '
            f'x2="{gradient_end[0]}" y2="{gradient_end[1]}">'
            f'<stop offset="0%" stop-color="{start_color}"/>'
            f'<stop offset="100%" stop-color="{end_color}"/>'
            '</linearGradient></defs>'
            f'<polyline points="{a[0]},{a[1]} {b[0]},{b[1]}" fill="none" '
            f'stroke="url(#{gradient_id})" stroke-width="3" '
            'stroke-linecap="round" stroke-linejoin="round"/>'
        )

    if end == virtual_end:
        return segment(start, end, start, end)
    # На шве тора показываем половины перехода на двух противоположных краях.
    border = ((start[0] + virtual_end[0]) / 2, (start[1] + virtual_end[1]) / 2)
    shift = (end[0] - virtual_end[0], end[1] - virtual_end[1])
    opposite = (border[0] + shift[0], border[1] + shift[1])
    shifted_start = (start[0] + shift[0], start[1] + shift[1])
    return (segment(start, border, start, virtual_end)
            + segment(opposite, end, shifted_start, end))


class MazeView:
    def __init__(
        self, game: Game, *,
        cell_size: int = 48, show_trail: bool = True, show_grid: bool = True,
    ):
        if type(cell_size) is not int or cell_size < 24:
            raise ValueError("cell_size должен быть целым числом не меньше 24")
        self.game = game
        self.grid = game.grid
        self.mouse = game.mouse
        self.cell_size = cell_size
        self.margin = 12
        self._show_trail = bool(show_trail)
        self._show_grid = bool(show_grid)
        self._renderers: dict[type[PlaceObject], Renderer] = {TrailLine: render_trail_line}
        self._svg_prefix = f"grid-{uuid4().hex}"
        self._handles = []
        self._message: str | None = None
        self._message_timer: Timer | None = None
        self._unsubscribers = [
            self.grid.subscribe(self._on_change),
            game.subscribe(self._on_change),
            game.subscribe_event(self._on_event),
        ]

    def _on_change(self):
        if self._message_timer is not None:
            self._message_timer.cancel()
            self._message_timer = None
        self._message = None
        self.redraw()

    def _on_event(self, event):
        if isinstance(event, WallHitEvent):
            if self._message_timer is not None:
                self._message_timer.cancel()
            self._message = "Вы уперлись в стену"
            self.redraw()
            self._message_timer = Timer(3, self._hide_message)
            self._message_timer.daemon = True
            self._message_timer.start()

    def _hide_message(self):
        self._message = None
        self._message_timer = None
        self.redraw()

    def register_renderer(self, object_type: type[PlaceObject], renderer: Renderer):
        if not issubclass(object_type, PlaceObject) or not callable(renderer):
            raise TypeError("Нужен тип PlaceObject и вызываемый renderer")
        self._renderers[object_type] = renderer
        self.redraw()

    def _renderer(self, obj: PlaceObject) -> Renderer | None:
        return next((self._renderers[cls] for cls in type(obj).__mro__
                     if cls in self._renderers), None)

    @property
    def show_trail(self) -> bool:
        return self._show_trail

    @show_trail.setter
    def show_trail(self, value: bool):
        self._show_trail = bool(value)
        self.redraw()

    @property
    def show_grid(self) -> bool:
        return self._show_grid

    @show_grid.setter
    def show_grid(self, value: bool):
        self._show_grid = bool(value)
        self.redraw()

    def _center(self, cell):
        return tuple(self.margin + (coordinate + 0.5) * self.cell_size for coordinate in cell)

    def to_svg(self) -> str:
        """Снимок состояния в SVG, пригодный также для сохранения в файл."""
        s, m = self.cell_size, self.margin
        width, height = self.grid.width * s + 2 * m, self.grid.height * s + 2 * m
        parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
                 f'height="{height}" viewBox="0 0 {width} {height}">',
                 '<rect width="100%" height="100%" fill="white"/>']

        def line(x1, y1, x2, y2, color="#334155", thickness=3):
            parts.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
                         f'stroke="{color}" stroke-width="{thickness}"/>')

        def polyline(points, color, thickness):
            coordinates = " ".join(f"{x},{y}" for x, y in points)
            parts.append(f'<polyline points="{coordinates}" fill="none" '
                          f'stroke="{color}" stroke-width="{thickness}" '
                          'stroke-linecap="round" stroke-linejoin="round"/>')

        def circle(point, radius, color):
            x, y = point
            parts.append(f'<circle cx="{x}" cy="{y}" r="{radius}" fill="{color}"/>')

        placements = tuple(sorted(self.grid.placements,
                                  key=lambda item: (item.object.layer, item.order)))
        context = RenderContext(placements, self._svg_prefix)

        def render_layer(layer):
            for item in placements:
                if item.object.layer == layer:
                    renderer = self._renderer(item.object)
                    if renderer is not None:
                        parts.append(renderer(self, item.object, item.location, context))

        for cell, color in ((self.grid.entrance, "#bbf7d0"), (self.grid.exit, "#fed7aa")):
            parts.append(f'<rect x="{m + cell[0] * s}" y="{m + cell[1] * s}" '
                         f'width="{s}" height="{s}" fill="{color}"/>')
        render_layer(1)
        if self.show_grid:
            for x in range(self.grid.width + 1):
                line(m + x * s, m, m + x * s, m + self.grid.height * s, "#e2e8f0", 1)
            for y in range(self.grid.height + 1):
                line(m, m + y * s, m + self.grid.width * s, m + y * s, "#e2e8f0", 1)
        render_layer(2)
        for x, y in self.grid.positions:
            cell = self.grid.cell_at((x, y))
            if x + 1 < self.grid.width and cell.right.closed:
                line(m + (x + 1) * s, m + y * s, m + (x + 1) * s, m + (y + 1) * s)
            if y + 1 < self.grid.height and cell.down.closed:
                line(m + x * s, m + (y + 1) * s, m + (x + 1) * s, m + (y + 1) * s)
            # Стена на шве тора отмечается с обеих противоположных сторон поля.
            if x == 0 and cell.left.closed:
                line(m, m + y * s, m, m + (y + 1) * s)
            if x == self.grid.width - 1 and cell.right.closed:
                line(m + self.grid.width * s, m + y * s,
                     m + self.grid.width * s, m + (y + 1) * s)
            if y == 0 and cell.up.closed:
                line(m + x * s, m, m + (x + 1) * s, m)
            if y == self.grid.height - 1 and cell.down.closed:
                line(m + x * s, m + self.grid.height * s,
                     m + (x + 1) * s, m + self.grid.height * s)
        render_layer(3)

        x, y = self._center(self.mouse.position)
        dx, dy = (1, 0)
        if self.mouse.last_direction is not None:
            dx, dy = self.mouse.last_direction.delta

        def point(forward, sideways):
            return (x + s * (forward * dx - sideways * dy),
                    y + s * (forward * dy + sideways * dx))

        polyline([point(-0.16, 0), point(-0.32, 0.08), point(-0.38, 0)], "#64748b", 2)
        circle((x, y), s * 0.19, "#94a3b8")
        nose = " ".join(f"{px},{py}" for px, py in
                        [point(0.05, -0.14), point(0.3, 0), point(0.05, 0.14)])
        parts.append(f'<polygon points="{nose}" fill="#94a3b8"/>')
        for side in (-0.14, 0.14):
            circle(point(0, side), s * 0.075, "#f9a8d4")
        circle(point(0.29, 0), s * 0.035, "#f9a8d4")
        for side in (-0.06, 0.06):
            circle(point(0.13, side), s * 0.025, "#0f172a")

        message = "Игра окончена!" if self.game.finished else self._message
        if message is not None:
            panel_width = min(width - 2 * m, 6 * s)
            panel_height = min(height - 2 * m, 2.25 * s)
            panel_x = (width - panel_width) / 2
            panel_y = (height - panel_height) / 2
            if not self.game.finished:
                parts.append('<g>')
            parts.extend([
                f'<rect x="{m}" y="{m}" width="{width - 2 * m}" '
                f'height="{height - 2 * m}" fill="#0f172a" fill-opacity="0.45"/>',
                f'<rect x="{panel_x}" y="{panel_y}" width="{panel_width}" '
                f'height="{panel_height}" rx="12" fill="white" stroke="#fb923c" '
                'stroke-width="3"/>',
                f'<text x="{width / 2}" y="{height / 2 - 4}" text-anchor="middle" '
                'font-family="sans-serif" font-size="20" font-weight="700" '
                f'fill="#0f172a">{message}</text>',
                f'<text x="{width / 2}" y="{height / 2 + 22}" text-anchor="middle" '
                'font-family="sans-serif" font-size="13" fill="#475569">'
                + ('Мышь добралась до выхода' if self.game.finished else 'Игра начата заново')
                + '</text>',
            ])
            if not self.game.finished:
                parts.append('</g>')
        parts.append("</svg>")
        return "".join(parts)

    def _ipython_display_(self):
        """Поддерживает display(view) и view последней строкой ячейки."""
        handle = display(SVG(data=self.to_svg()), display_id=True)
        if handle is not None:
            self._handles.append(handle)

    def redraw(self):
        """Обновить все области Jupyter, в которых отображено представление."""
        if self._handles:
            image = SVG(data=self.to_svg())
            for handle in self._handles:
                handle.update(image)

    def close(self):
        """Отключить автообновление; уже показанное изображение остаётся."""
        if self._message_timer is not None:
            self._message_timer.cancel()
            self._message_timer = None
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        self._handles.clear()
