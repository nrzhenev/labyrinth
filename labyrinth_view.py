"""SVG-представление через стандартный вывод Jupyter, без GUI-виджетов."""

from IPython.display import SVG, display

from labyrinth import Maze, Mouse


class MazeView:
    def __init__(
        self, maze: Maze, mouse: Mouse | None = None, *,
        cell_size: int = 48, show_trail: bool = True, show_grid: bool = True,
    ):
        if type(cell_size) is not int or cell_size < 24:
            raise ValueError("cell_size должен быть целым числом не меньше 24")
        if mouse is not None and mouse.maze is not maze:
            raise ValueError("Мышка должна принадлежать этому лабиринту")
        self.maze = maze
        self.mouse = mouse if mouse is not None else Mouse(maze)
        self.cell_size = cell_size
        self.margin = 12
        self._show_trail = bool(show_trail)
        self._show_grid = bool(show_grid)
        self._handles = []
        self._unsubscribers = [maze.subscribe(self.redraw), self.mouse.subscribe(self.redraw)]

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
        width, height = self.maze.width * s + 2 * m, self.maze.height * s + 2 * m
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

        for cell, color in ((self.maze.entrance, "#bbf7d0"), (self.maze.exit, "#fed7aa")):
            parts.append(f'<rect x="{m + cell[0] * s}" y="{m + cell[1] * s}" '
                         f'width="{s}" height="{s}" fill="{color}"/>')
        if self.show_grid:
            for x in range(self.maze.width + 1):
                line(m + x * s, m, m + x * s, m + self.maze.height * s, "#e2e8f0", 1)
            for y in range(self.maze.height + 1):
                line(m, m + y * s, m + self.maze.width * s, m + y * s, "#e2e8f0", 1)
        for x, y in self.maze.positions:
            if x + 1 < self.maze.width and self.maze.has_wall((x, y), "right"):
                line(m + (x + 1) * s, m + y * s, m + (x + 1) * s, m + (y + 1) * s)
            if y + 1 < self.maze.height and self.maze.has_wall((x, y), "down"):
                line(m + x * s, m + (y + 1) * s, m + (x + 1) * s, m + (y + 1) * s)
            # Стена на шве тора отмечается с обеих противоположных сторон поля.
            if x == 0 and self.maze.has_wall((x, y), "left"):
                line(m, m + y * s, m, m + (y + 1) * s)
            if x == self.maze.width - 1 and self.maze.has_wall((x, y), "right"):
                line(m + self.maze.width * s, m + y * s,
                     m + self.maze.width * s, m + (y + 1) * s)
            if y == 0 and self.maze.has_wall((x, y), "up"):
                line(m + x * s, m, m + (x + 1) * s, m)
            if y == self.maze.height - 1 and self.maze.has_wall((x, y), "down"):
                line(m + x * s, m + self.maze.height * s,
                     m + (x + 1) * s, m + self.maze.height * s)
        if self.show_trail and len(self.mouse.trail) > 1:
            segment = [self.mouse.trail[0]]
            for cell in self.mouse.trail[1:]:
                previous = segment[-1]
                if abs(cell[0] - previous[0]) + abs(cell[1] - previous[1]) > 1:
                    if len(segment) > 1:
                        polyline([self._center(item) for item in segment], "#93c5fd", 3)
                    segment = [cell]
                else:
                    segment.append(cell)
            if len(segment) > 1:
                polyline([self._center(item) for item in segment], "#93c5fd", 3)

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
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        self._handles.clear()
